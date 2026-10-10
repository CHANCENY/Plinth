"""
User
-----
Wraps the 'users' table. Columns are declared as fields below (Django-style -
see core/database/fields.py) rather than a hand-written SCHEMA list; SCHEMA,
primary_key, and fillable are all derived from these fields automatically by
BaseModel.__init_subclass__. To evolve this table over time, run:

    python console.py migration    # generates a migration file from whatever changed here
    python console.py migrate      # applies pending migration files to the database
"""

import datetime
import hashlib
import hmac
import os
import re
from typing import Optional

from core.database.fields import AutoField, BooleanField, CharField, EnumField, TimestampField
from core.models.BaseModel import BaseModel


class User(BaseModel):
    table_name = "users"

    STATUSES = ("active", "pending", "suspended", "deleted", "blocked")
    EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    uid = AutoField()
    name = CharField(max_length=255, unique=True)
    email = CharField(max_length=255, unique=True)
    password = CharField(max_length=255)
    first_name = CharField(max_length=255, null=True)
    last_name = CharField(max_length=255, null=True)
    is_verified = BooleanField(null=True)
    status = EnumField(choices=list(STATUSES), default="pending")
    role = CharField(max_length=50, default="member")
    # comma-separated permission strings, or the literal "*" for all - see the `permissions`
    # property below, which is what BaseMiddleware.has_permission() actually reads via
    # getattr(current_user, "permissions", []). Stored under a different name (_raw suffix)
    # because a Field and a @property can't share one class attribute name.
    permissions_raw = CharField(max_length=1000, null=True)
    deleted_at = TimestampField(null=True)
    created_at = TimestampField(auto_now_add=True)
    updated_at = TimestampField(auto_now_add=True, auto_now=True)

    def __init__(self, db_connection, **attributes):
        plain_password = attributes.pop("password", None)
        permissions = attributes.pop("permissions", None)
        super().__init__(db_connection, **attributes)
        self.attributes.setdefault("status", "pending")
        self.attributes.setdefault("is_verified", False)
        self.attributes.setdefault("role", "member")
        if plain_password:
            self.set_password(plain_password)
        if permissions is not None:
            self.permissions = permissions

    # ------------------------------------------------------------------ #
    # role / permissions - read by core.http.middleware.BaseMiddleware
    # (has_role/has_permission), enforced per-route by PermissionMiddleware
    # via each route's "permission" list in Routing.py.
    # ------------------------------------------------------------------ #
    @property
    def permissions(self):
        """"*" (all permissions) or a list of permission strings, e.g. ["users.manage"]."""
        raw = self.attributes.get("permissions_raw")
        if raw is None or raw == "":
            return []
        if raw.strip() == "*":
            return "*"
        return [p.strip() for p in raw.split(",") if p.strip()]

    @permissions.setter
    def permissions(self, value) -> None:
        if value is None or value == "*":
            self.attributes["permissions_raw"] = value
        elif isinstance(value, (list, tuple, set)):
            self.attributes["permissions_raw"] = ",".join(value)
        else:
            self.attributes["permissions_raw"] = str(value)

    def has_permission(self, permission: str) -> bool:
        current = self.permissions
        return current == "*" or permission in current

    def has_role(self, role: str) -> bool:
        return self.attributes.get("role") == role

    def make_admin(self) -> "User":
        self.attributes["role"] = "admin"
        self.permissions = "*"
        return self

    # ------------------------------------------------------------------ #
    # password hashing - PBKDF2-HMAC-SHA256, stdlib only (no extra deps)
    # ------------------------------------------------------------------ #
    @staticmethod
    def hash_password(plain_password: str, iterations: int = 200_000) -> str:
        salt = os.urandom(16)
        derived = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
        return f"pbkdf2_sha256${iterations}${salt.hex()}${derived.hex()}"

    def set_password(self, plain_password: str) -> "User":
        self.attributes["password"] = self.hash_password(plain_password)
        return self

    def verify_password(self, plain_password: str) -> bool:
        stored = self.attributes.get("password")
        if not stored:
            return False
        try:
            _algo, iterations, salt_hex, hash_hex = stored.split("$")
            derived = hashlib.pbkdf2_hmac(
                "sha256", plain_password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
            )
            return hmac.compare_digest(derived, bytes.fromhex(hash_hex))
        except (ValueError, AttributeError):
            return False

    # ------------------------------------------------------------------ #
    # validation
    # ------------------------------------------------------------------ #
    @classmethod
    def _validate_email(cls, email: str) -> None:
        if not email or not cls.EMAIL_RE.match(email):
            raise ValueError(f"Invalid email address: {email!r}")

    def save(self) -> "User":
        if "email" in self.attributes:
            self._validate_email(self.attributes["email"])
        if self.attributes.get("status") not in (None, *self.STATUSES):
            raise ValueError(f"Invalid status '{self.attributes['status']}'. Expected one of {self.STATUSES}")
        return super().save()

    # ------------------------------------------------------------------ #
    # finders
    # ------------------------------------------------------------------ #
    @classmethod
    def find_by_email(cls, db_connection, email: str) -> Optional["User"]:
        return cls.objects(db_connection).filter(email=email).first()

    @classmethod
    def find_by_name(cls, db_connection, name: str) -> Optional["User"]:
        return cls.objects(db_connection).filter(name=name).first()

    @classmethod
    def authenticate(cls, db_connection, email: str, plain_password: str) -> Optional["User"]:
        user = cls.find_by_email(db_connection, email)
        if user and user.attributes.get("status") == "active" and user.verify_password(plain_password):
            return user
        return None

    # ------------------------------------------------------------------ #
    # status helpers
    # ------------------------------------------------------------------ #
    def set_status(self, status: str) -> "User":
        if status not in self.STATUSES:
            raise ValueError(f"Invalid status '{status}'. Expected one of {self.STATUSES}")
        self.attributes["status"] = status
        return self

    def mark_verified(self) -> "User":
        self.attributes["is_verified"] = True
        return self

    # ------------------------------------------------------------------ #
    # soft delete - matches the deleted_at/status columns in SCHEMA
    # ------------------------------------------------------------------ #
    def delete(self) -> int:
        """Soft delete: sets status='deleted' and stamps deleted_at, keeping the row."""
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        affected = self._this_row("delete").update(status="deleted", deleted_at=now)
        self.attributes["status"] = "deleted"
        self.attributes["deleted_at"] = now
        return affected

    def hard_delete(self) -> int:
        """Actually removes the row - use with care."""
        return super().delete()

    def restore(self) -> "User":
        self._this_row("restore").update(status="active", deleted_at=None)
        self.attributes["status"] = "active"
        self.attributes["deleted_at"] = None
        return self

    def __repr__(self):
        return (
            f"<User uid={self.attributes.get('uid')} "
            f"email={self.attributes.get('email')!r} status={self.attributes.get('status')!r}>"
        )
