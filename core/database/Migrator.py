"""
Migrator
----------
Two jobs, matching the two CLI commands in console.py:

    python console.py migration   -> Migrator.generate()
        For each registered model (MODELS below), diffs its current fields
        (core/database/fields.py) against the FIELDS snapshot in the latest
        migration file for that table, and writes a new migration file if
        anything changed - a CREATE TABLE for a model with no prior
        migration, or ALTER TABLE ADD/DROP/MODIFY COLUMN statements for one
        that already has a table. Files land in migrations/ at the project
        root (see MigrationFile.py for the file format).

    python console.py migrate     -> Migrator.migrate()
        Runs whichever migration files haven't been applied yet, in
        filename order, and records each one in a `migrations` tracking
        table - so re-running is a no-op once everything's applied, the
        same way a real migration tool behaves.

MODELS is also the registration order = dependency order for fresh(): a
model with a FOREIGN KEY to another table must come after it (User before
File/OAuthIdentity, since those reference users.uid).

A model that hasn't declared any Field attributes (still using a
hand-written SCHEMA list) is simply skipped by generate() - there's nothing
to diff. It can still be created directly with Model.create_table(db) (see
BaseModel), just not through the versioned migration-file workflow here.

Column/ALTER syntax throughout is MySQL-flavored, the same convention this
project has used from the start - migrate needs a real MySQL database to
run cleanly; the `migrations` tracking table itself is driver-agnostic, so
`python console.py migrate:status` works on any driver.
"""

from typing import Dict, List, Optional, Type

from core.database.MigrationFile import latest_fields_for_table, list_migration_files, load_migration, migration_name, write_migration_file
from core.models.BaseModel import BaseModel
from core.models.File import File
from core.models.OAuthIdentity import OAuthIdentity
from core.models.PasswordReset import PasswordReset
from core.models.Permission import Permission
from core.models.Role import Role
from core.models.RolePermission import RolePermission
from core.models.Session import Session
from core.models.User import User

# registration order = dependency order - foreign-key targets come first
MODELS: List[Type[BaseModel]] = [
    User,
    File,
    OAuthIdentity,
    Session,
    PasswordReset,
    Role,
    Permission,
    RolePermission,
]

MIGRATIONS_TABLE = "migrations"
MIGRATIONS_SCHEMA = [
    "migration VARCHAR(255) PRIMARY KEY",
    "batch INTEGER NOT NULL",
    "run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
]


class Migrator:
    def __init__(self, db_connection, models: Optional[List[Type[BaseModel]]] = None):
        self.db = db_connection
        if models is not None:
            self.models = models
        else:
            self.models = MODELS + self._app_models()
        self.warnings: List[str] = []  # filled in by migrate() - things that needed a human's judgement

    @staticmethod
    def _app_models() -> List[Type[BaseModel]]:
        """Models any registered app declared (public/apps/<name>/models/*.py), per the last
        `python console.py apps:discover` run - see core/apps/AppManager.py. Empty if no apps
        are registered, or discovery hasn't been run yet; core migrations work fine either way."""
        from core.apps.AppManager import AppManager

        return AppManager().load_model_classes()

    # ------------------------------------------------------------------ #
    # the migrations table itself
    # ------------------------------------------------------------------ #
    def _ensure_migrations_table(self) -> None:
        self.db.schema().create_table(MIGRATIONS_TABLE, MIGRATIONS_SCHEMA, True)

    def _query(self):
        return self.db.connect().table(MIGRATIONS_TABLE)

    def _applied(self) -> set:
        self._ensure_migrations_table()
        return {row["migration"] for row in self._query().get()}

    def _next_batch(self) -> int:
        rows = self._query().order_by("batch", "DESC").limit(1).get()
        return (rows[0]["batch"] + 1) if rows else 1

    # ------------------------------------------------------------------ #
    # generate - writes migration files, touches no database
    # ------------------------------------------------------------------ #
    def generate(self) -> List[str]:
        """Writes a migration file for every registered model whose fields changed. Returns the filenames written."""
        written = []
        for model in self.models:
            current = self._current_fields(model)
            if not current:
                continue  # no declared Field attributes on this model - nothing to diff/generate

            previous = latest_fields_for_table(model.table_name)
            if previous is None:
                path = write_migration_file(
                    slug=f"create_{model.table_name}_table",
                    table=model.table_name,
                    fields=current,
                    operations=[self._create_table_sql(model)],
                )
            else:
                operations, warnings = self._diff_operations(model.table_name, previous, current)
                if not operations and not warnings:
                    continue  # nothing changed for this table
                path = write_migration_file(
                    slug=f"alter_{model.table_name}_table",
                    table=model.table_name,
                    fields=current,
                    operations=operations,
                    warnings=warnings,
                )
            written.append(migration_name(path))
        return written

    @staticmethod
    def _current_fields(model: Type[BaseModel]) -> Dict[str, str]:
        return {field.migration_key(): field.to_sql() for field in model._fields.values()}

    @staticmethod
    def _create_table_sql(model: Type[BaseModel]) -> str:
        return f"CREATE TABLE IF NOT EXISTS {model.table_name} ({', '.join(model.SCHEMA)})"

    @staticmethod
    def _diff_operations(table: str, previous: Dict[str, str], current: Dict[str, str]):
        """
        Compares two migration_key()->to_sql() snapshots and returns (operations, warnings).
        Constraint keys (fk_*/unique_*) that changed or were removed can't be safely auto-ALTERed -
        MySQL needs their exact database-assigned name to drop them, which isn't reliably tracked
        here - so those become a warning asking for a manual look instead of a guessed statement.
        """
        operations: List[str] = []
        warnings: List[str] = []

        def is_constraint(key: str) -> bool:
            return key.startswith("fk_") or key.startswith("unique_")

        for key in current:
            if key not in previous:
                sql = current[key]
                operations.append(f"ALTER TABLE {table} ADD {sql}" if is_constraint(key) else f"ALTER TABLE {table} ADD COLUMN {sql}")

        for key in current:
            if key in previous and current[key] != previous[key]:
                if is_constraint(key):
                    warnings.append(f"constraint '{key}' changed - drop and re-add it manually: {current[key]}")
                else:
                    operations.append(f"ALTER TABLE {table} MODIFY COLUMN {current[key]}")

        for key in previous:
            if key not in current:
                if is_constraint(key):
                    warnings.append(f"constraint '{key}' was removed from the model - drop it manually if needed")
                else:
                    operations.append(f"ALTER TABLE {table} DROP COLUMN {key}")

        return operations, warnings

    # ------------------------------------------------------------------ #
    # migrate - runs pending migration files
    # ------------------------------------------------------------------ #
    def status(self) -> List[Dict[str, object]]:
        """[{"migration": ..., "table": ..., "applied": bool}, ...] in file order."""
        applied = self._applied()
        rows = []
        for path in list_migration_files():
            name = migration_name(path)
            rows.append({"migration": name, "table": load_migration(path).TABLE, "applied": name in applied})
        return rows

    def migrate(self) -> List[str]:
        """Runs any pending migration files, in filename order. Returns the migration names that ran."""
        self.warnings = []
        applied = self._applied()
        batch = self._next_batch()
        ran = []
        for path in list_migration_files():
            name = migration_name(path)
            if name in applied:
                continue
            module = load_migration(path)
            for statement in module.OPERATIONS:
                self.db.connect().raw(statement)
            for warning in getattr(module, "WARNINGS", []):
                self.warnings.append(f"{name}: {warning}")
            self._query().insert({"migration": name, "batch": batch})
            ran.append(name)
        return ran

    def fresh(self) -> List[str]:
        """Drops every registered table (reverse order, to respect foreign keys) and re-runs migrate()."""
        schema = self.db.schema()
        for model in reversed(self.models):
            schema.drop_table(model.table_name, True)
        schema.drop_table(MIGRATIONS_TABLE, True)
        return self.migrate()
