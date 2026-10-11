import importlib
import re
from core.http.middleware.PermissionMiddleware import PermissionMiddleware
from core.http.request import Request
from core.http.response import Response


class Routing:

    def __init__(self, request: Request):
        self.request = request
        self.uri = request.get_request_uri()
        self.method = request.method.upper()

        self.routes = []
        # On by default so every route's "permission" entry is actually enforced - call
        # set_middlewares([]) after construction if you need to disable this (e.g. in a test
        # that doesn't care about permissions). current_user still needs to be supplied via
        # set_current_user() for this to do anything meaningful - see Server.py.
        self.middlewares = [PermissionMiddleware()]

        self.current_user = None
        self.matched_route = None
        self.route_parameters = {}
        # No routes are hard-coded here anymore. Every route - including the default
        # login/register/admin ones - is declared with @route on a controller method inside
        # a registered app (see app.py, public/apps/admin/, and core/apps/AppManager.py) and
        # loaded from the discovery cache below. Run `python console.py apps:discover`
        # after a fresh checkout or whenever an app's routes change.
        self.set_routes([])
        self._load_app_routes()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_routes(self, routes):
        self.routes = routes
        return self

    def _load_app_routes(self):
        """
        Loads every route from whatever `python console.py apps:discover` last found
        (see core/apps/AppManager.py) - a plain file read, no filesystem scanning here.
        All routes now come from apps (the default ones from the built-in "admin" app),
        so a missing/empty cache means the server has no routes at all and every
        request 404s until discovery is run.
        """
        from core.apps.AppManager import AppManager

        cache = AppManager().load_cache()
        if cache:
            self.routes.extend(cache.get("routes", []))

    def add_route(self, path, method, controller, permission=None):
        if isinstance(method, str):
            method = [method]

        self.routes.append({
            "path": path,
            "method": [item.upper() for item in method],
            "controller": controller,
            "permission": permission or ["*"],
        })

        return self

    def set_middlewares(self, middlewares):
        self.middlewares = middlewares
        return self

    def add_middleware(self, middleware):
        self.middlewares.append(middleware)
        return self

    def set_current_user(self, current_user):
        self.current_user = current_user
        return self

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self):
        route = self.match_route()

        if route is None:
            return Response(
                "Route not found",
                Response.HTTP_NOT_FOUND
            )

        self.matched_route = route

        # Make dynamic route parameters available to Request.
        self.route_parameters = route["parameters"]

        if hasattr(self.request, "set_route_parameters"):
            self.request.set_route_parameters(
                self.route_parameters
            )

        # Check HTTP method.
        if not self.method_allowed(route):
            response = Response(
                "Method Not Allowed",
                Response.HTTP_METHOD_NOT_ALLOWED
            )

            allowed_methods = ", ".join(route["method"])

            response.set_header(
                "Allow",
                allowed_methods
            )

            return response

        return self.run_middlewares(
            route,
            self.current_user,
            self.call_controller
        )

    # ------------------------------------------------------------------
    # Route matching
    # ------------------------------------------------------------------

    def match_route(self):
        request_path = self.get_request_path()

        for route in self.routes:
            route_path = route["path"]

            parameters = self.match_path(
                route_path,
                request_path
            )

            if parameters is None:
                continue

            return {
                **route,
                "parameters": parameters,
            }

        return None

    def get_request_path(self):
        """
        Return only the URI path.

        Example:

            /pages/123?sort=latest

        becomes:

            /pages/123
        """

        if "?" in self.uri:
            return self.uri.split("?", 1)[0]

        return self.uri

    def match_path(self, route_path, request_path):
        """
        Match a route path and extract dynamic parameters.

        Example:

            /pages/<page_id>

        against:

            /pages/123

        returns:

            {
                "page_id": "123"
            }
        """

        route_parts = self.normalize_path(route_path).split("/")
        request_parts = self.normalize_path(request_path).split("/")

        if len(route_parts) != len(request_parts):
            return None

        parameters = {}

        for route_part, request_part in zip(
                route_parts,
                request_parts
        ):

            # Dynamic parameter.
            if (
                    route_part.startswith("<")
                    and route_part.endswith(">")
            ):
                parameter_name = route_part[1:-1]

                if not parameter_name:
                    return None

                parameters[parameter_name] = request_part

                continue

            # Static route segment.
            if route_part != request_part:
                return None

        return parameters

    @staticmethod
    def normalize_path(path):
        path = path.strip()

        if not path:
            return "/"

        if not path.startswith("/"):
            path = "/" + path

        if len(path) > 1:
            path = path.rstrip("/")

        return path

    # ------------------------------------------------------------------
    # HTTP methods
    # ------------------------------------------------------------------

    def method_allowed(self, route):
        allowed_methods = [
            method.upper()
            for method in route["method"]
        ]

        return self.method in allowed_methods

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------

    def run_middlewares(self, route, current_user, controller_callable):
        """
        Execute middleware sequentially.

        Each middleware must provide:

            link(route, current_user, next)

        The middleware may either:

            return True

        or:

            return False

        or return a Response directly.

        A Response returned by middleware immediately stops
        execution and becomes the final response.
        """

        middlewares = list(self.middlewares)

        def execute(index):
            if index >= len(middlewares):
                return controller_callable()

            middleware = self.resolve_middleware(
                middlewares[index]
            )

            next_callable = lambda: execute(index + 1)

            result = middleware.link(
                route,
                current_user,
                next_callable
            )

            # Middleware explicitly denied access.
            if result is False:
                return Response(
                    "Access Denied",
                    Response.HTTP_FORBIDDEN
                )

            # Middleware returned a response.
            if isinstance(result, Response):
                return result

            # Middleware returned True.
            if result is True:
                return execute(index + 1)

            # If middleware forgot to return anything,
            # treat it as a denial rather than accidentally
            # granting access.
            if result is None:
                return Response(
                    "Access Denied",
                    Response.HTTP_FORBIDDEN
                )

            return result

        return execute(0)

    # ------------------------------------------------------------------
    # Middleware resolution
    # ------------------------------------------------------------------

    def resolve_middleware(self, middleware):
        """
        Accept:

            middleware object

        or:

            "package.module.ClassName"
        """

        if not isinstance(middleware, str):
            return middleware

        middleware_class = self.resolve_class(
            middleware
        )

        return middleware_class()

    # ------------------------------------------------------------------
    # Controller
    # ------------------------------------------------------------------

    def call_controller(self):
        controller = self.matched_route["controller"]

        controller_callable = self.resolve_controller(
            controller
        )

        result = controller_callable(
            self.request
        )

        if not isinstance(result, Response):
            raise TypeError(
                "Controller must return an instance of Response. "
                f"Got {type(result).__name__}."
            )

        return result

    def resolve_controller(self, controller):
        """
        Resolve:

            "App.Controllers.PageController::index"

        into:

            PageController().index
        """

        if callable(controller):
            return controller

        if not isinstance(controller, str):
            raise TypeError(
                "Controller must be a callable or "
                "ClassName::method_name string."
            )

        if "::" not in controller:
            raise ValueError(
                "Controller must use the format "
                "'ClassName::method_name'."
            )

        class_name, method_name = controller.split(
            "::",
            1
        )

        controller_class = self.resolve_class(
            class_name
        )

        controller_instance = controller_class()

        if not hasattr(
                controller_instance,
                method_name
        ):
            raise AttributeError(
                f"Controller {class_name} does not have "
                f"method {method_name}."
            )

        method = getattr(
            controller_instance,
            method_name
        )

        if not callable(method):
            raise TypeError(
                f"{controller} is not callable."
            )

        return method

    # ------------------------------------------------------------------
    # Class resolution
    # ------------------------------------------------------------------

    def resolve_class(self, class_name):
        """
        Resolve a Python class.

        Supported format:

            app.controllers.PageController

        or:

            app.controllers.page_controller.PageController
        """

        if "." not in class_name:
            raise ValueError(
                "Class name must contain its Python module path. "
                "Example: "
                "'app.controllers.PageController'"
            )

        module_name, class_name_only = class_name.rsplit(
            ".",
            1
        )

        module = importlib.import_module(
            f"{module_name}.{class_name_only}"
        )

        if not hasattr(module, class_name_only):
            raise AttributeError(
                f"Class '{class_name_only}' was not found "
                f"in module '{module_name}'."
            )

        return getattr(
            module,
            class_name_only
        )
