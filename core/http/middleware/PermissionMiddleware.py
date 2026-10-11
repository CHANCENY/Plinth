from core.http.middleware.BaseMiddleware import BaseMiddleware
from core.http.response import Response


class PermissionMiddleware(BaseMiddleware):
    """
    Enforces each route's "permission" list (see Routing.py's route table):

        ["*"]              public - no login required
        ["authenticated"]  any logged-in, active user - no specific permission needed
        ["administrator"]  logged in, active, AND an administrator (holds "*") - for routes that
                           must never be delegated to a role, e.g. managing permissions themselves
        ["some.permission"]  logged in, active, AND holding that permission (or "*" on the user)

    Several permission strings in the list are OR'd together - holding any one of them is enough.
    """

    def link(self, route, current_user, next):

        permissions = route.get(
            "permission",
            ["*"]
        )

        if "*" in permissions:
            return next()

        if not self.is_authenticated(current_user):
            return Response(
                "Authentication required",
                Response.HTTP_UNAUTHORIZED
            )

        if not self.is_active(current_user):
            return Response(
                "Account is inactive",
                Response.HTTP_FORBIDDEN
            )

        if self.has_access(permissions, current_user):
            return next()

        return Response(
            "Access denied",
            Response.HTTP_FORBIDDEN
        )
