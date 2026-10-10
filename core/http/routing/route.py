"""
route
------
Decorator for APP controller methods (see core/apps/AppManager.py, which
discovers these) - lets a method declare its own route instead of it living
in Routing.py's central table, the way every CORE route still does. Only app
controllers use this; nothing changes for core/controllers/*.

    from core.controllers.ControllerBase import ControllerBase
    from core.http.routing.route import route

    class WidgetController(ControllerBase):
        @route("/apps/admin/widgets", methods=["GET"])
        def index(self, request):
            ...

        @route("/apps/admin/widgets/<id>", methods=["GET", "POST"], permission=["widgets.manage"])
        def edit(self, request):
            ...

`methods` defaults to ["GET"]; `permission` defaults to ["*"] (public) - same
defaults and shape as a route dict in Routing.py's own table. Stack more than
one @route on a method if it should answer more than one path.

This only attaches metadata to the function (func._routes) - it does nothing
by itself. `python console.py apps:discover` is what actually reads it and
writes the routes into the cache Routing.py loads at request time (see
AppManager's docstring for why discovery happens there and not on every
request).
"""

from typing import Callable, List, Optional


def route(path: str, methods: Optional[List[str]] = None, permission: Optional[List[str]] = None) -> Callable:
    def decorator(func: Callable) -> Callable:
        func._routes = getattr(func, "_routes", []) + [
            {
                "path": path,
                "method": list(methods) if methods else ["GET"],
                "permission": list(permission) if permission else ["*"],
            }
        ]
        return func

    return decorator
