"""
ApiKeysPageController
-----------------------
The dashboard pages for API keys, using the admin layout (extends "admin:admin").

    GET/POST  /admin/api-keys                              your keys + the form that creates one (needs apiauth.keys.manage)
    POST      /admin/api-keys/<key_id>/revoke              revoke one of yours
    POST      /admin/api-keys/<key_id>/rotate              new secret for one of yours (shown once)

    GET       /admin/api-credentials                       every key and recent token, with owners (needs apiauth.keys.admin)
    POST      /admin/api-credentials/keys/<key_id>/revoke
    POST      /admin/api-credentials/tokens/<id>/revoke

A new key's secret is displayed on the page that creates it - once. Reloading or leaving loses it, by design.
"""

import datetime

from core.controllers.ControllerBase import ControllerBase
from core.http.routing.route import route
from core.services.AccessService import AccessService, as_list
from core.services.RequestAuthService import AuthenticationFailed
from public.apps.apiauth.models.ApiKey import ApiKey
from public.apps.apiauth.models.ApiToken import ApiToken
from public.apps.apiauth.services.CredentialService import CredentialService


def when(timestamp) -> str:
    """A unix timestamp as 'YYYY-MM-DD HH:MM UTC', or 'never'."""
    if not timestamp:
        return "never"
    return datetime.datetime.fromtimestamp(int(timestamp), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class ApiKeysPageController(ControllerBase):

    # ------------------------------------------------------------------ #
    # your keys
    # ------------------------------------------------------------------ #
    @route("/admin/api-keys", methods=["GET", "POST"], permission=["apiauth.keys.manage"])
    def keys(self, request):
        user = self.require_auth(request)
        credentials = CredentialService(self.db())

        if request.is_method("POST"):
            name = str(request.get("name") or "").strip()
            errors = {}
            if not name or len(name) > 100:
                errors["name"] = "A name is required (100 characters at most)."
            days = None
            if request.get("expires_in_days"):
                try:
                    days = int(request.get("expires_in_days"))
                    if not 1 <= days <= 3650:
                        raise ValueError
                except ValueError:
                    errors["expires_in_days"] = "Enter a number of days between 1 and 3650, or leave it empty."
            scopes = None
            if not errors:
                try:
                    scopes = credentials.resolve_scopes(user, as_list(request.get("scopes")) or None)
                except AuthenticationFailed as exc:
                    errors["scopes"] = exc.description

            if errors:
                return self._keys_page(request, user, credentials, errors=errors, old=request.all())

            key, plaintext = credentials.create_key(user, name, scopes, days)
            return self._keys_page(request, user, credentials, new_secret=plaintext, new_key=key)

        return self._keys_page(request, user, credentials, revoked=bool(request.get("revoked")))

    @route("/admin/api-keys/<key_id>/revoke", methods=["POST"], permission=["apiauth.keys.manage"])
    def revoke_key(self, request):
        key = self._owned_key(request)
        if key is None:
            return self.not_found("Key not found")
        CredentialService(self.db()).revoke_key(key)
        return self.redirect("/admin/api-keys?revoked=1")

    @route("/admin/api-keys/<key_id>/rotate", methods=["POST"], permission=["apiauth.keys.manage"])
    def rotate_key(self, request):
        key = self._owned_key(request)
        if key is None:
            return self.not_found("Key not found")
        if not key.is_usable:
            return self.forbidden("A revoked or expired key can't be rotated - create a new one.")
        user = self.require_auth(request)
        credentials = CredentialService(self.db())
        plaintext = credentials.rotate_key(key)
        return self._keys_page(request, user, credentials, new_secret=plaintext, new_key=key, rotated=True)

    # ------------------------------------------------------------------ #
    # everyone's credentials (administrators)
    # ------------------------------------------------------------------ #
    @route("/admin/api-credentials", methods=["GET"], permission=["apiauth.keys.admin"])
    def credentials(self, request):
        db = self.db()
        keys = list(ApiKey.objects(db).select_related("owner").order_by("-id")[:200])
        tokens = list(ApiToken.objects(db).filter(revoked_at__isnull=True).select_related("owner").order_by("-id")[:100])
        return self.view(
            "apiauth:credentials/index",
            {
                "keys": [
                    {"key": k, "owner": k.owner, "created": str(k.created_at)[:16], "expires": when(k.expires_at), "used": when(k.last_used_at),
                     "scopes": ", ".join(k.scopes) if k.scopes else "all of the owner's permissions"}
                    for k in keys
                ],
                "tokens": [
                    {"token": t, "owner": t.owner, "expires": when(t.access_expires_at), "used": when(t.last_used_at),
                     "scopes": ", ".join(t.scopes) if t.scopes else "all"}
                    for t in tokens
                ],
                "revoked": bool(request.get("revoked")),
            },
            request=request,
        )

    @route("/admin/api-credentials/keys/<key_id>/revoke", methods=["POST"], permission=["apiauth.keys.admin"])
    def admin_revoke_key(self, request):
        key = ApiKey.objects(self.db()).filter(key_id=request.get_route_parameter("key_id")).first()
        if key is None:
            return self.not_found("Key not found")
        CredentialService(self.db()).revoke_key(key)
        return self.redirect("/admin/api-credentials?revoked=1")

    @route("/admin/api-credentials/tokens/<id>/revoke", methods=["POST"], permission=["apiauth.keys.admin"])
    def admin_revoke_token(self, request):
        token = ApiToken.find(self.db(), request.get_route_parameter("id"))
        if token is None:
            return self.not_found("Token not found")
        CredentialService(self.db()).revoke_row(token)
        return self.redirect("/admin/api-credentials?revoked=1")

    # ------------------------------------------------------------------ #
    def _owned_key(self, request):
        user = self.require_auth(request)
        key = ApiKey.objects(self.db()).filter(key_id=request.get_route_parameter("key_id")).first()
        if key is None or (key.uid != user.uid and not user.has_permission("apiauth.keys.admin")):
            return None
        return key

    def _keys_page(self, request, user, credentials, errors=None, old=None, new_secret=None, new_key=None, rotated=False, revoked=False):
        groups = []
        for group in AccessService(self.db()).grouped_permissions():
            held = [p for p in group["permissions"] if user.has_permission(p.permission_key)]
            if held:
                groups.append({"app": group["app"], "permissions": held})
        rows = [
            {"key": k, "created": str(k.created_at)[:16], "expires": when(k.expires_at), "used": when(k.last_used_at),
             "scopes": ", ".join(k.scopes) if k.scopes else "everything you can do"}
            for k in user.api_keys.order_by("-id")
        ]
        context = {
            "rows": rows, "groups": groups, "errors": errors or {}, "old": old or {}, "revoked": revoked, "rotated": rotated,
            "new_secret": new_secret, "new_key": new_key,
            "signing_secret": credentials.signing_secret(new_key) if new_key is not None else None,
        }
        return self.view("apiauth:keys/index", context, request=request)
