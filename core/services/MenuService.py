"""
MenuService
-------------
Builds the admin dashboard's sidebar from the menu items apps declare in their menus.py (collected
into storage/cache/apps.json by `python console.py apps:discover` - see core/apps/AppManager.py).

    groups = MenuService().build(current_user, "/admin/users/3/edit")
    # [{"label": "Overview", "links": [{"label": "Dashboard", "path": "/admin", "active": False}]},
    #  {"label": "Manage",   "links": [{"label": "Users", "path": "/admin/users", "active": True}, ...]}]

    * Visibility: an item is included only if the user passes its "permission" list, using the very
      same rule route protection uses (BaseMiddleware.has_access) - so "administrator",
      "authenticated", "*" and permission keys mean here exactly what they mean on a route.
    * Order: items by (weight, label); a group sits where its first (lowest-weight) visible item is.
      A group with no visible items doesn't appear at all.
    * Highlighting: at most one link is active - an exact path match wins, otherwise the longest
      item path that is a parent of the current path ("/admin/users" for "/admin/users/3/edit").
      Items with "exact": True only match their own path (so Dashboard at "/admin" isn't lit up
      on every page below it).

Templates reach this through the `dashboard_menu()` function ControllerBase.view() puts in the
context - see public/apps/admin/views/partials/nav.html.
"""

from typing import Any, Dict, List, Optional

from core.apps.AppManager import AppManager
from core.http.middleware.PermissionMiddleware import PermissionMiddleware


class MenuService:
    def __init__(self, app_manager: Optional[AppManager] = None):
        self.apps = app_manager or AppManager()
        self._access = PermissionMiddleware()  # only used for has_access()

    def items(self) -> List[Dict[str, Any]]:
        """Every declared menu item from the last discovery, unfiltered (empty if never discovered)."""
        cache = self.apps.load_cache() or {}
        return list(cache.get("menus", []))

    def build(self, current_user, current_path: str = "") -> List[Dict[str, Any]]:
        visible = [item for item in self.items() if self._access.has_access(item.get("permission"), current_user)]
        visible.sort(key=lambda item: (item.get("weight", 0), item.get("label", "")))

        active_path = self._active_path(visible, current_path)

        groups: Dict[str, Dict[str, Any]] = {}
        for item in visible:
            group = groups.setdefault(item["group"], {"label": item["group"], "links": []})
            group["links"].append(
                {"label": item["label"], "path": item["path"], "active": item["path"] == active_path}
            )
        return list(groups.values())

    @staticmethod
    def _active_path(items: List[Dict[str, Any]], current_path: str) -> Optional[str]:
        current = (current_path or "").split("?", 1)[0].rstrip("/") or "/"
        best, best_length = None, -1
        for item in items:
            path = item["path"].rstrip("/") or "/"
            matches = current == path or (not item.get("exact") and path != "/" and current.startswith(path + "/"))
            if matches and len(path) > best_length:
                best, best_length = item["path"], len(path)
        return best
