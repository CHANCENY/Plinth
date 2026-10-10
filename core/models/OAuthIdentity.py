"""
OAuthIdentity
---------------
Links a User to an external OAuth ("social login") account - one row per
(provider, provider_user_id) pair, so a single User can have several linked
providers (Google AND GitHub, say) without adding any columns to the users
table you already defined.

Columns are declared as fields below (Django-style - see
core/database/fields.py); SCHEMA/primary_key/fillable are derived from them
automatically. To evolve this table over time:

    python console.py migration
    python console.py migrate
"""

from typing import Optional

from core.database.fields import AutoField, CharField, ForeignKey, IntegerField, TimestampField, UniqueTogether
from core.models.BaseModel import BaseModel


class OAuthIdentity(BaseModel):
    table_name = "oauth_identities"

    id = AutoField()
    uid = IntegerField()
    provider = CharField(max_length=32)
    provider_user_id = CharField(max_length=255)
    created_at = TimestampField(auto_now_add=True)
    updated_at = TimestampField(auto_now_add=True, auto_now=True)
    unique_identity = UniqueTogether(columns=["provider", "provider_user_id"], name="uniq_oauth_identity")
    uid_fk = ForeignKey(column="uid", references="users", references_column="uid")

    @classmethod
    def find_by_provider(cls, db_connection, provider: str, provider_user_id: str) -> Optional["OAuthIdentity"]:
        return cls.objects(db_connection).filter(provider=provider, provider_user_id=provider_user_id).first()
