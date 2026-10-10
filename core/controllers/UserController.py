"""
UserController
----------------
Admin CRUD for the users table: list/create/edit/delete. Every route here
requires the "users.manage" permission (see Routing.py) - PermissionMiddleware
enforces that before any of these methods ever runs, so there's no need to
re-check it here.
"""

from core.controllers.ControllerBase import ControllerBase
from core.models.User import User

EDITABLE_STATUSES = ("active", "pending", "suspended", "blocked")  # "deleted" is handled by delete(), not here
ROLES = ("member", "admin")


class UserController(ControllerBase):

    def index(self, request):
        users = list(User.objects(self.db()).exclude(status="deleted").order_by("-created_at"))
        return self.view("users/index", {"users": users, "active_nav": "users"}, request=request)

    def create(self, request):
        if request.is_method("POST"):
            validator = self.validate(
                request,
                {
                    "name": "required|min:2",
                    "email": "required|email",
                    "password": "required|min:8",
                    "role": f"required|in:{','.join(ROLES)}",
                },
            )
            if validator.fails():
                return self._form("users/create", request, errors=validator.get_errors(), old=request.all())

            data = validator.validated()
            if User.find_by_email(self.db(), data["email"]) is not None:
                return self._form(
                    "users/create",
                    request,
                    errors={"email": ["An account with that email already exists."]},
                    old=data,
                )

            user = User(self.db(), name=data["name"], email=data["email"], password=data["password"])
            user.role = data["role"]
            user.permissions = "*" if data["role"] == "admin" else []
            user.set_status("active").mark_verified().save()
            return self.redirect("/admin/users")

        return self._form("users/create", request)

    def edit(self, request):
        user = self._find_or_404(request)
        if user is None:
            return self.not_found("User not found")

        if request.is_method("POST"):
            validator = self.validate(
                request,
                {
                    "name": "required|min:2",
                    "email": "required|email",
                    "status": f"required|in:{','.join(EDITABLE_STATUSES)}",
                    "role": f"required|in:{','.join(ROLES)}",
                },
            )
            if validator.fails():
                return self._form("users/edit", request, errors=validator.get_errors(), old=request.all(), user=user)

            data = validator.validated()
            existing = User.find_by_email(self.db(), data["email"])
            if existing is not None and existing.uid != user.uid:
                return self._form(
                    "users/edit",
                    request,
                    errors={"email": ["Another account already uses that email."]},
                    old=data,
                    user=user,
                )

            user.name = data["name"]
            user.email = data["email"]
            user.status = data["status"]
            user.role = data["role"]
            user.permissions = "*" if data["role"] == "admin" else []

            new_password = request.get("password")
            if new_password:
                user.set_password(new_password)

            user.save()
            return self.redirect("/admin/users")

        return self._form("users/edit", request, user=user)

    def delete(self, request):
        user = self._find_or_404(request)
        if user is None:
            return self.not_found("User not found")

        current_user = self.require_auth(request)
        if current_user is not None and current_user.uid == user.uid:
            return self.forbidden("You can't delete your own account while signed in.")

        if user.role == "admin":
            remaining_admins = (
                User.objects(self.db())
                .filter(role="admin")
                .exclude(status="deleted")
                .exclude(uid=user.uid)
                .count()
            )
            if remaining_admins == 0:
                return self.forbidden("Can't delete the last remaining admin.")

        user.delete()  # soft delete - see User.delete()
        return self.redirect("/admin/users")

    # ------------------------------------------------------------------ #
    def _find_or_404(self, request):
        return User.find(self.db(), request.get_route_parameter("uid"))

    def _form(self, view, request, errors=None, old=None, user=None):
        context = {"errors": errors or {}, "old": old or {}, "active_nav": "users"}
        if user is not None:
            context["user"] = user
        return self.view(view, context, request=request)
