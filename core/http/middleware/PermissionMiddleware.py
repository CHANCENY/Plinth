from core.http.middleware.BaseMiddleware import BaseMiddleware
from core.http.response import Response


class PermissionMiddleware(BaseMiddleware):
    """
    Enforces each route's "permission" list (see Routing.py's route table):

        ["*"]              public - no login required
        ["authenticated"]  any logged-in, active user - no specific permission needed
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

        if "authenticated" in permissions:
            return next()

        for permission in permissions:
            if self.has_permission(
                current_user,
                permission
            ):
                return next()

        return Response(
            "Access denied",
            Response.HTTP_FORBIDDEN
        )