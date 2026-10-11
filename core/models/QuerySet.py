"""
QuerySet
----------
Django-style, lazy, chainable queries that return model instances instead of
raw rows. Reached through a model's `objects()` entry point:

    User.objects(db).filter(status="active").order_by("-created_at").all()
    User.objects(db).filter(email__icontains="@example.com").count()
    User.objects(db).exclude(status__in=["deleted", "blocked"]).first()
    OAuthIdentity.objects(db).filter(provider="google", provider_user_id="123").first()

Why `objects(db)` and not Django's bare `objects`: Django keeps one global
database connection, so `User.objects` needs no arguments. This framework
passes DatabaseConnection explicitly everywhere (ControllerBase.db(),
services, models) - there is no global - so the connection is handed in at
the entry point instead. Everything after that reads the same as Django.

Every chaining method (filter/exclude/order_by/limit/offset/all) returns a NEW
QuerySet and leaves the original untouched, so a base queryset can be reused
safely. Nothing hits the database until a terminal call: iteration, first(),
last(), count(), exists(), update(), delete(), create(), get_or_create().
(This is also why a QuerySet doesn't hold a query builder: db.connect()
returns one shared builder whose state .table() resets, so the builder is only
borrowed for the instant a query actually executes.)

Field lookups - the same double-underscore convention as Django:

    field=value              equals (field=None means IS NULL)
    field__ne=value          not equals
    field__gt / __gte        greater than / greater than or equal
    field__lt / __lte        less than / less than or equal
    field__in=[...]          in a list (an empty list matches nothing)
    field__contains=text     LIKE %text%        (literal %, _ in text are escaped)
    field__icontains=text    case-insensitive contains
    field__startswith=text   LIKE text%
    field__endswith=text     LIKE %text
    field__isnull=True|False IS NULL / IS NOT NULL

Differences from Django worth knowing:
  - exclude() is plain SQL negation, so rows where the column is NULL are NOT
    returned by exclude(field=x) (Django adds extra SQL to include them).
  - contains/startswith/endswith use SQL LIKE, whose case sensitivity depends
    on the database (MySQL/SQLite: insensitive for ASCII by default;
    Postgres: sensitive). icontains is case-insensitive on all three.
  - No relationships/joins/Q objects/F expressions/annotations - conditions are
    ANDed together. For anything beyond this, use db.connect() directly.
  - Field names must be plain identifiers; anything else raises ValueError,
    so a kwargs key can never smuggle SQL into a query.
  - Bulk update()/delete() don't run model save() logic (no updated_at stamp),
    like Django, and refuse to run with no filter unless allow_all=True.
"""

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LOOKUPS = {
    "exact", "ne", "gt", "gte", "lt", "lte", "in",
    "contains", "icontains", "startswith", "endswith", "isnull",
}
# OFFSET without LIMIT is a syntax error on MySQL/SQLite; this is the largest value all three accept
MAX_LIMIT = 9223372036854775807

Condition = Tuple[str, str, Any, bool]  # (column, lookup, value, negated)


class QuerySet:
    def __init__(self, db_connection, model_class):
        self.db = db_connection
        self.model = model_class
        self._conditions: List[Condition] = []
        self._ordering: List[Tuple[str, str]] = []
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None

    # ------------------------------------------------------------------ #
    # chaining - each returns a new QuerySet
    # ------------------------------------------------------------------ #
    def _clone(self) -> "QuerySet":
        clone = QuerySet(self.db, self.model)
        clone._conditions = list(self._conditions)
        clone._ordering = list(self._ordering)
        clone._limit = self._limit
        clone._offset = self._offset
        return clone

    def all(self) -> "QuerySet":
        return self._clone()

    def filter(self, **lookups: Any) -> "QuerySet":
        clone = self._clone()
        clone._conditions.extend(self._parse_lookups(lookups, negated=False))
        return clone

    def exclude(self, **lookups: Any) -> "QuerySet":
        clone = self._clone()
        clone._conditions.extend(self._parse_lookups(lookups, negated=True))
        return clone

    def order_by(self, *fields: str) -> "QuerySet":
        """order_by("name") ascending, order_by("-created_at") descending. Replaces any earlier ordering."""
        clone = self._clone()
        clone._ordering = []
        for field in fields:
            direction = "DESC" if field.startswith("-") else "ASC"
            clone._ordering.append((self._check_identifier(field.lstrip("-")), direction))
        return clone

    def limit(self, n: int) -> "QuerySet":
        clone = self._clone()
        clone._limit = int(n)
        return clone

    def offset(self, n: int) -> "QuerySet":
        clone = self._clone()
        clone._offset = int(n)
        return clone

    # ------------------------------------------------------------------ #
    # lookups -> conditions
    # ------------------------------------------------------------------ #
    @staticmethod
    def _check_identifier(name: str) -> str:
        if not IDENTIFIER_RE.match(name):
            raise ValueError(f"QuerySet: '{name}' is not a valid field name")
        return name

    def _parse_lookups(self, lookups: Dict[str, Any], negated: bool) -> List[Condition]:
        parsed = []
        for key, value in lookups.items():
            field, sep, lookup = key.rpartition("__")
            if not sep or lookup not in LOOKUPS:
                field, lookup = key, "exact"  # no (or unknown) suffix - the whole key is the field name
            self._check_identifier(field)
            if lookup == "in" and isinstance(value, (str, bytes)):
                raise ValueError(f"QuerySet: '{key}' needs a list/tuple, got a string")
            parsed.append((field, lookup, value, negated))
        return parsed

    @staticmethod
    def _escape_like(text: str) -> str:
        # '!' is the escape character (see BaseQueryBuilder.where_like)
        return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")

    def _apply_condition(self, builder, condition: Condition) -> None:
        column, lookup, value, negated = condition

        if lookup in ("exact", "ne"):
            equals = (lookup == "exact") != negated  # True -> "=", False -> "!="
            if value is None:
                (builder.where_null if equals else builder.where_not_null)(column)
            else:
                builder.where(column, "=" if equals else "!=", value)

        elif lookup in ("gt", "gte", "lt", "lte"):
            operators = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
            opposite = {"gt": "<=", "gte": "<", "lt": ">=", "lte": ">"}
            builder.where(column, opposite[lookup] if negated else operators[lookup], value)

        elif lookup == "in":
            (builder.where_not_in if negated else builder.where_in)(column, list(value))

        elif lookup in ("contains", "startswith", "endswith"):
            text = self._escape_like(str(value))
            pattern = {"contains": f"%{text}%", "startswith": f"{text}%", "endswith": f"%{text}"}[lookup]
            builder.where_like(column, pattern, negate=negated)

        elif lookup == "icontains":
            # LOWER() on both sides: LIKE alone is case-sensitive on Postgres
            pattern = f"%{self._escape_like(str(value)).lower()}%"
            builder.where_like(f"LOWER({column})", pattern, negate=negated)

        elif lookup == "isnull":
            wants_null = bool(value) != negated
            (builder.where_null if wants_null else builder.where_not_null)(column)

    # ------------------------------------------------------------------ #
    # execution
    # ------------------------------------------------------------------ #
    def _builder(self):
        builder = self.db.connect().table(self.model.table_name)
        for condition in self._conditions:
            self._apply_condition(builder, condition)
        return builder

    def _is_sliced(self) -> bool:
        return self._limit is not None or self._offset is not None

    def _rows(self) -> List[Any]:
        builder = self._builder()
        for column, direction in self._ordering:
            builder.order_by(column, direction)
        limit = self._limit
        if self._offset is not None:
            limit = MAX_LIMIT if limit is None else limit
        if limit is not None:
            builder.limit(limit)
        if self._offset is not None:
            builder.offset(self._offset)
        return builder.get()

    def _instances(self) -> List[Any]:
        return [self.model._from_row(self.db, row) for row in self._rows()]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._instances())

    # no __len__ on purpose: list(qs) calls __len__ as a size hint, which would fire a
    # whole extra COUNT query before every iteration - use qs.count() when you want a count
    def __bool__(self) -> bool:
        return self.exists()

    def __getitem__(self, key: Union[int, slice]):
        """qs[0] -> one instance (IndexError if none); qs[:10] / qs[5:15] -> a sliced QuerySet."""
        if isinstance(key, slice):
            if key.step not in (None, 1):
                raise ValueError("QuerySet: slicing with a step is not supported")
            start = key.start or 0
            if start < 0 or (key.stop is not None and key.stop < 0):
                raise ValueError("QuerySet: negative slicing is not supported")
            clone = self._clone()
            clone._offset = (self._offset or 0) + start or None
            remaining = None if self._limit is None else max(self._limit - start, 0)  # room left in an existing slice
            if key.stop is not None:
                requested = max(key.stop - start, 0)
                clone._limit = requested if remaining is None else min(requested, remaining)
            else:
                clone._limit = remaining
            return clone
        if key < 0:
            raise ValueError("QuerySet: negative indexing is not supported")
        rows = self[key:key + 1]._instances()
        if not rows:
            raise IndexError("QuerySet index out of range")
        return rows[0]

    def first(self) -> Optional[Any]:
        """First instance (ordered by primary key if you haven't ordered), or None. Never raises."""
        ordered = self if self._ordering else self.order_by(self.model.primary_key)
        rows = ordered[:1]._instances()
        return rows[0] if rows else None

    def last(self) -> Optional[Any]:
        """Last instance (reverse of your ordering, or of the primary key), or None."""
        if self._is_sliced():
            raise ValueError("QuerySet: cannot call last() on a sliced QuerySet")
        if self._ordering:
            flipped = [(c, "ASC" if d == "DESC" else "DESC") for c, d in self._ordering]
        else:
            flipped = [(self.model.primary_key, "DESC")]
        clone = self._clone()
        clone._ordering = flipped
        rows = clone[:1]._instances()
        return rows[0] if rows else None

    def count(self) -> int:
        if self._is_sliced():
            return len(self._rows())
        return self._builder().count()

    def exists(self) -> bool:
        return len(self[:1]._rows()) > 0

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def _guard_bulk(self, action: str, allow_all: bool) -> None:
        if self._is_sliced():
            raise ValueError(f"QuerySet: cannot {action} a sliced QuerySet")
        if not self._conditions and not allow_all:
            raise ValueError(
                f"QuerySet: {action}() with no filter would affect every row in "
                f"'{self.model.table_name}' - add a filter(), or pass allow_all=True if that's intended"
            )

    def update(self, allow_all: bool = False, **values: Any) -> int:
        """Bulk UPDATE of every matching row, returns the number of rows changed. Does not stamp updated_at."""
        self._guard_bulk("update", allow_all)
        if not values:
            return 0
        for column in values:
            self._check_identifier(column)
        return self._builder().update(values)

    def delete(self, allow_all: bool = False) -> int:
        """Bulk (hard) DELETE of every matching row, returns the number of rows removed."""
        self._guard_bulk("delete", allow_all)
        return self._builder().delete()

    def create(self, **values: Any):
        """Builds the model from `values`, saves it, and returns it. Filters on this queryset are ignored."""
        instance = self.model(self.db, **values)
        instance.save()
        return instance

    def get_or_create(self, defaults: Optional[Dict[str, Any]] = None, **lookups: Any) -> Tuple[Any, bool]:
        """
        Returns (instance, created). Looks the row up by `lookups`; if there isn't one, creates it from the
        plain (no "__") lookups plus `defaults`. Not atomic - two concurrent callers can both miss and both
        insert; put a UNIQUE constraint on the lookup columns (as oauth_identities does) if that matters.
        """
        existing = self.filter(**lookups).first()
        if existing is not None:
            return existing, False
        values = {key: value for key, value in lookups.items() if "__" not in key}
        values.update(defaults or {})
        return self.create(**values), True

    def _insert_row(self, values: Dict[str, Any]) -> Any:
        """Low-level INSERT used by BaseModel.save(); returns the new primary key."""
        for column in values:
            self._check_identifier(column)
        return self.db.connect().table(self.model.table_name).insert(values, primary_key=self.model.primary_key)
