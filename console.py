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

    python console.py fake:models                       # which models can be filled with fake data, and how many rows each has
    python console.py fake:generate User=20 Post=100    # create fake rows (parents first; related rows are made as needed)
    python console.py fake:generate Post --count 50 --seed 7 --locale fr_FR
    python console.py fake:generate Post --dry-run      # show sample rows without saving anything
    python console.py fake:runs                         # what earlier runs created
    python console.py fake:undo [run_id]                # delete exactly what a run created (default: the latest)
                                                        # needs `pip install Faker`; see core/fakedata/service.py

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


def _fake_targets(service, args):
    """["User=20", "Post"] -> [(User, 20), (Post, <--count or the model's default>)]"""
    targets = []
    for token in args.targets:
        name, _, count_text = token.partition("=")
        model = service.resolve(name)
        try:
            count = int(count_text) if count_text else (args.count or service.default_count(model))
        except ValueError:
            raise ValueError(f"'{token}': the count after '=' must be a whole number")
        targets.append((model, count))
    return targets


def command_fake(args) -> int:
    """fake:models / fake:generate / fake:runs / fake:undo - see core/fakedata/service.py."""
    from core.fakedata.generators import FakeOptions
    from core.fakedata.service import FakeDataError, FakeDataService, database_id

    if not 0 <= args.null_ratio <= 1:
        print("error: --null-ratio must be between 0 and 1", file=sys.stderr)
        return 1

    db = DatabaseConnection()
    try:
        service = FakeDataService(
            db,
            FakeOptions(locale=args.locale, seed=args.seed, password=args.password, null_ratio=args.null_ratio,
                        create_related=not args.no_related),
        )
        try:
            if args.command == "fake:models":
                if not service.faker_available():
                    print("note: Faker isn't installed yet - run: pip install Faker\n")
                print(f"{'Model':18} {'Table':26} {'Rows':>7} {'Default':>8}  Customised")
                for info in service.catalog():
                    rows = "-" if info.rows is None else str(info.rows)
                    print(f"{info.name:18} {info.table:26} {rows:>7} {info.default_count:>8}  {'yes' if info.customised else ''}")
                print("\nGenerate with:  python console.py fake:generate <Model>[=count] ...")
                return 0

            if args.command == "fake:runs":
                runs = service.runs()
                if not runs:
                    print("No recorded runs.")
                for run in runs:
                    summary = ", ".join(f"{i['table']}: {len(i['ids'])}" for i in run["items"])
                    print(f"{run['id']}  {run['created_at']}  [{run.get('database')}]  {summary}")
                return 0

            if args.command == "fake:undo":
                result = service.undo(args.targets[0] if args.targets else None, force=args.force)
                for table, count in result.deleted.items():
                    print(f"  deleted {count} row(s) from {table}")
                for problem in result.problems:
                    print(f"  problem: {problem}", file=sys.stderr)
                print(f"Run {result.run_id} " + ("could not be fully undone - it is kept so you can retry." if result.problems else "undone."))
                return 1 if result.problems else 0

            # fake:generate
            service.check_ready()  # Faker installed? locale valid? - before anything is printed or confirmed
            if not args.targets:
                print("error: name at least one model, e.g.  fake:generate User=20 Post=100   (see fake:models)", file=sys.stderr)
                return 1
            targets = _fake_targets(service, args)

            if args.dry_run:
                for model, count in targets:
                    print(f"--- {model.__name__} (sample of {min(count, 5)}; nothing is saved) ---")
                    for row in service.preview(model, min(count, 5)):
                        print("  " + ", ".join(f"{k}={v!r}" for k, v in row.items()))
                return 0

            print(f"Database: {database_id()}")
            for model, count in targets:
                print(f"  {count} x {model.__name__}  ({model.table_name})")
            if not args.yes:
                if not sys.stdin.isatty():
                    print("error: not an interactive terminal - pass --yes to go ahead without asking", file=sys.stderr)
                    return 1
                if input("Insert these rows into that database? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("Cancelled.")
                    return 1

            result = service.generate(targets)
            for table, count in result.created.items():
                print(f"  created {count} row(s) in {table}")
            print(f"Done: {result.total} row(s) in {result.seconds:.1f}s.  Run id: {result.run_id}")
            if "users" in result.created:
                print(f"Fake users sign in with the password: {args.password}")
            print(f"Undo with:  python console.py fake:undo {result.run_id}")
            return 0
        except (FakeDataError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    finally:
        db.close()


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
FAKE_COMMANDS = ("fake:models", "fake:generate", "fake:runs", "fake:undo")
COMMANDS = {**NO_DB_COMMANDS, **MIGRATOR_COMMANDS, **{name: command_fake for name in FAKE_COMMANDS}}


def main() -> int:
    parser = argparse.ArgumentParser(prog="console.py", description="Framework command-line tools.")
    parser.add_argument("command", choices=sorted(COMMANDS.keys()), help="Command to run.")
    parser.add_argument("--host", default=None, help="serve only: overrides .env's APP_HOST for this run.")
    parser.add_argument("--port", type=int, default=None, help="serve only: overrides .env's APP_PORT for this run.")
    parser.add_argument("targets", nargs="*", help="fake:generate: Model[=count] ...; fake:undo: a run id.")
    parser.add_argument("--count", type=int, default=None, help="fake:generate: rows for models named without =count.")
    parser.add_argument("--locale", default="en_US", help="fake:generate: Faker locale, e.g. fr_FR, de_DE (default en_US).")
    parser.add_argument("--seed", type=int, default=None, help="fake:generate: makes a run reproducible.")
    parser.add_argument("--password", default="password", help="fake:generate: the password fake users get (default 'password').")
    parser.add_argument("--null-ratio", type=float, default=0.1, help="fake:generate: how often a nullable column is NULL, 0-1 (default 0.1).")
    parser.add_argument("--no-related", action="store_true", help="fake:generate: don't create rows in related tables that are empty.")
    parser.add_argument("--dry-run", action="store_true", help="fake:generate: show sample rows, save nothing.")
    parser.add_argument("--yes", action="store_true", help="fake:generate: don't ask for confirmation.")
    parser.add_argument("--force", action="store_true", help="fake:undo: delete even if the run was made against a different database.")
    args = parser.parse_args()

    if args.command in FAKE_COMMANDS:
        return command_fake(args)
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
