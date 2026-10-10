from core.database.query_builders.BaseQueryBuilder import BaseQueryBuilder


class MySQLQueryBuilder(BaseQueryBuilder):
    """
    QueryBuilder for MySQL (mysql-connector-python).
    Uses %s placeholders. cursor.lastrowid works natively for insert(), so the
    base implementation is used as-is.

    mysql-connector's default cursor returns plain tuples, not dict-like rows
    (unlike sqlite3.Row or psycopg2's RealDictCursor) - so BaseModel._from_row's
    dict(row) blows up with "cannot convert dictionary update sequence element
    #0 to a sequence" as soon as a row has more than 2 columns. Requesting a
    dictionary cursor here fixes that and keeps rows dict-shaped across all
    three SQL drivers.
    """

    placeholder = "%s"

    def _cursor(self):
        return self.connection.cursor(dictionary=True)
