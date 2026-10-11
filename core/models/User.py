"""
User
-----
Wraps the 'users' table. Columns are declared as fields below (Django-style -
see core/database/fields.py) rather than a hand-written SCHEMA list; SCHEMA,
primary_key, and fillable are all derived from these fields automatically by
BaseModel.__init_subclass__. To evolve this table over time, run:

    python console.py migration    # generates a migration file from whatever changed here
    python console.py migrate      # applies pending migration files to the database

Roles and permissions
    A user has one `role` (a roles.role_key such as "admin.member") and a list of permissions
    stored as JSON in `permissions_raw` (["*"] = administrator, everything). The permissions are a
    snapshot: they're copied from the role's column of the admin permissions matrix when the
    account is created (save()) and are editable per user by an administrator afterwards.

    Because they're a snapshot, changing `role` alone does NOT change what the user can do. Use
    set_role() to move someone to another role *and* take that role's permissions - assigning
    `user.role = ...` directly is only right if you're setting the permissions yourself too.
"""

import datetime
import hashlib
import hmac
import json
import os
import re
from typing import List, Optional

from core.database.fields import AutoField, BooleanField, CharField, EnumField, TextField, TimestampField
from core.models.BaseModel import BaseModel
from core.models.Role import Role


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
    role = CharField(max_length=100, default=Role.DEFAULT_KEY)  # a roles.role_key, e.g. "admin.member"
    # JSON list of permission keys, e.g. ["admin.users.manage", "blog.create"], or ["*"] for
    # everything - see the `permissions` property below, which is what
    # BaseMiddleware.has_permission() actually reads via getattr(current_user, "permissions", []).
    # Stored under a different name (_raw suffix) because a Field and a @property can't share
    # one class attribute name. Filled in from the user's role when the account is created
    # (see save()), and editable per user by an administrator afterwards.
    permissions_raw = TextField(null=True)
    deleted_at = TimestampField(null=True)
    created_at = TimestampField(auto_now_add=True)
    updated_at = TimestampField(auto_now_add=True, auto_now=True)

    def __init__(self, db_connection, **attributes):
        plain_password = attributes.pop("password", None)
        permissions = attributes.pop("permissions", None)
        super().__init__(db_connection, **attributes)
        self.attributes.setdefault("status", "pending")
        self.attributes.setdefault("is_verified", False)
        self.attributes.setdefault("role", Role.DEFAULT_KEY)
        if plain_password:
            self.set_password(plain_password)
        if permissions is not None:
            self.permissions = permissions

    # ------------------------------------------------------------------ #
    # role / permissions - read by core.http.middleware.BaseMiddleware
    # (has_role/has_permission), enforced per-route by PermissionMiddleware
    # via each route's "permission" list (see core/http/routing/route.py).
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse_permissions(raw) -> List[str]:
        """Permission keys from a stored value: a JSON list (current format), or - for rows saved
        before permissions became JSON - the literal "*" or a comma-separated string."""
        if raw is None:
            return []
        if isinstance(raw, (list, tuple, set)):
            items = list(raw)
        else:
            text = str(raw).strip()
            if not text:
                return []
            if text.startswith("["):
                try:
                    items = json.loads(text)
                except ValueError:
                    items = text.strip("[]").replace('"', "").split(",")
            else:
                items = text.split(",")
        return sorted({str(item).strip() for item in items if str(item).strip()})

    @staticmethod
    def dump_permissions(permissions) -> str:
        """The JSON string stored in permissions_raw. "*" (superuser) collapses to just ["*"]."""
        items = User.parse_permissions(permissions)
        if "*" in items:
            items = ["*"]
        return json.dumps(items)

    @property
    def permissions(self):
        """"*" (all permissions) or a list of permission strings, e.g. ["admin.users.manage"]."""
        items = self.parse_permissions(self.attributes.get("permissions_raw"))
        return "*" if "*" in items else items

    @permissions.setter
    def permissions(self, value) -> None:
        if value is None:
            self.attributes["permissions_raw"] = None
        elif value == "*":
            self.attributes["permissions_raw"] = self.dump_permissions(["*"])
        else:
            self.attributes["permissions_raw"] = self.dump_permissions(value)

    def has_permission(self, permission: str) -> bool:
        current = self.permissions
        return current == "*" or permission in current

    def is_admin(self) -> bool:
        """True for an administrator: someone holding every permission ("*" in permissions_raw)."""
        return self.permissions == "*"

    def has_role(self, role: str) -> bool:
        return self.attributes.get("role") == role

    def set_role(self, role_key: str, apply_role_permissions: bool = True) -> "User":
        """
        Moves the user to `role_key`. By default their permissions are replaced with whatever that
        role grants (["*"] for a superuser role) - so demoting an administrator really removes
        their full access. Pass apply_role_permissions=False to change only the label and keep the
        user's current permissions (e.g. when they were hand-tuned).
        """
        if not role_key:
            raise ValueError("A role key is required")
        self.attributes["role"] = role_key
        if apply_role_permissions:
            self.permissions = Role.permissions_for(self.db, role_key)
        return self

    def make_admin(self) -> "User":
        self.attributes["role"] = Role.ADMIN_KEY
        self.permissions = "*"  # explicit, so it works before the roles table has been synced
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
        if not self._persisted and self.attributes.get("permissions_raw") in (None, ""):
            # A brand-new account that wasn't given explicit permissions starts with whatever its
            # role grants (the role's column of the admin permissions matrix), saved as JSON.
            self.permissions = Role.permissions_for(self.db, self.attributes.get("role") or Role.DEFAULT_KEY)
        elif self.attributes.get("permissions_raw") not in (None, ""):
            # Whatever way permissions_raw was set (permissions=, permissions_raw="a,b", a legacy
            # "*", ...), what reaches the database is always the canonical JSON list.
            self.attributes["permissions_raw"] = self.dump_permissions(self.attributes["permissions_raw"])
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
