"""
PasswordResetService
-----------------------
Issues and verifies one-time password-reset tokens, stored in a
password_resets table through the same DatabaseConnection/QueryBuilder every
other part of the framework uses. One pending reset per email at a time -
requesting a new one replaces any existing one for that address.

Expected table - create with PasswordResetService.create_table(db), or via
the migration CLI (python console.py migrate), which discovers this table
through the PasswordReset model (core/models/PasswordReset.py):
    password_resets
        email        VARCHAR(255) PRIMARY KEY
        token_hash   VARCHAR(255) NOT NULL
        expires_at   TIMESTAMP NOT NULL
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP

Only the token's hash is stored, the same principle as password storage -
the raw token is returned once, from create(), to go out in the reset email.

Expected .env keys
---------------------
PASSWORD_RESET_LIFETIME   seconds, default: 3600 (1 hour)

Usage
-----
    resets = PasswordResetService(db)

    token = resets.create(user.email)              # -> put this in the reset-password email/link
    ...
    if resets.verify(email, token):
        resets.consume(email)
        user.set_password(new_password).save()
"""

import datetime
import hashlib
import hmac
import secrets
from typing import Optional

from core.models.PasswordReset import PasswordReset
from core.services.EnvService import EnvService

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class PasswordResetService:
    table_name = PasswordReset.table_name

    def __init__(self, db_connection, lifetime_seconds: Optional[int] = None, env: Optional[EnvService] = None):
        self.db = db_connection
        env = env or EnvService()
        self.lifetime_seconds = lifetime_seconds or env.get_int("PASSWORD_RESET_LIFETIME", 3600)

    # ------------------------------------------------------------------ #
    # schema - the password_resets table itself is defined on the
    # PasswordReset model (core/models/PasswordReset.py), so the migration
    # CLI can discover and run it the same way as every other table; this
    # just forwards for anyone still calling PasswordResetService.create_table(db).
    # ------------------------------------------------------------------ #
    @classmethod
    def create_table(cls, db_connection, if_not_exists: bool = True):
        return PasswordReset.create_table(db_connection, if_not_exists)

    def _query(self):
        return self.db.connect().table(self.table_name)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, email: str) -> str:
        """Issues a fresh token for `email`, replacing any existing pending reset. Returns the raw token."""
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.datetime.utcnow() + datetime.timedelta(seconds=self.lifetime_seconds)).strftime(
            TIMESTAMP_FORMAT
        )
        token_hash = self._hash(token)

        if self._query().where("email", email).first() is not None:
            self._query().where("email", email).update({"token_hash": token_hash, "expires_at": expires_at})
        else:
            self._query().insert({"email": email, "token_hash": token_hash, "expires_at": expires_at})

        return token

    def verify(self, email: str, token: str) -> bool:
        """True if `token` is the current, non-expired reset token for `email`."""
        row = self._query().where("email", email).first()
        if row is None:
            return False
        if self._is_expired(row):
            self._query().where("email", email).delete()
            return False
        return hmac.compare_digest(self._hash(token), row["token_hash"])

    def consume(self, email: str) -> None:
        """Deletes the pending reset for `email` - call this once the new password has been saved."""
        self._query().where("email", email).delete()

    @staticmethod
    def _is_expired(row) -> bool:
        text = str(row["expires_at"]).split(".")[0].replace("T", " ")
        expires_at = datetime.datetime.strptime(text, TIMESTAMP_FORMAT)
        return expires_at < datetime.datetime.utcnow()
