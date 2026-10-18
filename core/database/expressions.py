"""
expressions
-------------
Building blocks for queries that go beyond "column = value": OR / NOT conditions (Q), references to
other columns and arithmetic on them (F), and aggregates (Count, Sum, Avg, Min, Max). They're used
through QuerySet (core/models/QuerySet.py):

    from core.database.expressions import Q, F, Count, Sum

    # OR / NOT - combine with | & ~
    Note.objects(db).filter(Q(title__icontains="plan") | Q(body__icontains="plan"))
    Note.objects(db).filter(~Q(status="archived"), owner=user)

    # compare a column with another column, or update a column from itself
    Product.objects(db).filter(price__gt=F("cost") * 2)
    Product.objects(db).filter(pk=1).update(stock=F("stock") - 1)

    # aggregates
    Order.objects(db).aggregate(total=Sum("amount"), n=Count("id"))
    Media.objects(db).values("bundle").annotate(n=Count("mid")).order_by("-n")

Nothing here builds SQL on its own: every expression compiles through as_sql(placeholder, resolve),
where `resolve` turns a field name (which may cross a relation, "owner__name") into a column
reference. Values are always passed as bound parameters, never interpolated into the SQL text.
"""

import datetime
import decimal
import uuid
from typing import Any, Callable, List, Optional, Tuple

Resolver = Callable[[str], str]
Compiled = Tuple[str, List[Any]]


def adapt_value(value: Any) -> Any:
    """Makes a Python value bindable by every driver (sqlite3 can't bind Decimal/UUID/date directly)."""
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return value


def is_expression(value: Any) -> bool:
    return hasattr(value, "as_sql")


# ---------------------------------------------------------------------- #
# value expressions: F, Value, arithmetic
# ---------------------------------------------------------------------- #
class Expression:
    """Base class: anything that can appear on the right of a comparison or in a SET clause."""

    def as_sql(self, placeholder: str, resolve: Resolver) -> Compiled:
        raise NotImplementedError

    def _wrap(self, other: Any) -> "Expression":
        return other if isinstance(other, Expression) else Value(other)

    def __add__(self, other):
        return Combined(self, "+", self._wrap(other))

    def __radd__(self, other):
        return Combined(self._wrap(other), "+", self)

    def __sub__(self, other):
        return Combined(self, "-", self._wrap(other))

    def __rsub__(self, other):
        return Combined(self._wrap(other), "-", self)

    def __mul__(self, other):
        return Combined(self, "*", self._wrap(other))

    def __rmul__(self, other):
        return Combined(self._wrap(other), "*", self)

    def __truediv__(self, other):
        return Combined(self, "/", self._wrap(other))

    def __rtruediv__(self, other):
        return Combined(self._wrap(other), "/", self)


class F(Expression):
    """A reference to a column: F("stock"), or across a relation: F("owner__id")."""

    def __init__(self, name: str):
        self.name = name

    def as_sql(self, placeholder: str, resolve: Resolver) -> Compiled:
        return resolve(self.name), []

    def __repr__(self) -> str:
        return f"F({self.name!r})"


class Value(Expression):
    """A literal, always sent as a bound parameter."""

    def __init__(self, value: Any):
        self.value = value

    def as_sql(self, placeholder: str, resolve: Resolver) -> Compiled:
        return placeholder, [adapt_value(self.value)]


class Combined(Expression):
    """left <op> right, parenthesised so precedence is always what the Python expression says."""

    def __init__(self, left: Expression, operator: str, right: Expression):
        self.left, self.operator, self.right = left, operator, right

    def as_sql(self, placeholder: str, resolve: Resolver) -> Compiled:
        left_sql, left_bindings = self.left.as_sql(placeholder, resolve)
        right_sql, right_bindings = self.right.as_sql(placeholder, resolve)
        return f"({left_sql} {self.operator} {right_sql})", left_bindings + right_bindings


# ---------------------------------------------------------------------- #
# aggregates
# ---------------------------------------------------------------------- #
class Aggregate(Expression):
    """COUNT/SUM/AVG/MIN/MAX of a field (or of any expression, e.g. Sum(F("price") * F("qty")))."""

    function = ""

    def __init__(self, source: Any = "*", distinct: bool = False):
        self.source = source
        self.distinct = distinct

    @property
    def source_name(self) -> Optional[str]:
        """The field name when the source is a plain field - used to convert the result back to Python."""
        if isinstance(self.source, F):
            return self.source.name
        if isinstance(self.source, str) and self.source != "*":
            return self.source
        return None

    def as_sql(self, placeholder: str, resolve: Resolver) -> Compiled:
        if self.source == "*":
            inner, bindings = "*", []
        elif isinstance(self.source, Expression):
            inner, bindings = self.source.as_sql(placeholder, resolve)
        else:
            inner, bindings = resolve(str(self.source)), []
        distinct = "DISTINCT " if self.distinct else ""
        return f"{self.function}({distinct}{inner})", bindings


class Count(Aggregate):
    function = "COUNT"


class Sum(Aggregate):
    function = "SUM"

    def __init__(self, source: Any, distinct: bool = False):
        super().__init__(source, distinct)


class Avg(Aggregate):
    function = "AVG"

    def __init__(self, source: Any, distinct: bool = False):
        super().__init__(source, distinct)


class Min(Aggregate):
    function = "MIN"

    def __init__(self, source: Any):
        super().__init__(source)


class Max(Aggregate):
    function = "MAX"

    def __init__(self, source: Any):
        super().__init__(source)


# ---------------------------------------------------------------------- #
# Q - boolean combinations of lookups
# ---------------------------------------------------------------------- #
class Q:
    """
    A group of lookups that can be combined with | (OR), & (AND) and ~ (NOT):

        Q(status="active") | Q(role="admin")
        ~Q(email__endswith="@example.com")
        Q(a=1) & (Q(b=2) | Q(c=3))

    Pass Q objects positionally to filter()/exclude(); keyword lookups there are ANDed with them.
    Keyword lookups inside one Q are ANDed together.
    """

    AND = "AND"
    OR = "OR"

    def __init__(self, *children: Any, **lookups: Any):
        self.children: List[Any] = list(children) + list(lookups.items())
        self.connector = Q.AND
        self.negated = False

    def _combine(self, other: "Q", connector: str) -> "Q":
        if not isinstance(other, Q):
            raise TypeError(f"Q objects can only be combined with other Q objects, not {type(other).__name__}")
        combined = Q()
        combined.connector = connector
        combined.children = [self, other]
        return combined

    def __or__(self, other: "Q") -> "Q":
        return self._combine(other, Q.OR)

    def __and__(self, other: "Q") -> "Q":
        return self._combine(other, Q.AND)

    def __invert__(self) -> "Q":
        inverted = Q()
        inverted.children = [self]
        inverted.negated = True
        return inverted

    def __repr__(self) -> str:
        return f"<Q {'NOT ' if self.negated else ''}{self.connector}: {self.children!r}>"
