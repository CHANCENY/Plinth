"""
File
-----
Wraps the 'file_managed' table - tracks uploaded/stored files as their own
records (similar in spirit to Drupal's file_managed table), independent of
whatever record they end up attached to.

No schema was given for this table originally, so a default is declared
below as fields (Django-style - see core/database/fields.py), matching the
style of User's columns. It owns a `uid` column linking back to users.uid
(nullable, so system-generated files with no owning user are allowed).
Adjust the fields below if this doesn't fit your needs - e.g. add
parent_type/parent_id fields if files need to attach to more than one kind
of record - then run:

    python console.py migration    # generates a migration file from what changed
    python console.py migrate      # applies it
"""

import datetime
from typing import List, Optional

from core.database.fields import AutoField, BigIntegerField, CharField, EnumField, ForeignKey, IntegerField, TimestampField
from core.models.BaseModel import BaseModel


class File(BaseModel):
    table_name = "file_managed"

    STATUSES = ("temporary", "permanent")

    fid = AutoField()
    uid = IntegerField(null=True)  # owning user; nullable for system-generated files
    filename = CharField(max_length=255)
    uri = CharField(max_length=1024)  # storage path or URL
    filemime = CharField(max_length=255, null=True)
    filesize = BigIntegerField(unsigned=True, default=0)  # bytes
    checksum = CharField(max_length=64, null=True)  # e.g. sha256, for integrity checks / de-duping
    status = EnumField(choices=list(STATUSES), default="temporary")
    deleted_at = TimestampField(null=True)
    created_at = TimestampField(auto_now_add=True)
    updated_at = TimestampField(auto_now_add=True, auto_now=True)
    uid_fk = ForeignKey(column="uid", references="users", references_column="uid")

    def __init__(self, db_connection, **attributes):
        super().__init__(db_connection, **attributes)
        self.attributes.setdefault("status", "temporary")
        self.attributes.setdefault("filesize", 0)

    # ------------------------------------------------------------------ #
    # validation
    # ------------------------------------------------------------------ #
    def save(self) -> "File":
        if self.attributes.get("status") not in (None, *self.STATUSES):
            raise ValueError(f"Invalid status '{self.attributes['status']}'. Expected one of {self.STATUSES}")
        return super().save()

    # ------------------------------------------------------------------ #
    # finders
    # ------------------------------------------------------------------ #
    @classmethod
    def find_by_uri(cls, db_connection, uri: str) -> Optional["File"]:
        return cls.objects(db_connection).filter(uri=uri).first()

    @classmethod
    def for_user(cls, db_connection, uid, limit: Optional[int] = None) -> List["File"]:
        queryset = cls.objects(db_connection).filter(uid=uid).order_by("-created_at")
        if limit is not None:
            queryset = queryset[:limit]
        return list(queryset)

    # ------------------------------------------------------------------ #
    # status helpers
    # ------------------------------------------------------------------ #
    def mark_permanent(self) -> "File":
        self.attributes["status"] = "permanent"
        return self

    def mark_temporary(self) -> "File":
        self.attributes["status"] = "temporary"
        return self

    # ------------------------------------------------------------------ #
    # soft delete - file_managed keeps a history rather than losing rows
    # ------------------------------------------------------------------ #
    def delete(self) -> int:
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        affected = self._this_row("delete").update(deleted_at=now)
        self.attributes["deleted_at"] = now
        return affected

    def hard_delete(self) -> int:
        """Actually removes the row - use with care (doesn't remove the underlying stored file itself)."""
        return super().delete()

    def __repr__(self):
        return (
            f"<File fid={self.attributes.get('fid')} "
            f"filename={self.attributes.get('filename')!r} status={self.attributes.get('status')!r}>"
        )
