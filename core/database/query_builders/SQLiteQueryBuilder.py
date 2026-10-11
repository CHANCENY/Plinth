from core.database.query_builders.BaseQueryBuilder import BaseQueryBuilder


class SQLiteQueryBuilder(BaseQueryBuilder):
    """
    QueryBuilder for SQLite (stdlib sqlite3).
    Uses ? placeholders. cursor.lastrowid works natively for insert(), so the
    base implementation is used as-is.
    """

    placeholder = "?"
