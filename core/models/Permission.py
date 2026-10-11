"""
Permission
------------
A named capability, e.g. "blog.create". Keys always start with the owning
app's name (<app>.<name>) so two apps can never collide.

Where rows come from (`source`):
    app     declared in an app's permissions.py and synced by
            `python console.py apps:discover` - managed in code, read-only in the admin UI
    custom  added by an administrator on the admin permissions page - editable there, and kept
            across discovery for as long as the owning app is still registered
"""

from core.database.fields import AutoField, CharField, EnumField, TimestampField
from core.models.BaseModel import BaseModel


class Permission(BaseModel):
    table_name = "permissions"

    SOURCES = ("app", "custom")

    id = AutoField()
    permission_key = CharField(max_length=100, unique=True)  # NOT `key` - KEY is a reserved word in MySQL
    label = CharField(max_length=255)
    description = CharField(max_length=500, null=True)
    app = CharField(max_length=100)
    source = EnumField(choices=list(SOURCES), default="app")
    created_at = TimestampField(auto_now_add=True)
    updated_at = TimestampField(auto_now_add=True, auto_now=True)

    @property
    def is_custom(self) -> bool:
        return self.attributes.get("source") == "custom"
