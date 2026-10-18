"""
ApiKeyController
------------------
Create and manage API keys. A key belongs to a user and acts as that user (optionally limited to some of their
permissions). The secret is shown once, in the creation response, and can never be retrieved again - only
rotated.

    GET     /api/keys                       your keys (never their secrets)
    POST    /api/keys                       create one: {"name": "ci", "scopes": ["notes.read"], "expires_in_days": 90}
                                            -> 201 {"key": {...}, "secret": "pk_xxx.yyy", "signing_secret": "..."}
    DELETE  /api/keys/<key_id>              revoke a key (and the tokens it issued)
    POST    /api/keys/<key_id>/rotate       a new secret for the same key id; the old one dies immediately

All require the "apiauth.keys.manage" permission. You can only manage your own keys, unless you also hold
"apiauth.keys.admin". `scopes` are permission keys you yourself hold; omit it for a key as powerful as you are.
"""

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from core.services.RequestAuthService import AuthenticationFailed
from public.apps.apiauth.models.ApiKey import ApiKey
from public.apps.apiauth.services import Http
from public.apps.apiauth.services.CredentialService import CredentialService

MAX_KEYS_PER_USER = 50


class ApiKeyController(ControllerBase):

    @route("/api/keys", methods=["GET"], permission=["apiauth.keys.manage"])
    def index(self, request):
        user = self.require_auth(request)
        credentials = CredentialService(self.db())
        keys = user.api_keys.order_by("-id")
        return self.json({"keys": [credentials.key_summary(key) for key in keys]})

    @route("/api/keys", methods=["POST"], permission=["apiauth.keys.manage"])
    def create(self, request):
        user = self.require_auth(request)
        credentials = CredentialService(self.db())
        data = Http.params(request)

        name = str(data.get("name") or "").strip()
        if not name or len(name) > 100:
            return Http.error("invalid_request", "name is required (100 characters at most)")
        days = data.get("expires_in_days")
        try:
            days = int(days) if days not in (None, "") else None
        except (TypeError, ValueError):
            return Http.error("invalid_request", "expires_in_days must be a whole number")
        if days is not None and not 1 <= days <= 3650:
            return Http.error("invalid_request", "expires_in_days must be between 1 and 3650")
        if user.api_keys.filter(revoked_at__isnull=True).count() >= MAX_KEYS_PER_USER:
            return Http.error("limit_reached", f"you already have {MAX_KEYS_PER_USER} active keys - revoke some first", status=409)

        try:
            scopes = credentials.resolve_scopes(user, credentials.parse_scope_param(data.get("scopes")))
        except AuthenticationFailed as exc:
            return exc.to_response()

        key, plaintext = credentials.create_key(user, name, scopes, days)
        body = {
            "key": credentials.key_summary(key),
            "secret": plaintext,
            "warning": "Store the secret now - it is not shown again.",
        }
        signing_secret = credentials.signing_secret(key)
        if signing_secret:
            body["signing_secret"] = signing_secret
        return self.json(body, status=201, headers={"Cache-Control": "no-store"})

    @route("/api/keys/<key_id>", methods=["DELETE"], permission=["apiauth.keys.manage"])
    def revoke(self, request):
        key = self._owned_key(request)
        if key is None:
            return Http.error("not_found", "no such key", status=404)
        CredentialService(self.db()).revoke_key(key)
        return self.no_content()

    @route("/api/keys/<key_id>/rotate", methods=["POST"], permission=["apiauth.keys.manage"])
    def rotate(self, request):
        key = self._owned_key(request)
        if key is None:
            return Http.error("not_found", "no such key", status=404)
        if not key.is_usable:
            return Http.error("invalid_request", "a revoked or expired key can't be rotated - create a new one", status=409)
        credentials = CredentialService(self.db())
        plaintext = credentials.rotate_key(key)
        body = {"key": credentials.key_summary(key), "secret": plaintext, "warning": "Store the secret now - it is not shown again."}
        signing_secret = credentials.signing_secret(key)
        if signing_secret:
            body["signing_secret"] = signing_secret
        return self.json(body, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ #
    def _owned_key(self, request):
        """The key named in the URL if it's yours (or you hold apiauth.keys.admin) - otherwise None, which
        answers 404 so you can't probe which key ids exist."""
        user = self.require_auth(request)
        key = ApiKey.objects(self.db()).filter(key_id=request.get_route_parameter("key_id")).first()
        if key is None or (key.uid != user.uid and not user.has_permission("apiauth.keys.admin")):
            return None
        return key
