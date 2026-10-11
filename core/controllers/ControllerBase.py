"""
ControllerBase
----------------
Abstract base class for controllers. Cannot be instantiated directly - only
subclassed. Bundles the helpers a controller method typically needs (a view
renderer, JSON/redirect responses, request validation, auth/session access,
a DB connection) so a controller stays focused on its own logic instead of
wiring up every service by hand.

Routing.resolve_controller() creates a NEW instance of the controller class
for every single request (see core/http/routing/Routing.py), so nothing here
holds per-request state on self - db()/env()/views() are cached on self
purely to avoid reconnecting/rebuilding them if a method calls them more than
once during the SAME request; session()/auth() are always request-scoped and
are deliberately never cached on self, since they're tied to whichever
Request was passed in for that one call.

Project layout assumed:
    project/
        core/
            controllers/
                ControllerBase.py   <- this file
                PageController.py
                UserController.py   <- your own controllers, each extending ControllerBase

Usage
-----
    from core.controllers.ControllerBase import ControllerBase
    from core.models.User import User

    class UserController(ControllerBase):
        def index(self, request):
            users = User.all(self.db())
            return self.view("users/index", {"users": users})
            # or, for an API: return self.json([u.to_dict() for u in users])

        def store(self, request):
            validator = self.validate(request, {
                "email": "required|email",
                "password": "required|min:8|confirmed",
            })
            if validator.fails():
                return self.json({"errors": validator.get_errors()}, status=422)

            user = User(self.db(), **validator.validated())
            user.save()
            return self.redirect("/users")

        def dashboard(self, request):
            if self.require_auth(request) is None:
                return self.redirect("/login")
            return self.view("dashboard/index", request=request)  # current_user is added automatically
"""

from typing import Any, Dict, Optional

from core.database.DatabaseConnection import DatabaseConnection
from core.http.response import JsonResponse, RedirectResponse, Response
from core.services.AuthService import AuthService
from core.services.EnvService import EnvService
from core.services.SessionService import SessionService
from core.services.TemplateService import TemplateService
from core.services.ValidationService import ValidationService


class ControllerBase:
    def __init__(self):
        if type(self) is ControllerBase:
            raise TypeError("ControllerBase is abstract and cannot be instantiated directly - subclass it.")
        self._db: Optional[DatabaseConnection] = None
        self._env: Optional[EnvService] = None
        self._views: Optional[TemplateService] = None

    # ------------------------------------------------------------------ #
    # shared, request-independent resources - cached on self per instance
    # ------------------------------------------------------------------ #
    def db(self) -> DatabaseConnection:
        if self._db is None:
            self._db = DatabaseConnection()
        return self._db

    def env(self) -> EnvService:
        if self._env is None:
            self._env = EnvService()
        return self._env

    def views(self) -> TemplateService:
        if self._views is None:
            self._views = TemplateService(env=self.env())
        return self._views

    # ------------------------------------------------------------------ #
    # per-request resources - always freshly built from the given request,
    # never cached on self
    # ------------------------------------------------------------------ #
    def session(self, request) -> SessionService:
        return SessionService(self.db(), env=self.env()).start(request)

    def auth(self, request) -> AuthService:
        return AuthService(self.db(), self.session(request))

    def require_auth(self, request):
        """
        Returns the current logged-in User, or None if the request is unauthenticated.
        Server.py already resolves this once per request and caches it as
        request._current_user, which this reuses if present - so calling this (or view(...,
        request=...)) more than once per request doesn't re-query the session/user each time.
        Falls back to resolving it directly when that cache isn't there (e.g. calling a
        controller method directly in a test, without going through Server.py).
        """
        if hasattr(request, "_current_user"):
            return request._current_user
        return self.auth(request).user()

    # ------------------------------------------------------------------ #
    # response helpers
    # ------------------------------------------------------------------ #
    def view(
        self,
        view: str,
        context: Optional[Dict[str, Any]] = None,
        theme: Optional[str] = None,
        status: int = 200,
        headers: Optional[Dict[str, str]] = None,
        request=None,
    ) -> Response:
        """
        Renders `view` with `context`. Pass `request` (every controller method
        already has one) and the currently logged-in User (or None) is added to
        the context automatically as `current_user` - that's what partials/nav.html
        (and anything else showing who's signed in) reads, so any page whose layout
        includes that nav needs `request` passed here or it will always render as
        logged out, regardless of the real session state. The same call also adds
        `dashboard_menu()`, which returns the sidebar groups the signed-in user may see
        (declared by apps in menus.py - see core/services/MenuService.py).
        """
        full_context = dict(context or {})
        if request is not None:
            current_user = self.require_auth(request)
            full_context.setdefault("current_user", current_user)
            # Called by the admin layout's nav (partials/nav.html) - a function rather than a
            # precomputed list so pages that don't render the nav never pay for building it.
            full_context.setdefault("dashboard_menu", lambda: self._dashboard_menu(current_user, request))
        return self.views().render_response(view, full_context, theme, status, headers)

    @staticmethod
    def _dashboard_menu(current_user, request):
        from core.services.MenuService import MenuService  # imported lazily - only the admin layout needs it

        return MenuService().build(current_user, request.get_path_info())

    def json(self, data: Any = None, status: int = 200, headers: Optional[Dict[str, str]] = None) -> JsonResponse:
        return JsonResponse(data, status=status, headers=headers)

    def redirect(self, url: str, status: int = Response.HTTP_FOUND, headers: Optional[Dict[str, str]] = None) -> RedirectResponse:
        return RedirectResponse(url, status=status, headers=headers)

    def back(self, request, default: str = "/", status: int = Response.HTTP_FOUND) -> RedirectResponse:
        """Redirects to the referring page, falling back to `default` if there isn't one."""
        return self.redirect(request.referer() or default, status=status)

    def no_content(self, status: int = Response.HTTP_NO_CONTENT, headers: Optional[Dict[str, str]] = None) -> Response:
        return Response(b"", status=status, headers=headers)

    def not_found(self, message: str = "Not Found") -> Response:
        return Response(message, status=Response.HTTP_NOT_FOUND)

    def forbidden(self, message: str = "Forbidden") -> Response:
        return Response(message, status=Response.HTTP_FORBIDDEN)

    def error(self, message: str = "Internal Server Error", status: int = Response.HTTP_INTERNAL_SERVER_ERROR) -> Response:
        return Response(message, status=status)

    # ------------------------------------------------------------------ #
    # validation
    # ------------------------------------------------------------------ #
    def validate(self, request, rules: Dict[str, str]) -> ValidationService:
        """
        Runs ValidationService against the request's combined query+body data
        (request.all()) and returns the validator, already run once:

            validator = self.validate(request, {"email": "required|email"})
            if validator.fails():
                return self.json({"errors": validator.get_errors()}, status=422)
            data = validator.validated()
        """
        validator = ValidationService.for_request(request, rules)
        validator.passes()
        return validator
