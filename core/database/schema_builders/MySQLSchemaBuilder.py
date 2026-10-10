from core.database.schema_builders.BaseSchemaBuilder import BaseSchemaBuilder


class MySQLSchemaBuilder(BaseSchemaBuilder):
    """
    SchemaBuilder for MySQL. Column example:
        "id INT AUTO_INCREMENT PRIMARY KEY"
        "name VARCHAR(255) NOT NULL"
    """

    def empty_table(self, name: str) -> str:
        """TRUNCATE - drops and recreates the table, resetting AUTO_INCREMENT."""
        sql = f"TRUNCATE TABLE {name}"
        self._execute(sql)
        return sql

    def has_table(self, name: str) -> bool:
        cur = self.connection.cursor()
        cur.execute("SHOW TABLES LIKE %s", (name,))
        row = cur.fetchone()
        cur.close()
        return row is not None
