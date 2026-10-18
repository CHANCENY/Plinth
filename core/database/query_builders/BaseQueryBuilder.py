"""
BaseQueryBuilder
-----------------
Shared fluent SQL builder logic for the mysql / postgres / sqlite drivers.
Each engine gets its own concrete subclass (MySQLQueryBuilder, PostgresQueryBuilder,
SQLiteQueryBuilder) because parameter placeholder style and INSERT semantics
differ per engine - but the query-building logic itself (table/select/where/
join/orderBy/limit/...) is identical, so it lives here once.

Calling .table(name) starts a fresh query: it resets any select/where/join/
order/limit state left over from a previous call, so one builder instance can
safely be reused for many queries, e.g.:

    users = db.table("users").where("active", 1).get()
    posts = db.table("posts").where("author_id", 5).get()   # clean slate
"""

from typing import Any, Dict, List, Optional, Tuple

# override in subclasses
PLACEHOLDER = "%s"


class WhereMixin:
    """
    The condition-building methods shared by the query builder and by WhereCollector (a bare
    list of conditions with no table, used to assemble the inside of a parenthesised group).
    They only append to self._wheres; rendering happens in render_conditions().
    """

    placeholder: str = PLACEHOLDER
    _wheres: List[Tuple[str, Any, str, Any]]

    def where(self, column: str, operator: str, value: Any = None, boolean: str = "AND") -> "BaseQueryBuilder":
        # allow where("col", value) shorthand for equality
        if value is None:
            value = operator
            operator = "="
        self._wheres.append((boolean, column, operator, value))
        return self

    def or_where(self, column: str, operator: str, value: Any = None) -> "BaseQueryBuilder":
        return self.where(column, operator, value, boolean="OR")

    def where_in(self, column: str, values: List[Any], boolean: str = "AND") -> "BaseQueryBuilder":
        self._wheres.append((boolean, column, "IN", list(values)))
        return self

    def where_not_in(self, column: str, values: List[Any], boolean: str = "AND") -> "BaseQueryBuilder":
        self._wheres.append((boolean, column, "NOT IN", list(values)))
        return self

    def where_null(self, column: str, boolean: str = "AND") -> "BaseQueryBuilder":
        self._wheres.append((boolean, column, "IS NULL", None))
        return self

    def where_not_null(self, column: str, boolean: str = "AND") -> "BaseQueryBuilder":
        self._wheres.append((boolean, column, "IS NOT NULL", None))
        return self

    def where_like(self, column: str, pattern: str, negate: bool = False, boolean: str = "AND") -> "BaseQueryBuilder":
        """
        LIKE with '!' as the escape character (`col LIKE ? ESCAPE '!'`). '!' rather than the
        usual backslash because it means the same thing, unquoted-safe, on mysql/postgres/sqlite -
        a backslash escape needs different literal quoting per engine. Callers escape any literal
        %, _ or ! in `pattern` as !%, !_ and !! (see QuerySet._escape_like).
        """
        self._wheres.append((boolean, column, "NOT LIKE ESCAPE" if negate else "LIKE ESCAPE", pattern))
        return self

    def where_raw(self, sql: str, bindings: Optional[List[Any]] = None, boolean: str = "AND"):
        """
        A ready-made SQL condition. `sql` must use this builder's own placeholder (builder.placeholder)
        for every bound value - callers never interpolate values into it. Used for things like
        `price > (cost * ?)` and `id IN (SELECT ...)` that the column/operator/value form can't express.
        """
        self._wheres.append((boolean, sql, "RAW", list(bindings or [])))
        return self

    def where_group(self, entries: List[Tuple[str, Any, str, Any]], negate: bool = False, boolean: str = "AND"):
        """A parenthesised group of conditions (as collected by a WhereCollector), optionally NOT-ed."""
        self._wheres.append((boolean, None, "NOT GROUP" if negate else "GROUP", list(entries)))
        return self

    @staticmethod
    def render_conditions(entries, placeholder: str) -> Tuple[str, List[Any]]:
        """Renders a list of where-entries to ("a = ? AND (b = ? OR c = ?)", [bindings]) - recursively for groups."""
        fragments: List[Tuple[str, str]] = []
        bindings: List[Any] = []
        ph = placeholder
        for boolean, column, operator, value in entries:
            op = operator.upper()
            if op in ("GROUP", "NOT GROUP"):
                inner, inner_bindings = WhereMixin.render_conditions(value, ph)
                if not inner:
                    continue  # an empty group (an empty Q) matches everything - nothing to add
                fragments.append((boolean, f"{'NOT ' if op == 'NOT GROUP' else ''}({inner})"))
                bindings.extend(inner_bindings)
            elif op == "RAW":
                fragments.append((boolean, f"({column})"))
                bindings.extend(value)
            elif op in ("IN", "NOT IN"):
                if not value:
                    # `IN ()` is a syntax error in SQL - an empty IN matches nothing, an empty NOT IN matches everything
                    fragments.append((boolean, "1 = 0" if op == "IN" else "1 = 1"))
                else:
                    placeholders = ", ".join([ph] * len(value))
                    fragments.append((boolean, f"{column} {op} ({placeholders})"))
                    bindings.extend(value)
            elif op in ("IS NULL", "IS NOT NULL"):
                fragments.append((boolean, f"{column} {op}"))
            elif op in ("LIKE ESCAPE", "NOT LIKE ESCAPE"):
                keyword = "NOT LIKE" if op.startswith("NOT") else "LIKE"
                fragments.append((boolean, f"{column} {keyword} {ph} ESCAPE '!'"))
                bindings.append(value)
            else:
                fragments.append((boolean, f"{column} {operator} {ph}"))
                bindings.append(value)

        sql = ""
        for i, (boolean, fragment) in enumerate(fragments):
            sql += fragment if i == 0 else f" {boolean} {fragment}"
        return sql, bindings


class WhereCollector(WhereMixin):
    """Collects conditions without a table - the body of a Q group; see BaseQueryBuilder.where_group()."""

    def __init__(self, placeholder: str = PLACEHOLDER):
        self.placeholder = placeholder
        self._wheres = []


class BaseQueryBuilder(WhereMixin):
    placeholder: str = PLACEHOLDER

    def __init__(self, connection):
        self.connection = connection
        self._table: Optional[str] = None
        self._columns: List[str] = ["*"]
        self._wheres: List[Tuple[str, str, str, Any]] = []
        self._joins: List[str] = []
        self._order_by: List[str] = []
        self._group_by: List[str] = []
        self._havings: List[Tuple[str, Any, str, Any]] = []
        self._distinct: bool = False
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None

    # ----------------------------- building ---------------------------- #
    def table(self, name: str) -> "BaseQueryBuilder":
        """Starts a new query against `name`, resetting any prior query state."""
        self._table = name
        self._columns = ["*"]
        self._wheres = []
        self._joins = []
        self._order_by = []
        self._group_by = []
        self._havings = []
        self._distinct = False
        self._limit = None
        self._offset = None
        return self

    def select(self, *columns: str) -> "BaseQueryBuilder":
        self._columns = list(columns) if columns else ["*"]
        return self

    def join(self, table: str, first: str, operator: str, second: str, join_type: str = "INNER") -> "BaseQueryBuilder":
        self._joins.append(f"{join_type} JOIN {table} ON {first} {operator} {second}")
        return self

    def left_join(self, table: str, first: str, operator: str, second: str) -> "BaseQueryBuilder":
        return self.join(table, first, operator, second, join_type="LEFT")

    def order_by(self, column: str, direction: str = "ASC") -> "BaseQueryBuilder":
        self._order_by.append(f"{column} {direction.upper()}")
        return self

    def group_by(self, *columns: str) -> "BaseQueryBuilder":
        self._group_by.extend(columns)
        return self

    def having(self, entries: List[Tuple[str, Any, str, Any]]) -> "BaseQueryBuilder":
        """Adds HAVING conditions (a list of where-entries, e.g. from a WhereCollector) - for aggregates."""
        self._havings.extend(entries)
        return self

    def distinct(self, enabled: bool = True) -> "BaseQueryBuilder":
        self._distinct = enabled
        return self

    def limit(self, n: int) -> "BaseQueryBuilder":
        self._limit = n
        return self

    def offset(self, n: int) -> "BaseQueryBuilder":
        self._offset = n
        return self

    # ----------------------------- internals ---------------------------- #
    def _require_table(self) -> None:
        if not self._table:
            raise ValueError(f"{type(self).__name__}: no table set - call .table('name') first")

    def _build_where(self) -> Tuple[str, List[Any]]:
        sql, bindings = self.render_conditions(self._wheres, self.placeholder)
        return (" WHERE " + sql, bindings) if sql else ("", [])

    def _build_having(self) -> Tuple[str, List[Any]]:
        sql, bindings = self.render_conditions(self._havings, self.placeholder)
        return (" HAVING " + sql, bindings) if sql else ("", [])

    def to_sql(self) -> Tuple[str, List[Any]]:
        self._require_table()
        cols = ", ".join(self._columns)
        sql = f"SELECT {'DISTINCT ' if self._distinct else ''}{cols} FROM {self._table}"
        if self._joins:
            sql += " " + " ".join(self._joins)
        where_sql, bindings = self._build_where()
        sql += where_sql
        if self._group_by:
            sql += " GROUP BY " + ", ".join(self._group_by)
        having_sql, having_bindings = self._build_having()
        sql += having_sql
        bindings = bindings + having_bindings
        if self._order_by:
            sql += " ORDER BY " + ", ".join(self._order_by)
        if self._limit is not None:
            sql += f" LIMIT {self._limit}"
        if self._offset is not None:
            sql += f" OFFSET {self._offset}"
        return sql, bindings

    def _cursor(self):
        return self.connection.cursor()

    # ----------------------------- execution ---------------------------- #
    def get(self) -> List[Any]:
        sql, bindings = self.to_sql()
        cur = self._cursor()
        cur.execute(sql, bindings)
        rows = cur.fetchall()
        cur.close()
        return rows

    def first(self) -> Optional[Any]:
        self.limit(1)
        rows = self.get()
        return rows[0] if rows else None

    def count(self) -> int:
        original_columns = self._columns
        self._columns = ["COUNT(*) AS count"]
        sql, bindings = self.to_sql()
        self._columns = original_columns
        cur = self._cursor()
        cur.execute(sql, bindings)
        row = cur.fetchone()
        cur.close()
        if row is None:
            return 0
        try:
            return row["count"]
        except (TypeError, KeyError, IndexError):
            return row[0]

    def insert(self, data: Dict[str, Any], primary_key: str = "id") -> Any:
        """
        Default insert - works for engines whose cursor exposes lastrowid (mysql, sqlite).
        `primary_key` is unused here; it's accepted so callers can pass it uniformly to every
        engine's builder (PostgresQueryBuilder needs it for its RETURNING clause).
        """
        self._require_table()
        ph = self.placeholder
        cols = ", ".join(data.keys())
        placeholders = ", ".join([ph] * len(data))
        sql = f"INSERT INTO {self._table} ({cols}) VALUES ({placeholders})"
        cur = self._cursor()
        cur.execute(sql, list(data.values()))
        self.connection.commit()
        last_id = getattr(cur, "lastrowid", None)
        cur.close()
        return last_id

    def update(self, data: Dict[str, Any]) -> int:
        self._require_table()
        ph = self.placeholder
        assignments: List[str] = []
        bindings: List[Any] = []
        for column, value in data.items():
            if hasattr(value, "as_sql"):  # an expression: SET stock = (stock - ?)
                expression_sql, expression_bindings = value.as_sql(ph, lambda name: name)
                assignments.append(f"{column} = {expression_sql}")
                bindings.extend(expression_bindings)
            else:
                assignments.append(f"{column} = {ph}")
                bindings.append(value)
        sql = f"UPDATE {self._table} SET {', '.join(assignments)}"
        where_sql, where_bindings = self._build_where()
        sql += where_sql
        bindings += where_bindings
        cur = self._cursor()
        cur.execute(sql, bindings)
        self.connection.commit()
        affected = cur.rowcount
        cur.close()
        return affected

    def delete(self) -> int:
        self._require_table()
        sql = f"DELETE FROM {self._table}"
        where_sql, bindings = self._build_where()
        sql += where_sql
        cur = self._cursor()
        cur.execute(sql, bindings)
        self.connection.commit()
        affected = cur.rowcount
        cur.close()
        return affected

    def raw(self, sql: str, bindings: Optional[List[Any]] = None) -> List[Any]:
        cur = self._cursor()
        cur.execute(sql, bindings or [])
        if cur.description:
            rows = cur.fetchall()
            cur.close()
            return rows
        self.connection.commit()
        cur.close()
        return []
