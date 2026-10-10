from core.database.schema_builders.BaseSchemaBuilder import BaseSchemaBuilder


class SQLiteSchemaBuilder(BaseSchemaBuilder):
    """
    SchemaBuilder for SQLite. Column example:
        "id INTEGER PRIMARY KEY AUTOINCREMENT"
        "name TEXT NOT NULL"

    SQLite has no TRUNCATE statement, so empty_table() falls back to
    DELETE FROM + clearing the autoincrement counter in sqlite_sequence
    (if that table exists, i.e. if any column used AUTOINCREMENT).
    """

    def empty_table(self, name: str) -> str:
        cur = self.connection.cursor()
        cur.execute(f"DELETE FROM {name}")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
        if cur.fetchone():
            cur.execute("DELETE FROM sqlite_sequence WHERE name = ?", (name,))
        self.connection.commit()
        cur.close()
        return f"DELETE FROM {name}"

    def has_table(self, name: str) -> bool:
        cur = self.connection.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,))
        row = cur.fetchone()
        cur.close()
        return row is not None
