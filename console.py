#!/usr/bin/env python3
"""
console.py
-----------
Command-line entry point for framework-level commands - currently database
migrations, run through the Migrator (core/database/Migrator.py). Models
declare their columns as fields (core/database/fields.py); `migration`
diffs those fields against migrations/ and writes what changed, `migrate`
applies whatever hasn't run yet.

`serve` runs the project's actual entry point, public/main.py - see that
file for the server itself; this is just a convenient way to start it.

`apps:discover` scans every app registered in app.py (public/apps/<name>/)
for routes (@route-decorated controller methods), models, the roles and
permissions each app declares in its permissions.py, and the dashboard menu
items in its menus.py, and writes what it finds
to storage/cache/apps.json - see core/apps/AppManager.py for why this is a
separate step rather than something that happens on every request. Routing.py
reads that cache; so does `migration`/`migrate` below, for any models an app
declares.

It then syncs the declared roles/permissions into the database (the roles,
permissions and role_permissions tables - see core/services/AccessService.py):
new ones are added, and any belonging to an app that's no longer registered are
removed, along with stale entries in users' stored permissions. If the tables
don't exist yet (first run, before `migrate`) it says so and skips that part -
run `migrate`, then `apps:discover` again.

Usage
-----
    python console.py serve             # start the dev server (public/main.py) - Ctrl+C to stop
    python console.py serve --host 0.0.0.0 --port 8080   # override .env's APP_HOST/APP_PORT for this run

    python console.py apps:discover     # (re)scan registered apps for routes/models/roles/permissions,
                                        # refresh the cache and sync roles & permissions to the database

    python console.py migration         # generate migration files from whatever changed in the models
    python console.py migrate           # run any pending migrations
    python console.py migrate:status    # show which migrations have run / are pending
    python console.py migrate:fresh     # drop every registered table and re-run all migrations

Run this from the project root (same place as .env), since DatabaseConnection
resolves paths (like a sqlite file) relative to it, and migrations/ is
created there too.
"""

import argparse
import sys

from core.database.DatabaseConnection import DatabaseConnection
from core.database.Migrator import Migrator


def command_serve(host, port) -> int:
    from public.main import run  # imported lazily - no other command needs the HTTP server

    run(host=host, port=port)
    return 0


def command_apps_discover() -> int:
    from core.apps.AppManager import AppManager

    manager = AppManager()
    try:
        result = manager.discover()
    except ValueError as exc:  # a permissions.py that breaks the definition rules
        print(f"apps:discover failed - {exc}", file=sys.stderr)
        return 1

    if not result["app_names"]:
        print("No apps registered in app.py - nothing to discover.")
        return 0

    print(f"Apps: {', '.join(result['app_names'])}")
    for warning in result["warnings"]:
        print(f"  warning: {warning}")

    if not result["routes"]:
        print("No routes found.")
    else:
        print(f"\n{len(result['routes'])} route(s):")
        for r in result["routes"]:
            methods = ",".join(r["method"])
            print(f"  {methods:10} {r['path']:35} -> {r['controller']}")

    if result["models"]:
        print(f"\n{len(result['models'])} model(s):")
        for m in result["models"]:
            print(f"  {m}")

    print(
        f"\n{len(result['permissions'])} permission(s), {len(result['roles'])} role(s), "
        f"{len(result['menus'])} menu item(s) declared by apps"
    )
    print(f"Cache written to {manager.cache_path}")
    return _sync_access(result)


def _sync_access(result) -> int:
    """Syncs the discovered roles/permissions into the database - see AccessService.sync()."""
    from core.models.Permission import Permission
    from core.models.Role import Role
    from core.models.RolePermission import RolePermission
    from core.services.AccessService import AccessService

    db = None
    try:
        try:
            db = DatabaseConnection()
            for model in (Role, Permission, RolePermission):
                model.objects(db).count()
        except Exception as exc:  # no database / tables not created yet - not an error, just not ready
            print("\nRoles & permissions were NOT synced to the database - it isn't ready yet:")
            print(f"  {type(exc).__name__}: {exc}")
            print("  Run `python console.py migration` and `python console.py migrate`, then `python console.py apps:discover` again.")
            return 0

        try:
            summary = AccessService(db).sync(result["permissions"], result["roles"], result["app_names"])
        except ValueError as exc:
            print(f"\nRoles & permissions were NOT synced - {exc}", file=sys.stderr)
            return 1
    finally:
        if db is not None:
            db.close()

    print("\nRoles & permissions synced to the database:")
    print(f"  permissions  +{summary['permissions_added']} added, ~{summary['permissions_updated']} updated, -{summary['permissions_removed']} removed")
    print(f"  roles        +{summary['roles_added']} added, ~{summary['roles_updated']} updated, -{summary['roles_removed']} removed")
    print(f"  role grants  +{summary['grants_added']} added, -{summary['grants_removed']} removed")
    print(
        f"  users        {summary['users_updated']} updated "
        f"({summary['users_role_reset']} moved to the default role, "
        f"{summary['user_permissions_removed']} stale permission(s) removed)"
    )
    return 0


def command_migration(migrator: Migrator) -> int:
    written = migrator.generate()
    if not written:
        print("No changes detected - nothing to generate.")
        return 0
    for name in written:
        print(f"Created migration: {name}")
    print("\nReview the generated file(s) in migrations/, then run: python console.py migrate")
    return 0


def command_migrate(migrator: Migrator) -> int:
    ran = migrator.migrate()
    if not ran:
        print("Nothing to migrate - already up to date.")
    else:
        for name in ran:
            print(f"Migrated: {name}")
    if migrator.warnings:
        print("\nNeeds a manual look:")
        for warning in migrator.warnings:
            print(f"  - {warning}")
    return 0


def command_status(migrator: Migrator) -> int:
    rows = migrator.status()
    if not rows:
        print("No migration files yet - run: python console.py migration")
        return 0
    width = max(len(row["migration"]) for row in rows)
    for row in rows:
        marker = "Yes" if row["applied"] else "No"
        print(f"{row['migration']:<{width}}  applied: {marker}")
    return 0


def command_fresh(migrator: Migrator) -> int:
    tables = ", ".join(model.table_name for model in migrator.models)
    confirm = input(f"This will DROP these tables and re-run all migrations: {tables}\nContinue? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return 1
    ran = migrator.fresh()
    for name in ran:
        print(f"Migrated: {name}")
    if migrator.warnings:
        print("\nNeeds a manual look:")
        for warning in migrator.warnings:
            print(f"  - {warning}")
    return 0


MIGRATOR_COMMANDS = {
    "migration": command_migration,
    "migrate": command_migrate,
    "migrate:status": command_status,
    "migrate:fresh": command_fresh,
}
NO_DB_COMMANDS = {"serve": command_serve, "apps:discover": command_apps_discover}
COMMANDS = {**NO_DB_COMMANDS, **MIGRATOR_COMMANDS}


def main() -> int:
    parser = argparse.ArgumentParser(prog="console.py", description="Framework command-line tools.")
    parser.add_argument("command", choices=sorted(COMMANDS.keys()), help="Command to run.")
    parser.add_argument("--host", default=None, help="serve only: overrides .env's APP_HOST for this run.")
    parser.add_argument("--port", type=int, default=None, help="serve only: overrides .env's APP_PORT for this run.")
    args = parser.parse_args()

    if args.command == "serve":
        return command_serve(args.host, args.port)  # no DB/migrator needed to start the server
    if args.command == "apps:discover":
        return command_apps_discover()

    db = DatabaseConnection()
    migrator = Migrator(db)
    try:
        return MIGRATOR_COMMANDS[args.command](migrator)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
