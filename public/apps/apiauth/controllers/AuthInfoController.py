"""
AuthInfoController
--------------------
Endpoints for clients to discover and test authentication:

    GET   /api/auth/schemes   public - what authentication this server accepts and how to use each scheme
    GET   /api/auth/me        any authenticated caller (session, key, token ...) - who am I, how did I
                              authenticate, and what am I allowed to do with this credential
    POST  /api/auth/logout    revokes the access token the request was made with (bearer / jwt callers)
"""

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from public.apps.apiauth.services import Http
from public.apps.apiauth.services.CredentialService import CredentialService
from public.apps.apiauth.services.Settings import Settings

USAGE = {
    "apikey": {"how": "X-API-Key: <key>   (or Authorization: ApiKey <key>)", "get_credentials": "POST /api/keys"},
    "bearer": {"how": "Authorization: Bearer <access_token>", "get_credentials": "POST /api/oauth/token"},
    "jwt": {"how": "Authorization: Bearer <jwt access_token>", "get_credentials": "POST /api/oauth/token"},
    "basic": {"how": "Authorization: Basic base64(<key_id>:<key secret>)", "get_credentials": "POST /api/keys"},
    "hmac": {
        "how": 'Authorization: Signature keyId="<key_id>", timestamp="<unix>", nonce="<random>", signature="<hex HMAC-SHA256>"',
        "get_credentials": "POST /api/keys   (the response includes a signing_secret)",
        "signature_of": "METHOD \\n TARGET(path+query) \\n TIMESTAMP \\n NONCE \\n SHA256(body) hex",
    },
}


class AuthInfoController(ControllerBase):

    @route("/api/auth/schemes", methods=["GET"])
    def schemes(self, request):
        settings = Settings()
        data = {
            "schemes": {name: USAGE[name] for name in settings.schemes if name in USAGE},
            "token_endpoint": "/api/oauth/token",
            "grants": settings.grants,
            "token_format": settings.token_format,
            "access_token_ttl": settings.access_ttl,
            "refresh_token_ttl": settings.refresh_ttl,
        }
        problems = settings.problems()
        if problems:
            data["configuration_problems"] = problems
        return self.json(data)

    @route("/api/auth/me", methods=["GET"], permission=["authenticated"])
    def me(self, request):
        user = self.require_auth(request)
        auth = getattr(request, "_auth", None)
        authentication = {"scheme": "session", "scopes": None, "expires_at": None}
        if auth is not None:
            authentication = {"scheme": auth.scheme, "scopes": auth.scopes, "expires_at": auth.expires_at}
            key_id = getattr(auth.credential, "key_id", None)
            if key_id:
                authentication["key_id"] = key_id
        return self.json(
            {
                "user": {"id": user.uid, "name": user.name, "email": user.email, "role": user.role, "status": user.status},
                "permissions": user.permissions,  # what THIS credential can do - already narrowed by its scopes
                "authentication": authentication,
            }
        )

    @route("/api/auth/logout", methods=["POST"], permission=["authenticated"])
    def logout(self, request):
        auth = getattr(request, "_auth", None)
        if auth is None or auth.scheme not in ("bearer", "jwt") or auth.credential is None:
            return Http.error("invalid_request", "logout revokes the access token the request used - this request wasn't made with one")
        CredentialService(self.db()).revoke_row(auth.credential)
        return self.no_content()
