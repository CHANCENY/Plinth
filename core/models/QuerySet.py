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

Every chaining method returns a NEW QuerySet and leaves the original
untouched, so a base queryset can be reused safely. Nothing hits the database
until a terminal call: iteration, first(), last(), count(), exists(),
aggregate(), update(), delete(), create(), get_or_create(). (This is also why a
QuerySet doesn't hold a query builder: db.connect() returns one shared builder
whose state .table() resets, so the builder is only borrowed for the instant a
query actually executes.)

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

`pk` stands for the primary key. Values are converted by the field type
(Decimal, UUID, date, JSON, ... - see core/database/fields.py), and a model
instance can be used where a relation is expected: filter(owner=user).

Beyond plain lookups
    OR / NOT      filter(Q(a=1) | Q(b=2)), exclude(Q(status="x")), filter(~Q(...))
    column maths  filter(price__gt=F("cost") * 2),  update(stock=F("stock") - 1)
    relations     filter(owner__name="Ada"), order_by("owner__name"), select_related("owner")
                  - follows ForeignKey / OneToOneField forwards, any depth ("file__owner__name")
    columns       only("a", "b"), defer("big_blob")     (the rest load on first access)
    dicts/tuples  values("a", "owner__name"), values_list("id", flat=True), distinct()
    aggregates    aggregate(total=Sum("amount"), n=Count("id"))
                  values("bundle").annotate(n=Count("mid")).filter(n__gt=1).order_by("-n")
    none()        an always-empty QuerySet

Differences from Django worth knowing:
  - exclude(a=1, b=2) is plain SQL negation of each lookup ANDed together, so rows
    where a column is NULL are NOT returned by exclude(field=x) (Django adds extra
    SQL to include them). exclude(Q(...)) negates the whole group.
  - contains/startswith/endswith use SQL LIKE, whose case sensitivity depends
    on the database (MySQL/SQLite: insensitive for ASCII by default;
    Postgres: sensitive). icontains is case-insensitive on all three.
  - Lookups can follow a relation forwards (ForeignKey/OneToOne) but not backwards
    or through many-to-many: filter the related model and use __in instead.
  - update()/delete() can't be combined with a relation lookup; use pk__in.
  - annotate() needs values() first (it groups by those fields); aggregates can't
    contain literal values; there is no prefetch_related().
  - Field names must be plain identifiers; anything else raises ValueError,
    so a kwargs key can never smuggle SQL into a query.
  - Bulk update()/delete() don't run model save() logic (no updated_at stamp),
    like Django, and refuse to run with no filter unless allow_all=True.
"""

import copy
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from core.database.expressions import Aggregate, Count, Expression, Q, adapt_value, is_expression
from core.database.query_builders.BaseQueryBuilder import WhereCollector

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LOOKUPS = {
    "exact", "ne", "gt", "gte", "lt", "lte", "in",
    "contains", "icontains", "startswith", "endswith", "isnull",
}
# OFFSET without LIMIT is a syntax error on MySQL/SQLite; this is the largest value all three accept
MAX_LIMIT = 9223372036854775807

Condition = Tuple[str, str, Any, bool]  # (field path, lookup, value, negated)
COMPARISONS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
OPPOSITES = {"gt": "<=", "gte": "<", "lt": ">=", "lte": ">"}


class _Join:
    """One LEFT JOIN added to follow a forward relation (alias j1, j2, ... so a table can be joined to itself)."""

    __slots__ = ("path", "alias", "parent_alias", "table", "model", "field")

    def __init__(self, path, alias, parent_alias, model, field):
        self.path = path
        self.alias = alias
        self.parent_alias = parent_alias
        self.table = model.table_name
        self.model = model
        self.field = field


class QuerySet:
    def __init__(self, db_connection, model_class):
        self.db = db_connection
        self.model = model_class
        self._conditions: List[Any] = []        # leaf Conditions, or Q trees whose leaves are Conditions
        self._havings: List[Any] = []           # conditions on annotations
        self._ordering: List[Tuple[str, str]] = []
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._joins: Dict[Tuple[str, ...], _Join] = {}
        self._select_related: List[Tuple[str, ...]] = []
        self._only: Optional[List[str]] = None
        self._defer: List[str] = []
        self._values: Optional[List[str]] = None
        self._values_list = False
        self._flat = False
        self._annotations: Dict[str, Expression] = {}
        self._distinct = False
        self._none = False
        self._create_defaults: Dict[str, Any] = {}  # applied by create(); set on reverse-relation querysets

    # ------------------------------------------------------------------ #
    # chaining - each returns a new QuerySet
    # ------------------------------------------------------------------ #
    def _clone(self) -> "QuerySet":
        clone = copy.copy(self)  # copy.copy keeps the subclass (e.g. the many-to-many queryset) and its extras
        clone._conditions = list(self._conditions)
        clone._havings = list(self._havings)
        clone._ordering = list(self._ordering)
        clone._joins = dict(self._joins)
        clone._select_related = list(self._select_related)
        clone._only = None if self._only is None else list(self._only)
        clone._defer = list(self._defer)
        clone._values = None if self._values is None else list(self._values)
        clone._annotations = dict(self._annotations)
        clone._create_defaults = dict(self._create_defaults)
        return clone

    def all(self) -> "QuerySet":
        return self._clone()

    def none(self) -> "QuerySet":
        """A QuerySet that never matches anything (and never touches the database)."""
        clone = self._clone()
        clone._none = True
        return clone

    def filter(self, *q_objects: Q, **lookups: Any) -> "QuerySet":
        clone = self._clone()
        for q in q_objects:
            clone._conditions.append(clone._parse_q(self._as_q(q)))
        for condition in clone._parse_lookups(lookups, negated=False):
            clone._add_condition(condition)
        return clone

    def exclude(self, *q_objects: Q, **lookups: Any) -> "QuerySet":
        clone = self._clone()
        for q in q_objects:
            negated = Q()
            negated.children = [clone._parse_q(self._as_q(q))]
            negated.negated = True
            clone._conditions.append(negated)
        for condition in clone._parse_lookups(lookups, negated=True):
            clone._add_condition(condition)
        return clone

    @staticmethod
    def _as_q(value: Any) -> Q:
        if not isinstance(value, Q):
            raise TypeError(f"QuerySet: positional arguments to filter()/exclude() must be Q objects, got {type(value).__name__}")
        return value

    def _add_condition(self, condition: Condition) -> None:
        (self._havings if condition[0] in self._annotations else self._conditions).append(condition)

    def order_by(self, *fields: str) -> "QuerySet":
        """order_by("name") ascending, order_by("-created_at") descending, order_by("owner__name") across a
        relation, order_by("-n") by an annotation. Replaces any earlier ordering."""
        clone = self._clone()
        clone._ordering = []
        for field in fields:
            direction = "DESC" if field.startswith("-") else "ASC"
            path = field.lstrip("-")
            if path not in clone._annotations:
                clone._resolve(path)  # validates the name and registers any join it needs
            clone._ordering.append((path, direction))
        return clone

    def limit(self, n: int) -> "QuerySet":
        clone = self._clone()
        clone._limit = int(n)
        return clone

    def offset(self, n: int) -> "QuerySet":
        clone = self._clone()
        clone._offset = int(n)
        return clone

    def distinct(self) -> "QuerySet":
        clone = self._clone()
        clone._distinct = True
        return clone

    def select_related(self, *paths: str) -> "QuerySet":
        """Load the named forward relations in the same query (LEFT JOIN) instead of one extra query per row:
        select_related("owner", "stored_file__owner")."""
        clone = self._clone()
        for path in paths:
            clone._resolve(f"{path}__pk")  # validates every step is a relation and registers the joins
            parts = tuple(path.split("__"))
            for depth in range(1, len(parts) + 1):
                if parts[:depth] not in clone._select_related:
                    clone._select_related.append(parts[:depth])
        return clone

    def only(self, *names: str) -> "QuerySet":
        """Load just these columns (plus the primary key); any other is fetched the first time it's read."""
        clone = self._clone()
        clone._check_columns(names)
        clone._only = list(names)
        return clone

    def defer(self, *names: str) -> "QuerySet":
        """Skip these columns for now (big text/blob columns); each is fetched the first time it's read."""
        clone = self._clone()
        clone._check_columns(names)
        clone._defer.extend(names)
        return clone

    def values(self, *names: str) -> "QuerySet":
        """Dicts instead of model instances: values("id", "owner__name"). No names = every column."""
        clone = self._clone()
        clone._values = list(names) if names else list(self.model._column_names)
        if not clone._values:
            raise ValueError("QuerySet: values() needs field names for a model with no declared fields")
        for name in clone._values:
            clone._resolve(name)
        clone._values_list = False
        return clone

    def values_list(self, *names: str, flat: bool = False) -> "QuerySet":
        """Tuples instead of instances: values_list("id", "name"); flat=True with one name gives plain values."""
        if flat and len(names) != 1:
            raise ValueError("QuerySet: values_list(flat=True) takes exactly one field name")
        clone = self.values(*names)
        clone._values_list = True
        clone._flat = flat
        return clone

    def annotate(self, **aggregates: Expression) -> "QuerySet":
        """Adds aggregates to a values() queryset, grouped by the values() fields:
        values("bundle").annotate(n=Count("mid")). Filter on them with filter(n__gt=1), order with order_by("-n")."""
        if not self._values:
            raise ValueError("QuerySet: annotate() needs values(<field>, ...) first - it groups by those fields")
        clone = self._clone()
        for alias, expression in aggregates.items():
            clone._check_identifier(alias)
            if alias in clone._values or alias in clone.model._column_names:
                raise ValueError(f"QuerySet: annotation '{alias}' clashes with a field name")
            if not isinstance(expression, Expression):
                raise TypeError(f"QuerySet: annotate({alias}=...) needs an aggregate such as Count()/Sum(), got {type(expression).__name__}")
            clone._annotations[alias] = expression
        return clone

    # ------------------------------------------------------------------ #
    # names, paths and joins
    # ------------------------------------------------------------------ #
    @staticmethod
    def _check_identifier(name: str) -> str:
        if not IDENTIFIER_RE.match(name):
            raise ValueError(f"QuerySet: '{name}' is not a valid field name")
        return name

    def _check_columns(self, names) -> None:
        if self._values is not None:
            raise ValueError("QuerySet: only()/defer() don't apply to values() querysets")
        known = self.model._column_names
        if not known:
            raise ValueError(f"QuerySet: only()/defer() need {self.model.__name__} to declare its fields")
        for name in names:
            if name not in known:
                raise ValueError(f"QuerySet: '{name}' is not a column of {self.model.__name__}")

    def _resolve(self, path: str):
        """
        "owner__name" -> (sql column, field, model, relation). Registers the LEFT JOINs needed to
        follow relations. `relation` is the ForeignKey when the path ends on a relation itself
        (filter(owner=user) compares the key column).
        """
        parts = path.split("__")
        model = self.model
        qualifier = model.table_name
        for index, part in enumerate(parts):
            self._check_identifier(part)
            if part == "pk":
                part = model.primary_key
            field = model._fields.get(part)
            last = index == len(parts) - 1

            if field is not None and field.is_virtual:
                raise ValueError(
                    f"QuerySet: '{part}' is a many-to-many relation - filter the related model and use __in instead"
                )
            if field is not None and field.is_relation:  # a forward ForeignKey / OneToOneField
                if last:
                    return f"{qualifier}.{field.column}", model._fields.get(field.column), model, field
                key = tuple(parts[: index + 1])
                join = self._joins.get(key)
                if join is None:
                    join = _Join(key, f"j{len(self._joins) + 1}", qualifier, field.related_model(), field)
                    self._joins[key] = join
                model, qualifier = join.model, join.alias
                continue

            if not last:
                raise ValueError(
                    f"QuerySet: can't follow '{part}' on {model.__name__} - lookups can follow forward "
                    f"ForeignKey/OneToOne relations only"
                )
            return f"{qualifier}.{part}", field, model, None
        raise ValueError("QuerySet: empty field name")

    def _column_sql(self, name: str) -> str:
        return self._resolve(name)[0]

    # ------------------------------------------------------------------ #
    # lookups -> conditions
    # ------------------------------------------------------------------ #
    def _parse_lookups(self, lookups: Dict[str, Any], negated: bool) -> List[Condition]:
        parsed = []
        for key, value in lookups.items():
            field, sep, lookup = key.rpartition("__")
            if not sep or lookup not in LOOKUPS:
                field, lookup = key, "exact"  # no (or unknown) suffix - the whole key is the field name
            if field in self._annotations:
                if lookup in ("contains", "icontains", "startswith", "endswith", "isnull"):
                    raise ValueError(f"QuerySet: '{key}' - annotations support exact/ne/gt/gte/lt/lte/in")
            else:
                self._resolve(field)  # validates, and registers joins on this (cloned) queryset
            if lookup == "in" and isinstance(value, (str, bytes)):
                raise ValueError(f"QuerySet: '{key}' needs a list/tuple, got a string")
            parsed.append((field, lookup, value, negated))
        return parsed

    def _parse_q(self, q: Q) -> Q:
        """A copy of a Q tree whose leaves are parsed Conditions (so joins are registered on this queryset)."""
        parsed = Q()
        parsed.connector = q.connector
        parsed.negated = q.negated
        for child in q.children:
            if isinstance(child, Q):
                parsed.children.append(self._parse_q(child))
            elif isinstance(child, tuple) and len(child) == 2:
                for condition in self._parse_lookups({child[0]: child[1]}, negated=False):
                    if condition[0] in self._annotations:
                        raise ValueError("QuerySet: annotations can't be filtered inside a Q object - use filter(alias__gt=...)")
                    parsed.children.append(condition)
            else:
                raise TypeError(f"QuerySet: unexpected {child!r} in a Q object")
        return parsed

    @staticmethod
    def _escape_like(text: str) -> str:
        # '!' is the escape character (see BaseQueryBuilder.where_like)
        return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")

    def _to_db(self, value: Any, field, relation, model) -> Any:
        """A lookup value -> what gets bound: model instances become their key, fields convert their type."""
        from core.models.BaseModel import BaseModel

        if value is None or is_expression(value):
            return value
        if isinstance(value, BaseModel):
            key_name = relation.references_column if relation is not None else value.primary_key
            return value.attributes.get(key_name)
        if field is not None and not field.is_constraint and not field.is_virtual:
            return field.to_db(value)
        return adapt_value(value)

    def _compile(self, conditions: List[Any], placeholder: str, having: bool = False) -> List[Tuple[str, Any, str, Any]]:
        collector = WhereCollector(placeholder)
        for condition in conditions:
            self._apply_condition(collector, condition, having)
        return collector._wheres

    def _apply_q(self, target, q: Q, having: bool) -> None:
        group = WhereCollector(target.placeholder)
        for child in q.children:
            part = WhereCollector(target.placeholder)
            if isinstance(child, Q):
                self._apply_q(part, child, having)
            else:
                self._apply_condition(part, child, having)
            if part._wheres:
                group._wheres.append((q.connector, None, "GROUP", part._wheres))
        if group._wheres:
            target.where_group(group._wheres, negate=q.negated)

    def _apply_condition(self, target, condition: Any, having: bool = False) -> None:
        if isinstance(condition, Q):
            return self._apply_q(target, condition, having)

        path, lookup, value, negated = condition

        if path == "__m2m__":  # many-to-many membership, added by the related queryset
            key_sql, through, near, far, near_value = value
            subquery = f"{key_sql} IN (SELECT {far} FROM {through} WHERE {near} = {target.placeholder})"
            return target.where_raw(f"NOT ({subquery})" if negated else subquery, [near_value])

        field = relation = model = None
        if path in self._annotations:
            column, bindings = self._annotations[path].as_sql(target.placeholder, self._column_sql)
            if bindings:
                raise ValueError("QuerySet: an annotation containing literal values can't be filtered")
        else:
            column, field, model, relation = self._resolve(path)

        if lookup == "in":
            value = [self._to_db(item, field, relation, model) for item in value]
        elif lookup not in ("contains", "icontains", "startswith", "endswith", "isnull"):
            value = self._to_db(value, field, relation, model)

        if lookup in ("exact", "ne"):
            equals = (lookup == "exact") != negated  # True -> "=", False -> "!="
            if value is None:
                (target.where_null if equals else target.where_not_null)(column)
            elif is_expression(value):
                self._compare_expression(target, column, "=" if equals else "!=", value)
            else:
                target.where(column, "=" if equals else "!=", value)

        elif lookup in COMPARISONS:
            operator = OPPOSITES[lookup] if negated else COMPARISONS[lookup]
            if is_expression(value):
                self._compare_expression(target, column, operator, value)
            else:
                target.where(column, operator, value)

        elif lookup == "in":
            (target.where_not_in if negated else target.where_in)(column, list(value))

        elif lookup in ("contains", "startswith", "endswith"):
            text = self._escape_like(str(value))
            pattern = {"contains": f"%{text}%", "startswith": f"{text}%", "endswith": f"%{text}"}[lookup]
            target.where_like(column, pattern, negate=negated)

        elif lookup == "icontains":
            # LOWER() on both sides: LIKE alone is case-sensitive on Postgres
            pattern = f"%{self._escape_like(str(value)).lower()}%"
            target.where_like(f"LOWER({column})", pattern, negate=negated)

        elif lookup == "isnull":
            wants_null = bool(value) != negated
            (target.where_null if wants_null else target.where_not_null)(column)

    def _compare_expression(self, target, column: str, operator: str, expression: Expression) -> None:
        sql, bindings = expression.as_sql(target.placeholder, self._column_sql)
        target.where_raw(f"{column} {operator} {sql}", bindings)

    # ------------------------------------------------------------------ #
    # execution
    # ------------------------------------------------------------------ #
    def _builder(self):
        builder = self.db.connect().table(self.model.table_name)
        for join in self._joins.values():
            builder.left_join(
                f"{join.table} AS {join.alias}",
                f"{join.alias}.{join.field.references_column}",
                "=",
                f"{join.parent_alias}.{join.field.column}",
            )
        builder._wheres.extend(self._compile(self._conditions, builder.placeholder))
        if self._havings:
            builder.having(self._compile(self._havings, builder.placeholder, having=True))
        if self._distinct:
            builder.distinct()
        return builder

    def _is_sliced(self) -> bool:
        return self._limit is not None or self._offset is not None

    def _loaded_columns(self) -> Optional[List[str]]:
        """Column names to SELECT for only()/defer(), or None for all of them."""
        if self._only is None and not self._defer:
            return None
        columns = self.model._column_names
        pk = self.model.primary_key
        if self._only is not None:
            wanted = [pk] + [name for name in self._only if name != pk]
            return [name for name in columns if name in wanted]
        return [name for name in columns if name == pk or name not in self._defer]

    def _select(self, builder) -> None:
        table = self.model.table_name

        if self._values is not None:
            columns = [f"{self._column_sql(name)} AS {name}" for name in self._values]
            for alias, expression in self._annotations.items():
                sql, bindings = expression.as_sql(builder.placeholder, self._column_sql)
                if bindings:
                    raise ValueError("QuerySet: an annotation containing literal values isn't supported")
                columns.append(f"{sql} AS {alias}")
            builder.select(*columns)
            if self._annotations:
                builder.group_by(*[self._column_sql(name) for name in self._values])
            return

        loaded = self._loaded_columns()
        if loaded is not None:
            columns = [f"{table}.{name}" for name in loaded]
        else:
            columns = [f"{table}.*"] if self._joins else ["*"]
        for path in self._select_related:
            join = self._joins[path]
            columns.extend(f"{join.alias}.{name} AS {join.alias}__{name}" for name in join.model._column_names)
        builder.select(*columns)

    def _rows(self) -> List[Any]:
        if self._none:
            return []
        builder = self._builder()
        self._select(builder)
        for path, direction in self._ordering:
            builder.order_by(path if path in self._annotations else self._column_sql(path), direction)
        limit = self._limit
        if self._offset is not None:
            limit = MAX_LIMIT if limit is None else limit
        if limit is not None:
            builder.limit(limit)
        if self._offset is not None:
            builder.offset(self._offset)
        return builder.get()

    def _build_instance(self, row: Any):
        data = dict(row)
        selected = {self._joins[path].alias: path for path in self._select_related}
        main: Dict[str, Any] = {}
        related: Dict[str, Dict[str, Any]] = {alias: {} for alias in selected}
        for key, value in data.items():
            alias, sep, column = key.partition("__")
            if sep and alias in related:
                related[alias][column] = value
            else:
                main[key] = value

        loaded = self._loaded_columns()
        deferred = None if loaded is None else set(self.model._column_names) - set(loaded)
        instance = self.model._from_row(self.db, main, deferred=deferred)

        built: Dict[Tuple[str, ...], Any] = {(): instance}
        for path in sorted(self._select_related, key=len):
            join = self._joins[path]
            values = related[join.alias]
            obj = None if all(v is None for v in values.values()) else join.model._from_row(self.db, values)
            built[path] = obj
            parent = built.get(path[:-1])
            if parent is not None:
                cache = parent.__dict__.setdefault("_relation_cache", {})
                cache[join.field.name] = (parent.attributes.get(join.field.column), obj)
        return instance

    def _value_row(self, row: Any):
        data = dict(row)
        converted: Dict[str, Any] = {}
        for name in self._values:
            value = data.get(name)
            field = self._resolve(name)[1]
            if value is not None and field is not None and not field.is_constraint:
                value = field.to_python(value)
            converted[name] = value
        for alias, expression in self._annotations.items():
            converted[alias] = self._convert_aggregate(expression, data.get(alias))
        if not self._values_list:
            return converted
        if self._flat:
            return converted[self._values[0]]
        return tuple(converted.values())

    def _convert_aggregate(self, expression: Expression, value: Any) -> Any:
        if value is None or not isinstance(expression, Aggregate):
            return value
        if isinstance(expression, Count):
            return int(value)
        name = expression.source_name
        if name is None:
            return value
        field = self._resolve(name)[1]
        if field is None or field.is_constraint:
            return value
        if expression.function == "AVG":
            return field.to_python(value) if type(field).__name__ == "DecimalField" else float(value)
        if expression.function == "SUM" and type(field).__name__ in ("IntegerField", "SmallIntegerField", "BigIntegerField"):
            return int(value)  # MySQL returns SUM(int) as Decimal
        return field.to_python(value)

    def _instances(self) -> List[Any]:
        rows = self._rows()
        if self._values is not None:
            return [self._value_row(row) for row in rows]
        return [self._build_instance(row) for row in rows]

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
        ordered = self if (self._ordering or self._annotations or self._distinct) else self.order_by(self.model.primary_key)
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
        if self._none:
            return 0
        if self._is_sliced():
            return len(self._rows())
        if self._distinct or self._annotations:
            builder = self._builder()
            self._select(builder)
            inner, bindings = builder.to_sql()
            rows = builder.raw(f"SELECT COUNT(*) AS count FROM ({inner}) AS counted", bindings)
            return self._first_value(rows[0]) if rows else 0
        return self._builder().count()

    @staticmethod
    def _first_value(row: Any) -> Any:
        try:
            return row["count"]
        except (TypeError, KeyError, IndexError):
            return row[0]

    def exists(self) -> bool:
        return len(self[:1]._rows()) > 0

    def aggregate(self, **aggregates: Expression) -> Dict[str, Any]:
        """One row of aggregates over the whole queryset: aggregate(total=Sum("amount"), n=Count("id")) -> {...}."""
        if self._is_sliced():
            raise ValueError("QuerySet: cannot aggregate() a sliced QuerySet")
        if not aggregates:
            raise ValueError("QuerySet: aggregate() needs at least one aggregate, e.g. aggregate(n=Count('id'))")
        if self._none:
            return {alias: (0 if isinstance(e, Count) else None) for alias, e in aggregates.items()}
        builder = self._builder()
        builder._distinct = False
        columns = []
        for alias, expression in aggregates.items():
            self._check_identifier(alias)
            if not isinstance(expression, Expression):
                raise TypeError(f"QuerySet: aggregate({alias}=...) needs Count()/Sum()/Avg()/Min()/Max()")
            sql, bindings = expression.as_sql(builder.placeholder, self._column_sql)
            if bindings:
                raise ValueError("QuerySet: aggregates containing literal values aren't supported")
            columns.append(f"{sql} AS {alias}")
        builder.select(*columns)
        rows = builder.get()
        data = dict(rows[0]) if rows else {}
        return {alias: self._convert_aggregate(expression, data.get(alias)) for alias, expression in aggregates.items()}

    # ------------------------------------------------------------------ #
    # writes
    # ------------------------------------------------------------------ #
    def _guard_bulk(self, action: str, allow_all: bool) -> None:
        if self._is_sliced():
            raise ValueError(f"QuerySet: cannot {action} a sliced QuerySet")
        if self._joins:
            raise ValueError(f"QuerySet: cannot {action}() across a relation - filter by key instead, e.g. pk__in=[...]")
        if not self._conditions and not self._havings and not allow_all:
            raise ValueError(
                f"QuerySet: {action}() with no filter would affect every row in "
                f"'{self.model.table_name}' - add a filter(), or pass allow_all=True if that's intended"
            )

    def _prepare_write(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """Field-name keys -> column names, values converted for the database (relations accept instances)."""
        from core.models.BaseModel import BaseModel

        prepared: Dict[str, Any] = {}
        for name, value in values.items():
            self._check_identifier(name)
            field = self.model._fields.get(name)
            if field is not None and field.is_relation and not field.is_virtual:
                column = field.column
                if isinstance(value, BaseModel):
                    value = value.attributes.get(field.references_column)
                field = self.model._fields.get(column)
            else:
                column = name
            if value is None or is_expression(value):
                prepared[column] = value
            elif field is not None and not field.is_constraint and not field.is_virtual:
                prepared[column] = field.to_db(value)
            else:
                prepared[column] = adapt_value(value)
        return prepared

    def update(self, allow_all: bool = False, **values: Any) -> int:
        """Bulk UPDATE of every matching row, returns the number of rows changed. Does not stamp updated_at.
        Values may be expressions: update(stock=F("stock") - 1)."""
        self._guard_bulk("update", allow_all)
        if not values:
            return 0
        return self._builder().update(self._prepare_write(values))

    def delete(self, allow_all: bool = False) -> int:
        """Bulk (hard) DELETE of every matching row, returns the number of rows removed."""
        self._guard_bulk("delete", allow_all)
        return self._builder().delete()

    def create(self, **values: Any):
        """Builds the model from `values`, saves it, and returns it. Filters on this queryset are ignored
        (but a reverse-relation queryset like user.files fills in its own foreign key)."""
        instance = self.model(self.db, **{**self._create_defaults, **values})
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
        prepared = self._prepare_write(values)
        return self.db.connect().table(self.model.table_name).insert(prepared, primary_key=self.model.primary_key)
