from core.http.middleware.BaseMiddleware import BaseMiddleware
from core.http.response import Response


class AuthenticationMiddleware(BaseMiddleware):

    def link(self, route, current_user, next):

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

        return next()