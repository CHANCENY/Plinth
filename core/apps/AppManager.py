"""
AppManager
------------
Discovers "apps" - self-contained feature folders under public/apps/<name>/,
each optionally with its own controllers/, services/, views/, and models/ -
and writes what it finds (routes + model import paths) into a cache file
that the live server reads from, instead of scanning the filesystem on every
request.

Why discovery is a separate step (python console.py apps:discover) and not
something Routing.py does on every request: importing every app's every
controller/model module on every single HTTP request - to find the methods
carrying @route - would be slow, and would let a broken app file crash
requests that have nothing to do with it. Discovery runs once (you re-run it
whenever you add/remove an app or change its routes), writes a plain JSON
cache, and Routing.py just reads that file - cheap, and a bad app can't take
the whole server down between discovery runs.

What's discovered, per app (see public/apps/blog/ for a working example of
the expected folder layout):

    controllers/*.py   one controller class per file, filename == class name
                        (same convention the built-in admin app's controllers use).
                        Any method carrying @route (core/http/routing/route.py)
                        becomes a route, with controller string
                        "public.apps.<app>.controllers.<File>::<method>" -
                        exactly the "module.path::Method" shape Routing.py's
                        own resolve_controller() already expects, so app
                        routes run through the exact same dispatch path core
                        routes do (including PermissionMiddleware).

    models/*.py        one BaseModel subclass per file, filename == class
                        name. Recorded as "<module>:<ClassName>" so Migrator
                        can import and include them in `python console.py
                        migration`/`migrate` alongside the core models.

    permissions.py     optional. Declares the user roles and user permissions this app
                        adds, as two module-level lists:

                            PERMISSIONS = [
                                {"key": "blog.create", "label": "Create blog posts",
                                 "description": "optional"},
                            ]
                            ROLES = [
                                {"key": "blog.editor", "label": "Blog editor",
                                 "description": "optional",
                                 "permissions": ["blog.create"],   # optional: granted by default
                                 "superuser": False},              # optional: True = holds everything
                            ]

                        Rules, enforced here - a violation aborts discovery with a list of
                        every problem found:
                          * every key must start with the app's own name followed by a dot
                            ("blog.create" in the blog app) so apps can never collide
                          * after that prefix: lowercase letters, digits, "_", and "." separators
                          * keys are at most 100 characters; "label" is required
                          * a role's "permissions" must name permissions that some registered
                            app declares

                        The definitions are cached with the routes, and `python console.py
                        apps:discover` also syncs them into the roles/permissions tables
                        (core/services/AccessService.py) - adding new ones and removing any
                        belonging to an app that is no longer registered.

    menus.py            optional. Declares items for the admin dashboard's sidebar (the nav in
                        public/apps/admin/views/partials/nav.html), as a MENUS list:

                            MENUS = [
                                {"key": "blog.new_post",            # '<app>.<name>', same rule as permissions
                                 "label": "New blog post",
                                 "path": "/blog/new",
                                 "group": "Content",                # sidebar section; default "Menu"
                                 "permission": ["blog.create"],     # who sees it; default ["authenticated"]
                                 "weight": 200,                     # lower = higher up; default 0
                                 "exact": False},                   # True = highlight only on this exact path
                            ]

                        "permission" takes the same values as a route's: "*" (everyone, even signed
                        out), "authenticated", "administrator", or permission keys - and it is
                        checked with the same rule the route middleware uses
                        (BaseMiddleware.has_access), so a link is shown exactly when the page behind
                        it would be allowed. Items are cached with the routes; see
                        core/services/MenuService.py for how they're grouped and highlighted.

    views/              not scanned (nothing to discover - it's just a
                        directory convention). TemplateService resolves
                        "<app_name>:some/view" against public/apps/<app>/views/
                        directly; see its docstring.

    services/           not scanned either - these are just normal importable
                        classes an app's own controllers use directly
                        (`from public.apps.<app>.services.Thing import Thing`).
                        Nothing here needs to "know" about them.

An app with no controllers/ or no models/ directory is simply skipped for
that part - apps are independent of each other and of the main framework by
design, so a views-only app (or one that hasn't gotten around to models yet)
works fine.
"""

import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


class AppManager:
    def __init__(self, project_root: Optional[str] = None):
        self.project_root = Path(project_root) if project_root else self._default_project_root()
        self.apps_dir = self.project_root / "public" / "apps"
        self.cache_path = self.project_root / "storage" / "cache" / "apps.json"

    @staticmethod
    def _default_project_root() -> Path:
        # this file lives at project/core/apps/AppManager.py
        return Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

    # ------------------------------------------------------------------ #
    # registered app names - app.py at the project root
    # ------------------------------------------------------------------ #
    def registered_apps(self) -> List[str]:
        app_py = self.project_root / "app.py"
        if not app_py.exists():
            return []
        spec = importlib.util.spec_from_file_location("app_registry", app_py)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return list(getattr(module, "REGISTERED_APPS", []))

    # ------------------------------------------------------------------ #
    # discovery - scans the filesystem, writes the cache
    # ------------------------------------------------------------------ #
    def discover(self) -> Dict[str, Any]:
        app_names = self.registered_apps()
        routes: List[Dict[str, Any]] = []
        models: List[str] = []
        warnings: List[str] = []
        permissions: List[Dict[str, Any]] = []
        roles: List[Dict[str, Any]] = []
        menus: List[Dict[str, Any]] = []
        errors: List[str] = []

        for app_name in app_names:
            app_dir = self.apps_dir / app_name
            if not app_dir.is_dir():
                warnings.append(f"app '{app_name}' is registered in app.py but public/apps/{app_name}/ doesn't exist")
                continue
            routes.extend(self._discover_routes(app_name, app_dir))
            models.extend(self._discover_models(app_name, app_dir))
            app_permissions, app_roles, app_errors = self._discover_access(app_name, app_dir)
            permissions.extend(app_permissions)
            roles.extend(app_roles)
            errors.extend(app_errors)
            app_menus, menu_errors = self._discover_menus(app_name, app_dir)
            menus.extend(app_menus)
            errors.extend(menu_errors)

        # a role may grant permissions declared by any registered app, so this can only be checked once all are loaded
        known_permissions = {p["key"] for p in permissions}
        for role in roles:
            for key in role["permissions"]:
                if key not in known_permissions:
                    errors.append(f"app '{role['app']}': role '{role['key']}' lists permission '{key}', which no registered app declares")

        if app_names:
            # the framework itself relies on these two roles (who the first account becomes, and the
            # default for everyone else) - see core/models/Role.py. Checked here, before the cache is
            # written, so a mistake can't leave the server with a half-registered set of apps.
            from core.models.Role import Role

            defined_roles = {r["key"] for r in roles}
            for required in (Role.ADMIN_KEY, Role.DEFAULT_KEY):
                if required not in defined_roles:
                    errors.append(
                        f"role '{required}' isn't declared by any registered app - keep the built-in 'admin' "
                        f"app registered in app.py (it declares the administrator and member roles)"
                    )

        if errors:
            raise ValueError("Invalid app definitions (nothing was discovered):\n  - " + "\n  - ".join(errors))

        result = {
            "app_names": app_names,
            "routes": routes,
            "models": models,
            "permissions": permissions,
            "roles": roles,
            "menus": menus,
            "warnings": warnings,
        }
        self._write_cache(result)
        return result

    def _discover_routes(self, app_name: str, app_dir: Path) -> List[Dict[str, Any]]:
        controllers_dir = app_dir / "controllers"
        if not controllers_dir.is_dir():
            return []

        routes = []
        for file_path in sorted(controllers_dir.glob("*.py")):
            if file_path.stem.startswith("_"):
                continue
            class_name = file_path.stem
            module_path = f"public.apps.{app_name}.controllers.{class_name}"
            try:
                module = importlib.import_module(module_path)
                controller_class = getattr(module, class_name)
            except (ImportError, AttributeError) as exc:
                raise ImportError(
                    f"AppManager: couldn't load controller '{class_name}' for app '{app_name}' "
                    f"(expected class '{class_name}' in {module_path}): {exc}"
                ) from exc

            for method_name, method in inspect.getmembers(controller_class, predicate=inspect.isfunction):
                for route_info in getattr(method, "_routes", []):
                    routes.append(
                        {
                            "path": route_info["path"],
                            "method": list(route_info["method"]),
                            "controller": f"{module_path}::{method_name}",
                            "permission": list(route_info["permission"]),
                            "app": app_name,
                        }
                    )
        return routes

    def _discover_models(self, app_name: str, app_dir: Path) -> List[str]:
        models_dir = app_dir / "models"
        if not models_dir.is_dir():
            return []

        from core.models.BaseModel import BaseModel

        found = []
        for file_path in sorted(models_dir.glob("*.py")):
            if file_path.stem.startswith("_"):
                continue
            class_name = file_path.stem
            module_path = f"public.apps.{app_name}.models.{class_name}"
            try:
                module = importlib.import_module(module_path)
                model_class = getattr(module, class_name)
            except (ImportError, AttributeError) as exc:
                raise ImportError(
                    f"AppManager: couldn't load model '{class_name}' for app '{app_name}' "
                    f"(expected class '{class_name}' in {module_path}): {exc}"
                ) from exc

            if inspect.isclass(model_class) and issubclass(model_class, BaseModel) and model_class is not BaseModel:
                found.append(f"{module_path}:{class_name}")
        return found

    @staticmethod
    def _check_key(app_name: str, kind: str, key: Any, where: str, errors: List[str]) -> bool:
        """The identifying-key rule shared by permissions, roles and menu items: a string that starts
        with '<app>.' followed by lowercase letters/digits/'_' with '.' separators, max 100 chars."""
        from core.services.AccessService import KEY_SUFFIX_RE, MAX_KEY_LENGTH

        if not isinstance(key, str) or not key:
            errors.append(f"{where}: every {kind} needs a string 'key'")
            return False
        if not key.startswith(f"{app_name}."):
            errors.append(f"{where}: {kind} key '{key}' must start with the app name: '{app_name}.<name>'")
            return False
        if not KEY_SUFFIX_RE.match(key[len(app_name) + 1:]) or len(key) > MAX_KEY_LENGTH:
            errors.append(
                f"{where}: {kind} key '{key}' must be '{app_name}.' followed by lowercase letters, digits, "
                f"'_' or '.' (max {MAX_KEY_LENGTH} characters)"
            )
            return False
        return True

    PERMISSION_FIELDS = {"key", "label", "description"}
    ROLE_FIELDS = {"key", "label", "description", "permissions", "superuser"}

    def _discover_access(self, app_name: str, app_dir: Path):
        """Reads public/apps/<app>/permissions.py. Returns (permissions, roles, errors) - normalized
        definitions plus a message for every rule they break (see the module docstring)."""
        if not (app_dir / "permissions.py").is_file():
            return [], [], []

        module_path = f"public.apps.{app_name}.permissions"
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise ImportError(f"AppManager: couldn't load permissions for app '{app_name}' ({module_path}): {exc}") from exc

        errors: List[str] = []
        where = f"app '{app_name}' permissions.py"

        def check_key(kind: str, key: Any) -> bool:
            return self._check_key(app_name, kind, key, where, errors)

        def entries(attribute: str, allowed: set, kind: str):
            raw = getattr(module, attribute, [])
            if not isinstance(raw, (list, tuple)):
                errors.append(f"{where}: {attribute} must be a list of dicts")
                return
            seen = set()
            for entry in raw:
                if not isinstance(entry, dict):
                    errors.append(f"{where}: every {kind} must be a dict, got {type(entry).__name__}")
                    continue
                unknown = set(entry) - allowed
                if unknown:
                    errors.append(f"{where}: {kind} '{entry.get('key')}' has unknown field(s) {sorted(unknown)} (allowed: {sorted(allowed)})")
                    continue
                if not check_key(kind, entry.get("key")):
                    continue
                if entry["key"] in seen:
                    errors.append(f"{where}: {kind} key '{entry['key']}' is declared twice")
                    continue
                if not isinstance(entry.get("label"), str) or not entry["label"].strip():
                    errors.append(f"{where}: {kind} '{entry['key']}' needs a non-empty 'label'")
                    continue
                seen.add(entry["key"])
                yield entry

        permissions = [
            {
                "key": e["key"],
                "label": e["label"].strip(),
                "description": e.get("description") or None,
                "app": app_name,
            }
            for e in entries("PERMISSIONS", self.PERMISSION_FIELDS, "permission")
        ]

        roles = []
        for e in entries("ROLES", self.ROLE_FIELDS, "role"):
            granted = e.get("permissions", [])
            if not isinstance(granted, (list, tuple)) or not all(isinstance(k, str) for k in granted):
                errors.append(f"{where}: role '{e['key']}' 'permissions' must be a list of permission keys")
                continue
            roles.append(
                {
                    "key": e["key"],
                    "label": e["label"].strip(),
                    "description": e.get("description") or None,
                    "app": app_name,
                    "superuser": bool(e.get("superuser", False)),
                    "permissions": list(dict.fromkeys(granted)),
                }
            )
        return permissions, roles, errors

    MENU_FIELDS = {"key", "label", "path", "group", "permission", "weight", "exact"}

    def _discover_menus(self, app_name: str, app_dir: Path):
        """Reads public/apps/<app>/menus.py (MENUS = [...]). Returns (menus, errors) - normalized
        items plus a message for every rule they break (see the module docstring)."""
        if not (app_dir / "menus.py").is_file():
            return [], []

        module_path = f"public.apps.{app_name}.menus"
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise ImportError(f"AppManager: couldn't load menus for app '{app_name}' ({module_path}): {exc}") from exc

        errors: List[str] = []
        where = f"app '{app_name}' menus.py"
        raw = getattr(module, "MENUS", [])
        if not isinstance(raw, (list, tuple)):
            return [], [f"{where}: MENUS must be a list of dicts"]

        menus, seen = [], set()
        for entry in raw:
            if not isinstance(entry, dict):
                errors.append(f"{where}: every menu item must be a dict, got {type(entry).__name__}")
                continue
            unknown = set(entry) - self.MENU_FIELDS
            if unknown:
                errors.append(f"{where}: menu item '{entry.get('key')}' has unknown field(s) {sorted(unknown)} (allowed: {sorted(self.MENU_FIELDS)})")
                continue
            if not self._check_key(app_name, "menu item", entry.get("key"), where, errors):
                continue
            key = entry["key"]
            if key in seen:
                errors.append(f"{where}: menu item key '{key}' is declared twice")
                continue
            seen.add(key)

            problems = len(errors)
            label, path = entry.get("label"), entry.get("path")
            if not isinstance(label, str) or not label.strip():
                errors.append(f"{where}: menu item '{key}' needs a non-empty 'label'")
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
                errors.append(f"{where}: menu item '{key}' needs a 'path' starting with a single '/' (e.g. '/admin/users')")
            group = entry.get("group", "Menu")
            if not isinstance(group, str) or not group.strip():
                errors.append(f"{where}: menu item '{key}' has an empty 'group'")
            permission = entry.get("permission", ["authenticated"])
            if not isinstance(permission, (list, tuple)) or not permission or not all(isinstance(p, str) and p for p in permission):
                errors.append(f"{where}: menu item '{key}' 'permission' must be a non-empty list of permission strings")
            weight = entry.get("weight", 0)
            if isinstance(weight, bool) or not isinstance(weight, int):
                errors.append(f"{where}: menu item '{key}' 'weight' must be an integer")
            if len(errors) > problems:
                continue

            menus.append(
                {
                    "key": key,
                    "label": label.strip(),
                    "path": path,
                    "group": group.strip(),
                    "permission": list(permission),
                    "weight": weight,
                    "exact": bool(entry.get("exact", False)),
                    "app": app_name,
                }
            )
        return menus, errors

    # ------------------------------------------------------------------ #
    # cache read/write
    # ------------------------------------------------------------------ #
    def _write_cache(self, data: Dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_cache(self) -> Optional[Dict[str, Any]]:
        """Returns the last-discovered {"app_names", "routes", "models", "permissions", "roles", "menus", "warnings"}, or None if
        `python console.py apps:discover` has never been run (or found nothing registered)."""
        if not self.cache_path.exists():
            return None
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def load_model_classes(self) -> List[type]:
        """Imports and returns every model class the last discovery found, for Migrator."""
        cache = self.load_cache()
        if not cache:
            return []
        classes = []
        for entry in cache.get("models", []):
            module_path, _, class_name = entry.partition(":")
            module = importlib.import_module(module_path)
            classes.append(getattr(module, class_name))
        return classes
