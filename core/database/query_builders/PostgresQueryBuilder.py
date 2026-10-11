from typing import Any, Dict

from core.database.query_builders.BaseQueryBuilder import BaseQueryBuilder


class PostgresQueryBuilder(BaseQueryBuilder):
    """
    QueryBuilder for PostgreSQL (psycopg2).
    Uses %s placeholders, same as MySQL - but psycopg2 does NOT populate
    cursor.lastrowid the way mysql-connector/sqlite3 do, so insert() is
    overridden here to use `RETURNING <primary_key>` instead.
    """

    placeholder = "%s"

    def insert(self, data: Dict[str, Any], primary_key: str = "id") -> Any:
        self._require_table()
        ph = self.placeholder
        cols = ", ".join(data.keys())
        placeholders = ", ".join([ph] * len(data))
        sql = f"INSERT INTO {self._table} ({cols}) VALUES ({placeholders}) RETURNING {primary_key}"
        cur = self._cursor()
        cur.execute(sql, list(data.values()))
        row = cur.fetchone()
        self.connection.commit()
        cur.close()
        if row is None:
            return None
        try:
            return row[primary_key]
        except (TypeError, KeyError):
            return row[0]
