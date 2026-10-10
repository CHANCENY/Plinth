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
                        (same convention core/controllers/ already uses).
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

        for app_name in app_names:
            app_dir = self.apps_dir / app_name
            if not app_dir.is_dir():
                warnings.append(f"app '{app_name}' is registered in app.py but public/apps/{app_name}/ doesn't exist")
                continue
            routes.extend(self._discover_routes(app_name, app_dir))
            models.extend(self._discover_models(app_name, app_dir))

        result = {"app_names": app_names, "routes": routes, "models": models, "warnings": warnings}
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

    # ------------------------------------------------------------------ #
    # cache read/write
    # ------------------------------------------------------------------ #
    def _write_cache(self, data: Dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_cache(self) -> Optional[Dict[str, Any]]:
        """Returns the last-discovered {"app_names", "routes", "models", "warnings"}, or None if
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
