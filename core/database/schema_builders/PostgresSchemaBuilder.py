from core.database.schema_builders.BaseSchemaBuilder import BaseSchemaBuilder


class PostgresSchemaBuilder(BaseSchemaBuilder):
    """
    SchemaBuilder for PostgreSQL. Column example:
        "id SERIAL PRIMARY KEY"
        "name VARCHAR(255) NOT NULL"
    """

    def empty_table(self, name: str, restart_identity: bool = True, cascade: bool = True) -> str:
        """
        TRUNCATE. restart_identity resets any SERIAL/IDENTITY counters (matches
        MySQL's TRUNCATE behavior); cascade also truncates tables with FKs to this one.
        """
        options = []
        if restart_identity:
            options.append("RESTART IDENTITY")
        if cascade:
            options.append("CASCADE")
        opts_sql = (" " + " ".join(options)) if options else ""
        sql = f"TRUNCATE TABLE {name}{opts_sql}"
        self._execute(sql)
        return sql

    def has_table(self, name: str) -> bool:
        cur = self.connection.cursor()
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            (name,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return False
        try:
            return bool(row["exists"])
        except (TypeError, KeyError):
            return bool(row[0])
