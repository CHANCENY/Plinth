from abc import ABC, abstractmethod


class BaseMiddleware(ABC):

    @abstractmethod
    def link(self, route, current_user, next):
        """
        Process the request before the controller is called.

        Parameters:
            route:
                The matched route definition, including dynamic
                parameters.

            current_user:
                The currently authenticated user, or None.

            next:
                Callable that continues the middleware chain.

        Returns:
            True:
                Continue to the next middleware.

            False:
                Deny access.

            Response:
                Immediately return a response.

        Example:

            def link(self, route, current_user, next):
                if current_user is None:
                    return False

                return next()
        """
        raise NotImplementedError(
            "Middleware must implement the link() method."
        )

    def is_authenticated(self, current_user):
        """
        Check whether a user is authenticated.
        """
        return current_user is not None

    def is_active(self, current_user):
        """
        Check whether the current user is active.
        """
        if not current_user:
            return False

        if isinstance(current_user, dict):
            return current_user.get("status") == "active"

        return getattr(current_user, "status", None) == "active"

    def is_verified(self, current_user):
        """
        Check whether the current user is verified.
        """
        if not current_user:
            return False

        if isinstance(current_user, dict):
            return bool(
                current_user.get("is_verified", False)
            )

        return bool(
            getattr(current_user, "is_verified", False)
        )

    def has_role(self, current_user, role):
        """
        Check whether the current user has a specific role.
        """
        if not current_user:
            return False

        if isinstance(current_user, dict):
            user_role = current_user.get("role")
        else:
            user_role = getattr(
                current_user,
                "role",
                None
            )

        if isinstance(user_role, (list, tuple, set)):
            return role in user_role

        return user_role == role

    def has_permission(self, current_user, permission):
        """
        Check whether the current user has a permission.
        """
        if not current_user:
            return False

        if isinstance(current_user, dict):
            permissions = current_user.get(
                "permissions",
                []
            )
        else:
            permissions = getattr(
                current_user,
                "permissions",
                []
            )

        if permissions == "*":
            return True

        return permission in permissions

    def route_parameter(self, route, name, default=None):
        """
        Retrieve a dynamic route parameter.

        Example:

            /pages/<page_id>

        route["parameters"]["page_id"]
        """
        parameters = route.get(
            "parameters",
            {}
        )

        return parameters.get(
            name,
            default
        )