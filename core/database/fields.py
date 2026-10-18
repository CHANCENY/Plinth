"""
fields
-------
Django-style field declarations for models, replacing hand-written SCHEMA
column strings. A model lists its columns as class attributes:

    class User(BaseModel):
        table_name = "users"

        uid = AutoField()
        name = CharField(max_length=255, unique=True)
        email = CharField(max_length=255, unique=True)
        password = CharField(max_length=255)
        first_name = CharField(max_length=255, null=True)
        status = EnumField(choices=["active", "pending", "suspended", "deleted", "blocked"], default="pending")
        created_at = TimestampField(auto_now_add=True)
        updated_at = TimestampField(auto_now_add=True, auto_now=True)

BaseModel.__init_subclass__ collects every Field declared directly on a model
into cls._fields (an ordered dict, declaration order preserved) and derives
SCHEMA, primary_key, and fillable from it automatically - see BaseModel's
docstring. Each field's to_sql() is exactly the column-definition string that
used to be hand-written in SCHEMA; the migration CLI (console.py migration /
migrate) uses that same string, per field, as the state it diffs between
migrations to decide what changed.

Column syntax here is MySQL-flavored, same convention the project has used
throughout (AUTO_INCREMENT, ENUM, ON UPDATE CURRENT_TIMESTAMP) - see the note
in Migrator's docstring about what that means for running migrate on SQLite.

Field types
    AutoField, SmallIntegerField, IntegerField, BigIntegerField   whole numbers
    FloatField, DecimalField(max_digits, decimal_places)          approximate / exact numbers
    CharField(max_length), TextField(size)                        text ("text" | "medium" | "long")
    BooleanField, EnumField(choices)
    DateField, TimeField, DateTimeField, TimestampField           dates and times
    JSONField                                                     any JSON-serialisable Python value
    UUIDField(auto=False)                                         uuid.UUID values; auto=True generates one
    BinaryField(max_length=None, size="blob")                     bytes: BLOB / MEDIUMBLOB / LONGBLOB / VARBINARY(n)

    Relationships (see the end of this file): ForeignKey, OneToOneField, ManyToManyField
    Table constraints: UniqueTogether

Python values <-> database values
    Every field has to_python() (database -> Python, applied when a row is loaded) and to_db()
    (Python -> database, applied when a value is saved or compared in a filter), so a DecimalField
    gives you decimal.Decimal, a JSONField a dict/list, a UUIDField a uuid.UUID, a DateField a
    datetime.date and a BinaryField bytes - identically on every driver. DateTimeField and
    TimestampField deliberately pass values through untouched.
"""

import datetime
import decimal
import json
import uuid
from typing import Any, List, Optional

_UNSET = object()  # sentinel: "no DEFAULT clause" is different from default=None ("DEFAULT NULL")


class Field:
    """
    Base class for every field type. Subclasses normally only need to
    implement column_type(); override to_sql() instead if a field's syntax
    doesn't fit the "name TYPE [PRIMARY KEY] [NOT NULL|NULL] [UNIQUE] [DEFAULT ...]"
    shape this base class builds (TimestampField and the constraint-only
    fields below do that).
    """

    is_constraint = False  # True for fields that render a table-level constraint, not a column (see below)
    is_virtual = False  # True for fields with no column and no DDL at all (ManyToManyField)
    is_relation = False  # True for ForeignKey / OneToOneField / ManyToManyField
    auto_default = False  # True if generate_default() should supply a value for a new row (UUIDField(auto=True))

    def __init__(self, null: bool = False, default: Any = _UNSET, unique: bool = False, primary_key: bool = False):
        self.null = null
        self.default = default
        self.unique = unique
        self.primary_key = primary_key
        self.name: Optional[str] = None  # set by __set_name__ below when the class body assigns this field

    def __set_name__(self, owner, name: str) -> None:
        # Called automatically by Python for every class-body attribute that defines this hook,
        # right when the model class is created - e.g. `uid = AutoField()` on User triggers
        # AutoField().__set_name__(User, "uid").
        self.name = name

    def __get__(self, instance, owner):
        # A model class attribute of a Field's own type isn't real column data - only the
        # instance value is (in instance.attributes). Without __get__/__set__ here, this
        # object being a plain class attribute would shadow BaseModel.__getattr__'s dict
        # lookup entirely (a plain class attribute always wins over __getattr__), so
        # `user.uid` would return this AutoField object instead of the row's uid.
        if instance is None:
            return self  # accessed on the class itself (User.status) - hand back the Field
        attributes = instance.attributes
        if self.name not in attributes and self.name in instance.__dict__.get("_deferred", ()):
            instance._load_deferred(self.name)  # loaded with .only()/.defer() - fetched on first access
        return attributes.get(self.name)

    def __set__(self, instance, value) -> None:
        instance.attributes[self.name] = value
        deferred = instance.__dict__.get("_deferred")
        if deferred:
            deferred.discard(self.name)  # assigning counts as loading it

    def contribute_to_class(self, owner) -> None:
        """Called once, right after the model class is built (see BaseModel.__init_subclass__) - lets
        relationship fields validate themselves and install their reverse accessors."""

    def to_python(self, value: Any) -> Any:
        """Database value -> Python value. Never called with None."""
        return value

    def to_db(self, value: Any) -> Any:
        """Python value -> something every driver can bind. Never called with None."""
        return value

    def generate_default(self) -> Any:
        """The value for a new row that didn't set this field (only used when auto_default is True)."""
        return None

    def column_type(self) -> str:
        raise NotImplementedError

    def migration_key(self) -> str:
        """Stable dict key used to diff this field's state between migrations. Plain fields key by their
        column name; constraint fields override this since they don't have one (see ForeignKey/UniqueTogether)."""
        return self.name

    def to_sql(self) -> str:
        if self.name is None:
            raise RuntimeError(f"{type(self).__name__} has no name - assign it as a class attribute on a model")
        parts = [self.name, self.column_type()]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        elif self.null:
            parts.append("NULL")
        else:
            parts.append("NOT NULL")
        if self.unique and not self.primary_key:
            parts.append("UNIQUE")
        if self.default is not _UNSET:
            parts.append(f"DEFAULT {self._format_default(self.default)}")
        return " ".join(parts)

    @staticmethod
    def _format_default(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"


# ---------------------------------------------------------------------- #
# concrete column fields
# ---------------------------------------------------------------------- #
class AutoField(Field):
    """Auto-incrementing integer, primary key by default (MySQL: INTEGER ... AUTO_INCREMENT)."""

    def __init__(self, primary_key: bool = True):
        super().__init__(null=False, primary_key=primary_key)

    def column_type(self) -> str:
        return "INTEGER"

    def to_sql(self) -> str:
        parts = [self.name, "INTEGER"]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        parts.append("AUTO_INCREMENT")
        return " ".join(parts)


class IntegerField(Field):
    def __init__(self, unsigned: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.unsigned = unsigned

    def column_type(self) -> str:
        return "INTEGER UNSIGNED" if self.unsigned else "INTEGER"


class BigIntegerField(Field):
    def __init__(self, unsigned: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.unsigned = unsigned

    def column_type(self) -> str:
        return "BIGINT UNSIGNED" if self.unsigned else "BIGINT"


class SmallIntegerField(Field):
    def __init__(self, unsigned: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.unsigned = unsigned

    def column_type(self) -> str:
        return "SMALLINT UNSIGNED" if self.unsigned else "SMALLINT"


class FloatField(Field):
    """Double-precision floating point (DOUBLE). Approximate - use DecimalField for money."""

    def column_type(self) -> str:
        return "DOUBLE"

    def to_python(self, value: Any) -> Any:
        return float(value)

    def to_db(self, value: Any) -> Any:
        return float(value)


class DecimalField(Field):
    """
    Exact fixed-point number (DECIMAL(max_digits, decimal_places)) - what to use for money. Values are
    decimal.Decimal in Python. Floats are accepted when saving but go through str(), so 0.1 is stored
    as 0.1 and not as the binary-float approximation of it.
    """

    def __init__(self, max_digits: int = 10, decimal_places: int = 2, **kwargs):
        super().__init__(**kwargs)
        if max_digits < 1 or not 0 <= decimal_places <= max_digits:
            raise ValueError("DecimalField needs max_digits >= 1 and 0 <= decimal_places <= max_digits")
        self.max_digits = max_digits
        self.decimal_places = decimal_places

    def column_type(self) -> str:
        return f"DECIMAL({self.max_digits},{self.decimal_places})"

    def to_python(self, value: Any) -> Any:
        return value if isinstance(value, decimal.Decimal) else decimal.Decimal(str(value))

    def to_db(self, value: Any) -> Any:
        return str(value if isinstance(value, decimal.Decimal) else decimal.Decimal(str(value)))


class CharField(Field):
    def __init__(self, max_length: int = 255, **kwargs):
        super().__init__(**kwargs)
        self.max_length = max_length

    def column_type(self) -> str:
        return f"VARCHAR({self.max_length})"


class TextField(Field):
    """TEXT holds 64 KB; size="medium" -> MEDIUMTEXT (16 MB), size="long" -> LONGTEXT (4 GB)."""

    SIZES = {"text": "TEXT", "medium": "MEDIUMTEXT", "long": "LONGTEXT"}

    def __init__(self, size: str = "text", **kwargs):
        super().__init__(**kwargs)
        if size not in self.SIZES:
            raise ValueError(f"TextField size must be one of {sorted(self.SIZES)}, got {size!r}")
        self.size = size

    def column_type(self) -> str:
        return self.SIZES[self.size]


class BooleanField(Field):
    def column_type(self) -> str:
        return "BOOLEAN"


class EnumField(Field):
    def __init__(self, choices: List[str], **kwargs):
        super().__init__(**kwargs)
        self.choices = list(choices)

    def column_type(self) -> str:
        rendered = ", ".join(f"'{choice}'" for choice in self.choices)
        return f"ENUM({rendered})"


class DateTimeField(Field):
    """Plain TIMESTAMP column with ordinary NOT NULL/NULL/DEFAULT handling (inherited from Field) -
    use this for a timestamp your own code always sets (e.g. a session's expires_at). Use
    TimestampField instead for MySQL's auto-stamped created_at/updated_at-style columns."""

    def column_type(self) -> str:
        return "TIMESTAMP"


class TimestampField(DateTimeField):
    """
    auto_now_add: stamp DEFAULT CURRENT_TIMESTAMP (set once, on insert).
    auto_now: add ON UPDATE CURRENT_TIMESTAMP (MySQL auto-refreshes it on every UPDATE).
    Neither implies the other - created_at wants auto_now_add only, updated_at wants both.
    Unlike the base Field, this never emits NOT NULL/NULL - matches how these columns are
    conventionally declared (nullability is moot once there's an auto-generated value).
    """

    def __init__(self, auto_now_add: bool = False, auto_now: bool = False, **kwargs):
        kwargs.setdefault("null", True)
        super().__init__(**kwargs)
        self.auto_now_add = auto_now_add
        self.auto_now = auto_now

    def to_sql(self) -> str:
        parts = [self.name, self.column_type()]
        if self.auto_now_add:
            parts.append("DEFAULT CURRENT_TIMESTAMP")
        elif self.default is not _UNSET:
            parts.append(f"DEFAULT {self._format_default(self.default)}")
        elif self.null:
            parts.append("DEFAULT NULL")
        if self.auto_now:
            parts.append("ON UPDATE CURRENT_TIMESTAMP")
        return " ".join(parts)


class DateField(Field):
    """A calendar date (DATE) - datetime.date in Python."""

    def column_type(self) -> str:
        return "DATE"

    def to_python(self, value: Any) -> Any:
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        return datetime.date.fromisoformat(str(value)[:10])

    def to_db(self, value: Any) -> Any:
        if isinstance(value, datetime.datetime):
            return value.date().isoformat()
        if isinstance(value, datetime.date):
            return value.isoformat()
        return datetime.date.fromisoformat(str(value)[:10]).isoformat()


class TimeField(Field):
    """A time of day (TIME) - datetime.time in Python. (MySQL returns TIME as a timedelta; that's converted.)"""

    def column_type(self) -> str:
        return "TIME"

    def to_python(self, value: Any) -> Any:
        if isinstance(value, datetime.time):
            return value
        if isinstance(value, datetime.timedelta):
            return (datetime.datetime.min + value).time()
        return datetime.time.fromisoformat(str(value))

    def to_db(self, value: Any) -> Any:
        if isinstance(value, datetime.time):
            return value.isoformat()
        if isinstance(value, datetime.timedelta):
            return (datetime.datetime.min + value).time().isoformat()
        return datetime.time.fromisoformat(str(value)).isoformat()


class JSONField(Field):
    """
    Any JSON-serialisable Python value (dict, list, str, number, bool) in a JSON column - dict/list
    in, dict/list out. Always pass the Python object, not an already-encoded string (that would be
    encoded a second time). None is stored as SQL NULL. MySQL doesn't allow a DEFAULT on JSON
    columns, so set defaults in code. Filtering inside the JSON isn't supported by QuerySet.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.default is not _UNSET:
            raise ValueError("JSONField can't have a database default (MySQL doesn't allow one) - set it in code")

    def column_type(self) -> str:
        return "JSON"

    def to_python(self, value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            value = bytes(value).decode("utf-8")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value  # a driver that already parsed it

    def to_db(self, value: Any) -> Any:
        return json.dumps(value, ensure_ascii=False)


class UUIDField(Field):
    """A UUID (CHAR(36)) - uuid.UUID in Python. UUIDField(auto=True) generates a uuid4 for new rows."""

    def __init__(self, auto: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.auto_default = auto

    def column_type(self) -> str:
        return "CHAR(36)"

    def to_python(self, value: Any) -> Any:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))

    def to_db(self, value: Any) -> Any:
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))

    def generate_default(self) -> Any:
        return uuid.uuid4()


class BinaryField(Field):
    """
    Raw bytes. By default a BLOB (64 KB); size="medium" -> MEDIUMBLOB (16 MB), size="long" -> LONGBLOB
    (4 GB); max_length=n -> VARBINARY(n) for short fixed-limit values like hashes. Only bytes-like values
    are accepted (str is rejected rather than silently encoded). MySQL doesn't allow a DEFAULT on blobs.
    Remember QuerySet loads every column of a row: for big blobs use .defer("data") or .only(...) so
    listing rows doesn't pull the bytes into memory.
    """

    SIZES = {"blob": "BLOB", "medium": "MEDIUMBLOB", "long": "LONGBLOB"}

    def __init__(self, max_length: Optional[int] = None, size: str = "blob", **kwargs):
        super().__init__(**kwargs)
        if size not in self.SIZES:
            raise ValueError(f"BinaryField size must be one of {sorted(self.SIZES)}, got {size!r}")
        if self.default is not _UNSET:
            raise ValueError("BinaryField can't have a database default (MySQL doesn't allow one on blobs) - set it in code")
        self.max_length = max_length
        self.size = size

    def column_type(self) -> str:
        return f"VARBINARY({self.max_length})" if self.max_length else self.SIZES[self.size]

    def to_python(self, value: Any) -> Any:
        return bytes(value) if isinstance(value, (bytearray, memoryview)) else value

    def to_db(self, value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        raise TypeError(f"BinaryField '{self.name}' needs bytes, got {type(value).__name__}")


# ---------------------------------------------------------------------- #
# table-level constraints - contribute a schema line but aren't columns:
# excluded from ALTER TABLE ADD COLUMN handling and from fillable/primary_key
# ---------------------------------------------------------------------- #
class ForeignKey(Field):
    """
    A foreign-key relationship. It wraps a column you declare normally (an IntegerField, or whatever
    matches the referenced key), adds the table-level FOREIGN KEY constraint, and - new - lets you follow
    the relationship in Python and in queries:

        class Media(BaseModel):
            uid = IntegerField(null=True)
            owner = ForeignKey(column="uid", references=User, on_delete="SET NULL", related_name="media_items")

        media.owner                     # the User (loaded on first access, then cached)
        media.owner = some_user         # sets media.uid
        user.media_items                # reverse side: a QuerySet of that user's Media
        Media.objects(db).select_related("owner")                  # one JOIN instead of a query per row
        Media.objects(db).filter(owner__name="Ada")                # filter across the relation
        Media.objects(db).order_by("owner__name")

    column             the local column holding the key
    references         the related model class (preferred), or just a table name (then the accessor works
                       once that table's model has been imported)
    references_column  the key column on the related table; defaults to the related model's primary key
                       ("id" if you gave a table name)
    on_delete/on_update  CASCADE, SET NULL, RESTRICT, NO ACTION or SET DEFAULT - added to the constraint.
                       Left unset, the constraint (and your migrations) are exactly what they were before.
    related_name       name of the reverse accessor on the related model (default "<model>_set");
                       "+" disables it

    The migration key is "fk_<column>" and does not depend on the attribute name, so renaming the
    attribute never produces a migration.
    """

    is_constraint = True
    is_relation = True
    ACTIONS = ("CASCADE", "SET NULL", "RESTRICT", "NO ACTION", "SET DEFAULT")
    single_reverse = False  # a reverse accessor returns a QuerySet; OneToOneField returns one object

    def __init__(self, column: str, references: Any, references_column: Optional[str] = None,
                 on_delete: Optional[str] = None, on_update: Optional[str] = None,
                 related_name: Optional[str] = None):
        super().__init__()
        self.column = column
        self.references = references
        self._references_column = references_column
        self.on_delete = self._action(on_delete, "on_delete")
        self.on_update = self._action(on_update, "on_update")
        self.related_name = related_name

    @classmethod
    def _action(cls, value: Optional[str], label: str) -> Optional[str]:
        if value is None:
            return None
        normalised = str(value).upper().replace("_", " ")
        if normalised not in cls.ACTIONS:
            raise ValueError(f"ForeignKey {label} must be one of {', '.join(cls.ACTIONS)}, got {value!r}")
        return normalised

    # ---- what it points at ------------------------------------------------------------ #
    @property
    def references_table(self) -> str:
        return self.references if isinstance(self.references, str) else self.references.table_name

    @property
    def references_column(self) -> str:
        if self._references_column:
            return self._references_column
        return "id" if isinstance(self.references, str) else self.references.primary_key

    def related_model(self):
        """The related model class. Raises LookupError if only a table name was given and that model hasn't been imported."""
        if not isinstance(self.references, str):
            return self.references
        from core.models.BaseModel import BaseModel

        model = BaseModel._registry.get(self.references)
        if model is None:
            raise LookupError(
                f"ForeignKey '{self.name}': no model for table '{self.references}' has been imported yet - "
                f"import it, or pass the model class as `references`"
            )
        return model

    def target_model_if_ready(self):
        try:
            return self.related_model()
        except LookupError:
            return None

    # ---- schema ------------------------------------------------------------------------ #
    def migration_key(self) -> str:
        return f"fk_{self.column}"

    def to_sql(self) -> str:
        sql = f"FOREIGN KEY ({self.column}) REFERENCES {self.references_table}({self.references_column})"
        if self.on_delete:
            sql += f" ON DELETE {self.on_delete}"
        if self.on_update:
            sql += f" ON UPDATE {self.on_update}"
        return sql

    def contribute_to_class(self, owner) -> None:
        from core.models.relations import connect_reverse

        column_field = owner._fields.get(self.column)
        if column_field is None or column_field.is_constraint or column_field.is_virtual:
            raise ValueError(
                f"{owner.__name__}.{self.name}: ForeignKey column '{self.column}' isn't a column declared on {owner.__name__}"
            )
        if self.on_delete == "SET NULL" and not column_field.null:
            raise ValueError(f"{owner.__name__}.{self.name}: on_delete='SET NULL' needs {self.column} declared with null=True")
        self.owner = owner
        connect_reverse(self, owner)

    # ---- following it from an instance --------------------------------------------------- #
    def __get__(self, instance, owner):
        if instance is None:
            return self
        key = getattr(instance, self.column)
        cache = instance.__dict__.setdefault("_relation_cache", {})
        cached = cache.get(self.name)
        if cached is not None and cached[0] == key:
            return cached[1]
        if key is None:
            return None
        related = self.related_model().objects(instance.db).filter(**{self.references_column: key}).first()
        cache[self.name] = (key, related)
        return related

    def __set__(self, instance, value) -> None:
        from core.models.BaseModel import BaseModel

        cache = instance.__dict__.setdefault("_relation_cache", {})
        if value is None:
            setattr(instance, self.column, None)
            cache.pop(self.name, None)
        elif isinstance(value, BaseModel):
            key = value.attributes.get(self.references_column)
            if key is None:
                raise ValueError(f"{self.name}: save() the related {type(value).__name__} before assigning it")
            setattr(instance, self.column, key)
            cache[self.name] = (key, value)
        else:
            setattr(instance, self.column, value)  # a raw key
            cache.pop(self.name, None)


class OneToOneField(ForeignKey):
    """
    A ForeignKey where at most one row on this side points at each related row - declare the wrapped
    column with unique=True (required, and checked). The reverse accessor returns the single related
    object (or None) instead of a QuerySet:

        class Profile(BaseModel):
            uid = IntegerField(unique=True)
            user = OneToOneField(column="uid", references=User, related_name="profile")

        user.profile        # a Profile or None
    """

    single_reverse = True

    def contribute_to_class(self, owner) -> None:
        column_field = owner._fields.get(self.column)
        if column_field is not None and not (column_field.unique or column_field.primary_key):
            raise ValueError(f"{owner.__name__}.{self.name}: OneToOneField needs its column '{self.column}' declared with unique=True")
        super().contribute_to_class(owner)


class ManyToManyField(Field):
    """
    A many-to-many relationship through a table you define yourself as an ordinary model (so it's
    created by your migrations like any other table, and can carry extra columns):

        class PostTag(BaseModel):
            table_name = "post_tags"
            timestamps = False
            id = AutoField()
            post_id = IntegerField()
            tag_id = IntegerField()
            unique_pair = UniqueTogether(columns=["post_id", "tag_id"])

        class Post(BaseModel):
            ...
            tags = ManyToManyField(Tag, through=PostTag, source_column="post_id", target_column="tag_id",
                                   related_name="posts")

        post.tags                  # a QuerySet of Tag - filter()/order_by()/count() as usual
        post.tags.add(tag_a, tag_b)
        post.tags.remove(tag_a)
        post.tags.set([tag_b, tag_c])
        post.tags.clear()
        tag.posts                  # the reverse side, same API

    to                the related model class (or its table name)
    through           the join table's model class (or its table name)
    source_column     the join-table column holding THIS model's key
    target_column     the join-table column holding the related model's key
    This field has no column of its own, so it never appears in migrations.
    """

    is_virtual = True
    is_relation = True

    def __init__(self, to: Any, through: Any, source_column: str, target_column: str, related_name: Optional[str] = None):
        super().__init__()
        self.to = to
        self.through = through
        self.source_column = source_column
        self.target_column = target_column
        self.related_name = related_name

    @property
    def through_table(self) -> str:
        return self.through if isinstance(self.through, str) else self.through.table_name

    @property
    def references_table(self) -> str:
        return self.to if isinstance(self.to, str) else self.to.table_name

    def related_model(self):
        if not isinstance(self.to, str):
            return self.to
        from core.models.BaseModel import BaseModel

        model = BaseModel._registry.get(self.to)
        if model is None:
            raise LookupError(f"ManyToManyField '{self.name}': no model for table '{self.to}' has been imported yet")
        return model

    def target_model_if_ready(self):
        try:
            return self.related_model()
        except LookupError:
            return None

    def to_sql(self) -> str:
        raise RuntimeError("ManyToManyField has no column or constraint of its own")

    def migration_key(self) -> str:
        return f"m2m_{self.name}"

    def contribute_to_class(self, owner) -> None:
        from core.models.relations import connect_reverse

        self.owner = owner
        connect_reverse(self, owner)

    def __get__(self, instance, owner):
        if instance is None:
            return self
        from core.models.relations import many_related_queryset

        return many_related_queryset(self, instance, forward=True)

    def __set__(self, instance, value) -> None:
        raise AttributeError(f"'{self.name}' is a many-to-many relation: use .add(), .remove(), .set() or .clear()")


class UniqueTogether(Field):
    """Table-level composite UNIQUE KEY across several columns."""

    is_constraint = True

    def __init__(self, columns: List[str], name: Optional[str] = None):
        super().__init__()
        self.columns = list(columns)
        self.constraint_name = name or f"uniq_{'_'.join(columns)}"

    def migration_key(self) -> str:
        return f"unique_{self.constraint_name}"

    def to_sql(self) -> str:
        return f"UNIQUE KEY {self.constraint_name} ({', '.join(self.columns)})"
