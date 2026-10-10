"""
BaseModel
----------
Active-record-style base class for a table. Subclasses (User, File, ...) set
table_name, primary_key, and fillable, and get Django-style querying and
persistence for free:

    # querying - see core/models/QuerySet.py for every lookup/method
    User.objects(db).filter(status="active").order_by("-created_at").all()
    User.objects(db).filter(email__icontains="@example.com").first()
    User.objects(db).exclude(status__in=["deleted", "blocked"]).count()
    User.objects(db).create(name="chance", email="c@example.com", password="s3cret!!")
    user, created = User.objects(db).get_or_create(email="c@example.com", defaults={"name": "chance"})

    # instances
    user = User(db, name="chance", email="c@example.com", password="s3cret!!")
    user.save()               # INSERT the first time, UPDATE after that
    user.name = ...           # attribute access reads self.attributes (user.name, user["name"])
    user.delete()

`objects(db)` takes the connection because this framework passes
DatabaseConnection explicitly everywhere instead of keeping a global one the
way Django does - see QuerySet's docstring. The shorter helpers below
(find/find_by/all) still work and are now just thin wrappers over objects().

This is for the SQL drivers (mysql/postgres/sqlite) only - a DatabaseConnection
configured with DB_DRIVER=mongodb has no fixed columns to map onto attributes,
so document-shaped data there is better handled directly through
db.connect()["collection"] (pymongo's native API) rather than through a model
like this one.
"""

import datetime
from typing import Any, Dict, List, Optional

from core.database.fields import Field
from core.models.QuerySet import QuerySet


class BaseModel:
    table_name: str = None
    primary_key: str = "id"
    fillable: List[str] = []
    timestamps: bool = True  # expects created_at / updated_at columns

    SCHEMA: List[str] = []  # auto-filled from declared Fields below, unless a subclass writes its own
    _fields: Dict[str, Field] = {}  # name -> Field, declaration order, filled in by __init_subclass__

    def __init_subclass__(cls, **kwargs):
        """
        Collects every Field declared directly on this model (Django-style, see
        core/database/fields.py) and derives SCHEMA/primary_key/fillable from them,
        so a model only has to declare its fields once. A model with no Field
        attributes (still using a hand-written SCHEMA list) is untouched - this
        is purely additive.
        """
        super().__init_subclass__(**kwargs)

        fields: Dict[str, Field] = {}
        for name, value in vars(cls).items():
            if isinstance(value, Field):
                value.name = name
                fields[name] = value
        if not fields:
            return
        cls._fields = fields

        columns = [f.to_sql() for f in fields.values() if not f.is_constraint]
        constraints = [f.to_sql() for f in fields.values() if f.is_constraint]
        cls.SCHEMA = columns + constraints

        primary_keys = [name for name, f in fields.items() if f.primary_key]
        if primary_keys:
            cls.primary_key = primary_keys[0]

        if "fillable" not in vars(cls):  # only auto-derive if this model didn't set its own fillable
            cls.fillable = [name for name, f in fields.items() if not f.is_constraint and not f.primary_key]

    @classmethod
    def create_table(cls, db_connection, if_not_exists: bool = False):
        """Runs this model's SCHEMA (hand-written or field-derived) directly - quick/dev use.
        For versioned schema changes over time, use the migration CLI instead:
        python console.py migration, then python console.py migrate."""
        return db_connection.schema().create_table(cls.table_name, cls.SCHEMA, if_not_exists)

    def __init__(self, db_connection, **attributes):
        if self.table_name is None:
            raise NotImplementedError(f"{type(self).__name__} must set table_name")
        self.db = db_connection
        self.attributes: Dict[str, Any] = {}
        # False until this instance has been written to (or loaded from) the database - decides
        # whether save() INSERTs or UPDATEs, so it works for caller-supplied keys (e.g. sessions.id)
        # and not just auto-increment ones. Django tracks the same thing as `_state.adding`.
        self._persisted = False
        self.fill(attributes)

    # ------------------------------------------------------------------ #
    # attribute access - lets you do user.name, user.email, etc.
    # ------------------------------------------------------------------ #
    def __getattr__(self, name):
        # only reached when normal attribute lookup fails
        try:
            return self.__dict__["attributes"][name]
        except KeyError:
            raise AttributeError(f"{type(self).__name__} has no attribute '{name}'")

    def __getitem__(self, key):
        return self.attributes[key]

    def __setitem__(self, key, value):
        self.attributes[key] = value

    def __contains__(self, key):
        return key in self.attributes

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.attributes)

    def fill(self, data: Dict[str, Any]) -> "BaseModel":
        for key, value in data.items():
            if key in self.fillable or key == self.primary_key:
                self.attributes[key] = value
        return self

    # ------------------------------------------------------------------ #
    # Django-style querying
    # ------------------------------------------------------------------ #
    @classmethod
    def objects(cls, db_connection) -> QuerySet:
        """Entry point for queries: Model.objects(db).filter(...).first() - see QuerySet."""
        return QuerySet(db_connection, cls)

    @classmethod
    def find(cls, db_connection, id_value) -> Optional["BaseModel"]:
        return cls.objects(db_connection).filter(**{cls.primary_key: id_value}).first()

    @classmethod
    def find_by(cls, db_connection, column: str, value: Any) -> Optional["BaseModel"]:
        return cls.objects(db_connection).filter(**{column: value}).first()

    @classmethod
    def all(cls, db_connection, limit: Optional[int] = None) -> List["BaseModel"]:
        queryset = cls.objects(db_connection).all()
        if limit is not None:
            queryset = queryset[:limit]
        return list(queryset)

    @classmethod
    def where(cls, db_connection, column: str, operator: str, value: Any = None):
        """
        Legacy escape hatch: returns the RAW query builder (not model instances), pre-scoped to this
        table. Prefer objects(db).filter(...).
        """
        return db_connection.connect().table(cls.table_name).where(column, operator, value)

    @classmethod
    def _from_row(cls, db_connection, row) -> "BaseModel":
        instance = cls.__new__(cls)
        instance.db = db_connection
        instance.attributes = dict(row)
        instance._persisted = True
        return instance

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #
    def _queryset(self) -> QuerySet:
        return type(self).objects(self.db)

    def _query(self):
        """Legacy escape hatch: the raw query builder for this table. Prefer _queryset()/objects()."""
        return self.db.connect().table(self.table_name)

    def _this_row(self, action: str) -> QuerySet:
        """A QuerySet matching just this instance's row (by primary key)."""
        pk = self.attributes.get(self.primary_key)
        if pk is None:
            raise ValueError(f"Cannot {action} a record with no primary key set")
        return self._queryset().filter(**{self.primary_key: pk})

    def save(self) -> "BaseModel":
        """INSERTs the first time, UPDATEs after that (see _persisted)."""
        if self.timestamps:
            now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            self.attributes.setdefault("created_at", now)
            self.attributes["updated_at"] = now

        data = {k: v for k, v in self.attributes.items() if k in self.fillable}
        pk_value = self.attributes.get(self.primary_key)

        if not self._persisted:
            if pk_value is not None:
                data[self.primary_key] = pk_value  # caller-supplied key (e.g. a session id)
            new_id = self._queryset()._insert_row(data)
            if pk_value is None:
                self.attributes[self.primary_key] = new_id
            self._persisted = True
        else:
            data.pop(self.primary_key, None)  # never rewrite the key we're matching on
            self._this_row("save").update(**data)
        return self

    def delete(self) -> int:
        """Hard delete - permanently removes the row. Subclasses may override for soft-delete."""
        affected = self._this_row("delete").delete()
        self._persisted = False
        return affected

    def refresh(self) -> "BaseModel":
        row = self._this_row("refresh").first()
        if row is not None:
            self.attributes = dict(row.attributes)
        return self
