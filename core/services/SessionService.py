"""
SessionService
---------------
Database-backed server-side sessions, stored in a `sessions` table through
the same DatabaseConnection/QueryBuilder every other part of the framework
uses - works across mysql/postgres/sqlite, whichever DB_DRIVER is configured.
(mongodb is not supported here, same reasoning as BaseModel: this needs a
table/query-builder, not a document store.)

One SessionService instance covers one request: call start(request) to load
(or create) the session for that visitor, read/write data on it during the
request, then call save(response) at the end so the session is persisted and
the session cookie is attached to the response.

Expected table - create with SessionService.create_table(db), or via the
migration CLI (python console.py migrate), which discovers this table
through the Session model (core/models/Session.py, MySQL-flavored, same
convention as User.SCHEMA/File.SCHEMA):
    sessions
        id          VARCHAR(64) PRIMARY KEY   -- the session token
        data        TEXT                      -- JSON-encoded session data
        expires_at  TIMESTAMP
        created_at  TIMESTAMP
        updated_at  TIMESTAMP

Expected .env keys (all optional)
-----------------------------------
SESSION_LIFETIME      seconds, default: 7200 (2 hours)
SESSION_COOKIE_NAME   default: "session_id"

Usage
-----
    from core.database.DatabaseConnection import DatabaseConnection
    from core.services.SessionService import SessionService

    db = DatabaseConnection()
    SessionService.create_table(db)   # once, e.g. during setup/migrations

    session = SessionService(db).start(request)

    session.set("cart_id", 42)
    views = session.get("page_views", 0)
    session.set("page_views", views + 1)

    session.flash("status", "Saved!")          # readable on the NEXT request only
    message = session.get_flash("status")       # reads what was flashed on the PREVIOUS request

    session.save(response)   # writes the sessions row + Set-Cookie on the response

Login/logout flows should call session.regenerate() when privilege changes
(see AuthService) to avoid session fixation.
"""

import datetime
import json
import secrets
from typing import Any, Dict, Optional

from core.models.Session import Session
from core.services.EnvService import EnvService

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class SessionService:
    table_name = Session.table_name

    def __init__(
        self,
        db_connection,
        lifetime_seconds: Optional[int] = None,
        cookie_name: Optional[str] = None,
        env: Optional[EnvService] = None,
    ):
        """
        db_connection: a DatabaseConnection instance (mysql/postgres/sqlite).
        """
        self.db = db_connection
        env = env or EnvService()
        self.lifetime_seconds = lifetime_seconds or env.get_int("SESSION_LIFETIME", 7200)
        self.cookie_name = cookie_name or env.get("SESSION_COOKIE_NAME", "session_id")

        self.session_id: Optional[str] = None
        self.data: Dict[str, Any] = {}
        self.is_new = True
        self._exists_in_db = False

    # ------------------------------------------------------------------ #
    # schema - the sessions table itself is defined on the Session model
    # (core/models/Session.py), so the migration CLI can discover and run
    # it the same way as every other table; this just forwards for anyone
    # still calling SessionService.create_table(db) directly.
    # ------------------------------------------------------------------ #
    @classmethod
    def create_table(cls, db_connection, if_not_exists: bool = True):
        return Session.create_table(db_connection, if_not_exists)

    def _query(self):
        return self.db.connect().table(self.table_name)

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def start(self, request) -> "SessionService":
        """Loads the session named by the request's session cookie, or starts a fresh one."""
        cookie_value = request.cookies.get(self.cookie_name) if hasattr(request, "cookies") else None

        if cookie_value:
            row = self._query().where("id", cookie_value).first()
            if row is not None and not self._is_expired(row):
                self.session_id = cookie_value
                self.data = self._decode(row["data"])
                self.is_new = False
                self._exists_in_db = True
                return self
            if row is not None:
                self._query().where("id", cookie_value).delete()  # expired - clean it up

        self.session_id = self._generate_id()
        self.data = {}
        self.is_new = True
        self._exists_in_db = False
        return self

    def save(self, response) -> "SessionService":
        """Persists the session row and attaches the session cookie to the response."""
        # promote anything flash()-ed this request so it's readable on the NEXT request only,
        # and drop whatever was readable THIS request (get_flash already had its chance at it)
        self.data.pop("_flash", None)
        next_flash = self.data.pop("_flash_next", None)
        if next_flash:
            self.data["_flash"] = next_flash

        expires_at = self._future_timestamp()
        payload = self._encode(self.data)

        if self._exists_in_db:
            affected = self._query().where("id", self.session_id).update(
                {"data": payload, "expires_at": expires_at}
            )
            if affected == 0:
                # row was removed underneath us (e.g. an expiry sweep) - recreate it
                self._insert(payload, expires_at)
        else:
            self._insert(payload, expires_at)
            self._exists_in_db = True

        response.set_cookie(
            self.cookie_name,
            self.session_id,
            max_age=self.lifetime_seconds,
            path="/",
            httponly=True,
        )
        return self

    def _insert(self, payload: str, expires_at: str) -> None:
        self._query().insert({"id": self.session_id, "data": payload, "expires_at": expires_at})

    def regenerate(self) -> "SessionService":
        """Issues a new session id, dropping the old row. Call this on login/logout/privilege change."""
        if self.session_id and self._exists_in_db:
            self._query().where("id", self.session_id).delete()
        self.session_id = self._generate_id()
        self._exists_in_db = False
        return self

    def destroy(self) -> "SessionService":
        if self.session_id and self._exists_in_db:
            self._query().where("id", self.session_id).delete()
        self.data = {}
        self._exists_in_db = False
        return self

    # ------------------------------------------------------------------ #
    # data access
    # ------------------------------------------------------------------ #
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> "SessionService":
        self.data[key] = value
        return self

    def has(self, key: str) -> bool:
        return key in self.data

    def delete(self, key: str) -> "SessionService":
        self.data.pop(key, None)
        return self

    def all(self) -> Dict[str, Any]:
        return {k: v for k, v in self.data.items() if k not in ("_flash", "_flash_next")}

    # ------------------------------------------------------------------ #
    # flash data - available on the request AFTER the one that set it
    # ------------------------------------------------------------------ #
    def flash(self, key: str, value: Any) -> "SessionService":
        self.data.setdefault("_flash_next", {})[key] = value
        return self

    def get_flash(self, key: str, default: Any = None) -> Any:
        return self.data.get("_flash", {}).get(key, default)

    def has_flash(self, key: str) -> bool:
        return key in self.data.get("_flash", {})

    # ------------------------------------------------------------------ #
    # expiry / encoding helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_expired(row) -> bool:
        return SessionService._parse_timestamp(row["expires_at"]) < datetime.datetime.utcnow()

    def _future_timestamp(self) -> str:
        moment = datetime.datetime.utcnow() + datetime.timedelta(seconds=self.lifetime_seconds)
        return moment.strftime(TIMESTAMP_FORMAT)

    @staticmethod
    def _parse_timestamp(value) -> datetime.datetime:
        if isinstance(value, datetime.datetime):
            return value
        # drivers may include fractional seconds/timezone - keep just the "YYYY-MM-DD HH:MM:SS" part
        text = str(value).split(".")[0].replace("T", " ")
        return datetime.datetime.strptime(text, TIMESTAMP_FORMAT)

    @staticmethod
    def _encode(data: Dict[str, Any]) -> str:
        return json.dumps(data)

    @staticmethod
    def _decode(payload) -> Dict[str, Any]:
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _generate_id() -> str:
        return secrets.token_hex(32)
