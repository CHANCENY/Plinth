"""
BaseSchemaBuilder
------------------
Shared DDL logic for the mysql / postgres / sqlite drivers: create_table,
drop_table, empty_table (truncate/clear), has_table.

Column definitions are passed as raw SQL fragments, one per column/constraint,
e.g.:

    schema.create_table("users", [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "name TEXT NOT NULL",
        "email TEXT NOT NULL UNIQUE",
        "active INTEGER DEFAULT 1",
    ])

They're left as raw strings rather than abstracted further because column
syntax (auto-increment, types, etc.) genuinely differs per engine - see each
subclass's docstring for that engine's conventions. CREATE/DROP/TRUNCATE
semantics, however, differ enough that each engine gets its own subclass.
"""

from typing import List


class BaseSchemaBuilder:
    def __init__(self, connection):
        self.connection = connection

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _execute(self, sql: str, bindings: list = None) -> None:
        cur = self.connection.cursor()
        cur.execute(sql, bindings or [])
        self.connection.commit()
        cur.close()

    @staticmethod
    def _require_columns(columns: List[str]) -> None:
        if not columns:
            raise ValueError("create_table: at least one column definition is required")

    # ------------------------------------------------------------------ #
    # DDL - shared across mysql/postgres/sqlite
    # ------------------------------------------------------------------ #
    def create_table(self, name: str, columns: List[str], if_not_exists: bool = True) -> str:
        """columns: list of raw column/constraint definitions, e.g. 'id INTEGER PRIMARY KEY'."""
        self._require_columns(columns)
        clause = "IF NOT EXISTS " if if_not_exists else ""
        cols_sql = ", ".join(columns)
        sql = f"CREATE TABLE {clause}{name} ({cols_sql})"
        self._execute(sql)
        return sql

    def drop_table(self, name: str, if_exists: bool = True) -> str:
        clause = "IF EXISTS " if if_exists else ""
        sql = f"DROP TABLE {clause}{name}"
        self._execute(sql)
        return sql

    # engine-specific: TRUNCATE isn't valid SQL everywhere (sqlite has none)
    def empty_table(self, name: str) -> str:
        raise NotImplementedError

    def has_table(self, name: str) -> bool:
        raise NotImplementedError
