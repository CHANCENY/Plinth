"""
Post
-----
Demo model for the "blog" app (public/apps/blog/) - proves an app's models
are discovered by `python console.py apps:discover` and picked up by the
migration CLI (migration/migrate/migrate:fresh) right alongside the core
models (User, File, ...), with no special-casing needed anywhere else.
"""

from core.database.fields import AutoField, CharField, TextField, TimestampField
from core.models.BaseModel import BaseModel


class Post(BaseModel):
    table_name = "blog_posts"

    id = AutoField()
    title = CharField(max_length=255)
    slug = CharField(max_length=255, unique=True)
    body = TextField(null=True)
    created_at = TimestampField(auto_now_add=True)
    updated_at = TimestampField(auto_now_add=True, auto_now=True)
