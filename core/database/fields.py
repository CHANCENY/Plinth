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
"""

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
        return instance.attributes.get(self.name)

    def __set__(self, instance, value) -> None:
        instance.attributes[self.name] = value

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


class CharField(Field):
    def __init__(self, max_length: int = 255, **kwargs):
        super().__init__(**kwargs)
        self.max_length = max_length

    def column_type(self) -> str:
        return f"VARCHAR({self.max_length})"


class TextField(Field):
    def column_type(self) -> str:
        return "TEXT"


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


# ---------------------------------------------------------------------- #
# table-level constraints - contribute a schema line but aren't columns:
# excluded from ALTER TABLE ADD COLUMN handling and from fillable/primary_key
# ---------------------------------------------------------------------- #
class ForeignKey(Field):
    """
    Wraps an existing column (declare that column normally, e.g. as an
    IntegerField) and adds a table-level FOREIGN KEY constraint for it:

        uid = IntegerField(null=True)
        uid_ref = ForeignKey(column="uid", references="users", references_column="uid")
    """

    is_constraint = True

    def __init__(self, column: str, references: str, references_column: str = "id"):
        super().__init__()
        self.column = column
        self.references = references
        self.references_column = references_column

    def migration_key(self) -> str:
        return f"fk_{self.column}"

    def to_sql(self) -> str:
        return f"FOREIGN KEY ({self.column}) REFERENCES {self.references}({self.references_column})"


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
