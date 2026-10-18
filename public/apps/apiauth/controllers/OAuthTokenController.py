"""
OAuthTokenController
----------------------
The token endpoint, modelled on OAuth 2.0 (RFC 6749) so any OAuth-aware client or library can talk to it. Both
endpoints are public - they're how a client gets credentials in the first place - and are protected by the
failure throttle instead.

    POST /api/oauth/token     exchange something for an access token. Parameters (form-encoded or JSON):

        grant_type=password            username (the account email), password, [scope]
                                       -> access token + refresh token, for a person's own account
        grant_type=client_credentials  client_id + client_secret (or HTTP Basic), [scope]
                                       -> access token only, for a program using an API key
        grant_type=refresh_token       refresh_token, [scope]
                                       -> a new access + refresh token; the old refresh token is spent

        Response:  {"access_token": "...", "token_type": "Bearer", "expires_in": 3600,
                    "refresh_token": "...", "scope": "a.b c.d"}   (refresh_token / scope only when applicable)
        Errors:    {"error": "invalid_grant", "error_description": "..."} - OAuth error codes, 400 / 401 / 429

    POST /api/oauth/revoke    token=<access or refresh token> -> 200 whether or not it existed (RFC 7009)

`scope` is a space-separated list of permission keys; it can only narrow what the account (or key) can already do.
Which grants are accepted and whether access tokens are opaque or JWT come from .env - see services/Settings.py.
"""

from core.controllers.ControllerBase import ControllerBase
from core.http.response import JsonResponse
from core.http.routing.route import route
from core.services.RequestAuthService import AuthenticationFailed
from public.apps.apiauth.services import Http
from public.apps.apiauth.services.CredentialService import CredentialService
from public.apps.apiauth.services.Settings import GRANTS, Settings
from public.apps.apiauth.services.Throttle import throttle


class OAuthTokenController(ControllerBase):

    @route("/api/oauth/token", methods=["POST"])
    def token(self, request):
        try:
            return self._issue(request)
        except AuthenticationFailed as exc:
            return exc.to_response()

    @route("/api/oauth/revoke", methods=["POST"])
    def revoke(self, request):
        token = str(Http.params(request).get("token") or "").strip()
        if not token:
            return Http.error("invalid_request", "token is required")
        CredentialService(self.db()).revoke_token(token)
        return JsonResponse({}, status=200, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ #
    def _issue(self, request):
        settings = Settings()
        credentials = CredentialService(self.db(), settings)
        data = Http.params(request)

        grant = str(data.get("grant_type") or "").lower()
        if not grant:
            raise AuthenticationFailed("invalid_request", "grant_type is required", status=400)
        if grant not in GRANTS or not settings.grant_enabled(grant):
            raise AuthenticationFailed("unsupported_grant_type", f"grant_type '{grant}' is not supported here", status=400)

        ip = request.client_ip or ""
        user_agent = request.user_agent() or ""
        scope = credentials.parse_scope_param(data.get("scope"))

        if grant == "password":
            return Http.no_store(self._password_grant(credentials, settings, data, scope, ip, user_agent))
        if grant == "client_credentials":
            return Http.no_store(self._client_credentials_grant(request, credentials, settings, data, scope, ip, user_agent))
        return Http.no_store(credentials.refresh(str(data.get("refresh_token") or ""), scope, ip, user_agent))

    def _guard(self, settings: Settings, key: str) -> None:
        wait = throttle.retry_after(key, settings.max_failures, settings.failure_window)
        if wait:
            raise AuthenticationFailed("too_many_attempts", f"too many failed attempts - try again in {wait}s", status=429,
                                       headers={"Retry-After": str(wait)})

    def _password_grant(self, credentials, settings, data, scope, ip, user_agent):
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not username or not password:
            raise AuthenticationFailed("invalid_request", "username and password are required", status=400)

        key = f"{ip}|password|{username.lower()}"
        self._guard(settings, key)
        user = credentials.check_password(username, password)
        if user is None:
            throttle.fail(key, settings.failure_window)
            raise AuthenticationFailed("invalid_grant", "invalid username or password", status=400)
        throttle.clear(key)

        return credentials.issue_tokens(user, credentials.resolve_scopes(user, scope), ip=ip, user_agent=user_agent)

    def _client_credentials_grant(self, request, credentials, settings, data, scope, ip, user_agent):
        basic = Http.basic_credentials(request)
        client_id, client_secret = basic if basic else (str(data.get("client_id") or ""), str(data.get("client_secret") or ""))
        if not client_id or not client_secret:
            raise AuthenticationFailed("invalid_request", "client_id and client_secret are required", status=400)

        challenge = {"WWW-Authenticate": 'Basic realm="api"'}
        key_name = f"{ip}|client|{client_id}"
        self._guard(settings, key_name)
        try:
            key = credentials.verify_key(f"{client_id}.{client_secret}")
            user = credentials.active_user(key.uid)
        except AuthenticationFailed as exc:
            throttle.fail(key_name, settings.failure_window)
            raise AuthenticationFailed("invalid_client", exc.description, status=401, headers=challenge)
        throttle.clear(key_name)

        scopes = credentials.resolve_scopes(user, scope, ceiling=key.scopes)
        return credentials.issue_tokens(user, scopes, key=key, ip=ip, user_agent=user_agent, refresh=False)
