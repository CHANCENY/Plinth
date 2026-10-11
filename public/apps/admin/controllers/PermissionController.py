"""
PermissionController
----------------------
The admin Permissions pages. Every route here is administrator-only (permission=["administrator"],
enforced by PermissionMiddleware before any of these methods runs) - it is not a permission that
can be granted to a role.

    GET/POST  /admin/permissions                   the matrix: one row per permission, one checkbox
                                                   column per role (Drupal-style); POST saves it
    GET/POST  /admin/permissions/new               add a permission
    GET/POST  /admin/permissions/<pid>/edit        edit a permission
    POST      /admin/permissions/<pid>/delete      delete a permission

Permissions come from two places (Permission.source). Ones an app declares in its permissions.py
("app") are managed in code - `apps:discover` keeps them in sync - so they can be ticked in the
matrix here but not edited or deleted. Ones added on this page ("custom") are fully editable, and
survive discovery as long as the app they belong to is still registered.
"""

from core.apps.AppManager import AppManager
from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from core.models.Permission import Permission
from core.services.AccessService import KEY_SUFFIX_RE, MAX_KEY_LENGTH, AccessService, as_list


class PermissionController(ControllerBase):

    # ------------------------------------------------------------------ #
    # the matrix
    # ------------------------------------------------------------------ #
    @route("/admin/permissions", methods=["GET", "POST"], permission=["administrator"])
    def index(self, request):
        access = AccessService(self.db())

        if request.is_method("POST"):
            # one checkbox group per role: name="grant:<role key>", value="<permission key>"
            posted = {role.role_key: as_list(request.get(f"grant:{role.role_key}")) for role in access.roles()}
            summary = access.save_matrix(posted, apply_to_users=bool(request.get("apply_to_users")))
            return self.redirect(f"/admin/permissions?saved=1&users={summary['users_updated']}")

        return self.view(
            "admin:permissions/index",
            {
                "roles": access.roles(),
                "groups": access.grouped_permissions(),
                "grants": access.grants(),
                "saved": bool(request.get("saved")),
                "users_updated": request.query.get_int("users", 0),
                "active_nav": "permissions",
            },
            request=request,
        )

    # ------------------------------------------------------------------ #
    # add
    # ------------------------------------------------------------------ #
    @route("/admin/permissions/new", methods=["GET", "POST"], permission=["administrator"])
    def create(self, request):
        apps = AppManager().registered_apps()

        if request.is_method("POST"):
            validator = self.validate(
                request,
                {
                    "app": f"required|in:{','.join(apps)}",
                    "key": f"required|max:{MAX_KEY_LENGTH - 20}",
                    "label": "required|min:2|max:255",
                    "description": "max:500",
                },
            )
            errors = validator.get_errors() if validator.fails() else {}
            data = validator.validated()

            full_key = ""
            if "key" not in errors and "app" not in errors:
                full_key = f"{data['app']}.{data['key']}"
                if not KEY_SUFFIX_RE.match(data["key"]):
                    errors["key"] = ["Use lowercase letters, digits, '_' and '.' only, e.g. reports.export"]
                elif len(full_key) > MAX_KEY_LENGTH:
                    errors["key"] = [f"The full key can be at most {MAX_KEY_LENGTH} characters."]
                elif Permission.objects(self.db()).filter(permission_key=full_key).exists():
                    errors["key"] = [f"A permission with the key '{full_key}' already exists."]

            if errors:
                return self._form("admin:permissions/create", request, errors=errors, old=request.all(), apps=apps)

            AccessService(self.db()).create_permission(
                app=data["app"], name=data["key"], label=data["label"], description=data.get("description")
            )
            return self.redirect("/admin/permissions?saved=1")

        return self._form("admin:permissions/create", request, apps=apps)

    # ------------------------------------------------------------------ #
    # edit
    # ------------------------------------------------------------------ #
    @route("/admin/permissions/<pid>/edit", methods=["GET", "POST"], permission=["administrator"])
    def edit(self, request):
        permission = self._find(request)
        if permission is None:
            return self.not_found("Permission not found")
        if not permission.is_custom:
            return self.forbidden(self._managed_by_app_message(permission))

        if request.is_method("POST"):
            validator = self.validate(
                request,
                {"label": "required|min:2|max:255", "description": "max:500"},
            )
            if validator.fails():
                return self._form(
                    "admin:permissions/edit", request, errors=validator.get_errors(), old=request.all(), permission=permission
                )

            data = validator.validated()
            permission.label = data["label"]
            permission.description = data.get("description") or None
            permission.save()
            return self.redirect("/admin/permissions?saved=1")

        return self._form("admin:permissions/edit", request, permission=permission)

    # ------------------------------------------------------------------ #
    # delete
    # ------------------------------------------------------------------ #
    @route("/admin/permissions/<pid>/delete", methods=["POST"], permission=["administrator"])
    def delete(self, request):
        permission = self._find(request)
        if permission is None:
            return self.not_found("Permission not found")
        if not permission.is_custom:
            return self.forbidden(self._managed_by_app_message(permission))

        AccessService(self.db()).delete_permission(permission)
        return self.redirect("/admin/permissions?saved=1")

    # ------------------------------------------------------------------ #
    def _find(self, request):
        return Permission.find(self.db(), request.get_route_parameter("pid"))

    @staticmethod
    def _managed_by_app_message(permission) -> str:
        return (
            f"'{permission.permission_key}' is declared by the '{permission.app}' app, so it's managed in "
            f"public/apps/{permission.app}/permissions.py - edit or remove it there and run "
            f"`python console.py apps:discover`."
        )

    def _form(self, view, request, errors=None, old=None, permission=None, apps=None):
        context = {"errors": errors or {}, "old": old or {}, "active_nav": "permissions"}
        if permission is not None:
            context["permission"] = permission
        if apps is not None:
            context["apps"] = apps
        return self.view(view, context, request=request)
