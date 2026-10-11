"""
UserController
----------------
Admin CRUD for the users table: list/create/edit/delete. Every route here
requires the "admin.users.manage" permission (see the @route decorators below) - PermissionMiddleware
enforces that before any of these methods ever runs.

Assigning access is a separate, stricter thing: choosing a user's role and editing their
permissions (users.permissions_raw) is administrator-only, enforced here on the server - not just
hidden in the form. Someone who merely holds admin.users.manage can still add and edit users, but
new accounts they create get the default role, and the role/permission fields are neither shown to
them nor read from what they submit.

  * Create: the administrator picks a role from the roles table; the permissions that role grants
    are collected and saved into permissions_raw as JSON automatically (User.save()).
  * Edit: an administrator can change the role and tick/untick individual permissions. The stored
    permissions are what's saved - changing the role in the form pre-ticks that role's permissions
    (in the browser), but the boxes are what counts.
"""

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from core.models.Role import Role
from core.models.User import User
from core.services.AccessService import AccessService, as_list

EDITABLE_STATUSES = ("active", "pending", "suspended", "blocked")  # "deleted" is handled by delete(), not here


class UserController(ControllerBase):

    @route("/admin/users", methods=["GET", "POST"], permission=["admin.users.manage"])
    def index(self, request):
        users = list(User.objects(self.db()).exclude(status="deleted").order_by("-created_at"))
        roles_by_key = {role.role_key: role for role in AccessService(self.db()).roles()}
        return self.view(
            "admin:users/index",
            {"users": users, "roles_by_key": roles_by_key, "active_nav": "users"},
            request=request,
        )

    @route("/admin/users/new", methods=["GET", "POST"], permission=["admin.users.manage"])
    def create(self, request):
        can_assign, roles = self._access_context(request)

        if request.is_method("POST"):
            rules = {
                "name": "required|min:2",
                "email": "required|email",
                "password": "required|min:8",
            }
            if can_assign and roles:
                rules["role"] = f"required|in:{','.join(role.role_key for role in roles)}"
            validator = self.validate(request, rules)
            if validator.fails():
                return self._form("admin:users/create", request, errors=validator.get_errors(), old=request.all())

            data = validator.validated()
            if User.find_by_email(self.db(), data["email"]) is not None:
                return self._form(
                    "admin:users/create",
                    request,
                    errors={"email": ["An account with that email already exists."]},
                    old=data,
                )

            user = User(self.db(), name=data["name"], email=data["email"], password=data["password"])
            user.role = data.get("role") or Role.DEFAULT_KEY
            # No permissions are set here on purpose: User.save() collects the chosen role's
            # permissions and stores them in permissions_raw as JSON.
            user.set_status("active").mark_verified().save()
            return self.redirect("/admin/users")

        return self._form("admin:users/create", request)

    @route("/admin/users/<uid>/edit", methods=["GET", "POST"], permission=["admin.users.manage"])
    def edit(self, request):
        user = self._find_or_404(request)
        if user is None:
            return self.not_found("User not found")

        can_assign, roles = self._access_context(request)
        access = AccessService(self.db())

        if request.is_method("POST"):
            rules = {
                "name": "required|min:2",
                "email": "required|email",
                "status": f"required|in:{','.join(EDITABLE_STATUSES)}",
            }
            if can_assign and roles:
                rules["role"] = f"required|in:{','.join(role.role_key for role in roles)}"
            validator = self.validate(request, rules)
            if validator.fails():
                return self._form("admin:users/edit", request, errors=validator.get_errors(), old=request.all(), user=user)

            data = validator.validated()
            existing = User.find_by_email(self.db(), data["email"])
            if existing is not None and existing.uid != user.uid:
                return self._form(
                    "admin:users/edit",
                    request,
                    errors={"email": ["Another account already uses that email."]},
                    old=data,
                    user=user,
                )

            if can_assign:
                new_role = data.get("role", user.role)
                permissions = self._posted_permissions(request, access)
                chosen = next((role for role in roles if role.role_key == new_role), None)
                if chosen is not None and chosen.is_superuser:
                    permissions = ["*"]  # the administrator role always means full access
                if user.is_admin() and "*" not in permissions and not self._other_admins_exist(user.uid):
                    return self._form(
                        "admin:users/edit",
                        request,
                        errors={"permissions": ["This is the only administrator - they have to keep full access."]},
                        old=request.all(),
                        user=user,
                    )
                user.role = new_role
                user.permissions = permissions

            user.name = data["name"]
            user.email = data["email"]
            user.status = data["status"]

            new_password = request.get("password")
            if new_password:
                user.set_password(new_password)

            user.save()
            return self.redirect("/admin/users")

        return self._form("admin:users/edit", request, user=user)

    @route("/admin/users/<uid>/delete", methods=["POST"], permission=["admin.users.manage"])
    def delete(self, request):
        user = self._find_or_404(request)
        if user is None:
            return self.not_found("User not found")

        current_user = self.require_auth(request)
        if current_user is not None and current_user.uid == user.uid:
            return self.forbidden("You can't delete your own account while signed in.")

        if user.is_admin() and not self._other_admins_exist(user.uid):
            return self.forbidden("Can't delete the last remaining admin.")

        user.delete()  # soft delete - see User.delete()
        return self.redirect("/admin/users")

    # ------------------------------------------------------------------ #
    def _find_or_404(self, request):
        return User.find(self.db(), request.get_route_parameter("uid"))

    def _access_context(self, request):
        """(can_assign, roles): whether the signed-in user may choose roles / edit permissions
        (administrators only), and the roles available to choose from."""
        actor = self.require_auth(request)
        can_assign = bool(actor is not None and actor.is_admin())
        return can_assign, AccessService(self.db()).roles()

    @staticmethod
    def _posted_permissions(request, access: AccessService):
        """The permissions ticked on the edit form: ["*"] if "all permissions" is ticked, else the
        ticked permission keys that actually exist."""
        if request.get("permissions_all"):
            return ["*"]
        valid = {permission.permission_key for permission in access.permissions()}
        return [key for key in as_list(request.get("permissions")) if key in valid]

    def _other_admins_exist(self, exclude_uid) -> bool:
        """True if some other active account holds full access ("*")."""
        candidates = (
            User.objects(self.db())
            .filter(permissions_raw__contains="*")
            .exclude(uid=exclude_uid)
            .exclude(status="deleted")
        )
        return any(candidate.is_admin() and candidate.status == "active" for candidate in list(candidates))

    def _form(self, view, request, errors=None, old=None, user=None):
        can_assign, roles = self._access_context(request)
        context = {
            "errors": errors or {},
            "old": old or {},
            "active_nav": "users",
            "can_assign": can_assign,
            "roles": roles,
            "default_role": Role.DEFAULT_KEY,
        }
        if user is not None:
            context["user"] = user
            if can_assign:
                access = AccessService(self.db())
                if request.is_method("POST"):
                    shown = self._posted_permissions(request, access)
                else:
                    shown = user.permissions
                context.update(
                    groups=access.grouped_permissions(),
                    all_permissions="*" in shown,
                    selected_permissions=[] if shown == "*" else list(shown),
                    role_defaults=access.role_defaults(),
                )
        return self.view(view, context, request=request)
