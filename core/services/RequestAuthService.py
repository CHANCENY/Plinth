"""
RequestAuthService
--------------------
Identifies the caller of a request from credentials in the request itself (an API key, a bearer token, a
signature ...) - the counterpart of the session cookie for clients that aren't a browser. It's the
framework's hook for apps that provide authentication: an app lists its authenticator classes in its
authenticators.py (see core/apps/AppManager.py) and Server runs them on every request before falling back
to the session.

The contract for an authenticator class (instantiated per request with the database connection):

    class MyAuthenticator:
        def __init__(self, db_connection): ...

        def authenticate(self, request):
            # No credentials this authenticator understands -> return None (the next authenticator, or
            # the session, gets a turn).
            # Credentials present but wrong/expired/revoked -> raise AuthenticationFailed(...). The request
            # is rejected with 401 on the spot - a client holding a dead token learns that immediately,
            # instead of being treated as anonymous.
            # Valid -> return an AuthResult.
            return AuthResult(user=user, scheme="mykey", credential=row, scopes=["notes.read"])

What a result does: `user` becomes the request's current_user - exactly as if that person had signed in with
a browser - so every route's permission list, has_permission(), roles and menus apply unchanged. `scopes`
(permission keys, or None for "everything the user can do") narrow what this credential may do: the user's
permissions are intersected with them for this request only (User.restrict_permissions - nothing is saved).
The result is also kept on request._auth for controllers that want to know how the caller authenticated
(request._auth.scheme, .credential, ...).
"""

import importlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.apps.AppManager import AppManager
from core.http.response import JsonResponse


class AuthenticationFailed(Exception):
    """Credentials were presented but are not valid. Becomes a JSON error response (default 401)."""

    def __init__(self, error: str, description: str = "", status: int = 401, headers: Optional[Dict[str, str]] = None):
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status = status
        self.headers = dict(headers or {})

    def to_response(self) -> JsonResponse:
        body = {"error": self.error}
        if self.description:
            body["error_description"] = self.description
        headers = {"Cache-Control": "no-store", **self.headers}
        return JsonResponse(body, status=self.status, headers=headers)


@dataclass
class AuthResult:
    user: Any                           # the User the credential belongs to
    scheme: str                         # "apikey", "bearer", "jwt", "basic", "hmac" ...
    credential: Any = None              # the stored credential row, if any
    scopes: Optional[List[str]] = None  # None = no restriction; otherwise permission keys that narrow the user
    expires_at: Optional[int] = None    # unix time the credential stops working, if it expires


class RequestAuthService:
    _classes: Dict[str, type] = {}  # dotted path -> class, imported once per process

    def __init__(self, db_connection, app_manager: Optional[AppManager] = None):
        self.db = db_connection
        self.apps = app_manager or AppManager()

    def authenticators(self) -> List[str]:
        cache = self.apps.load_cache() or {}
        return [entry["class"] for entry in cache.get("authenticators", [])]

    def authenticate(self, request) -> Optional[AuthResult]:
        """The first authenticator to recognise credentials decides. None means anonymous (fall back to the session)."""
        for dotted in self.authenticators():
            result = self._load(dotted)(self.db).authenticate(request)
            if result is not None:
                if result.scopes is not None:
                    result.user.restrict_permissions(result.scopes)
                return result
        return None

    @classmethod
    def _load(cls, dotted: str) -> type:
        if dotted not in cls._classes:
            module_name, _, class_name = dotted.rpartition(".")
            cls._classes[dotted] = getattr(importlib.import_module(module_name), class_name)
        return cls._classes[dotted]
