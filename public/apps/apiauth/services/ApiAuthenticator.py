"""
ApiAuthenticator
------------------
The class Server runs on every request (registered in the app's authenticators.py) to recognise API
credentials. It looks at the Authorization / X-API-Key headers and, for the schemes enabled in
API_AUTH_SCHEMES, verifies them:

    X-API-Key: <key>                       or   Authorization: ApiKey <key>               -> apikey
    Authorization: Bearer <opaque token>                                                  -> bearer
    Authorization: Bearer <jwt>                                                           -> jwt
    Authorization: Basic base64(key_id:secret)  (or email:password if enabled)            -> basic
    Authorization: Signature keyId="...", timestamp="...", nonce="...", signature="..."   -> hmac

No recognisable credentials (or a scheme that's switched off) -> None, and the request carries on as anonymous /
session. Credentials that are present but wrong -> AuthenticationFailed, which Server turns into a 401 with a
WWW-Authenticate header (or 429 once a client has failed too often).

A valid credential gives back the owning user, narrowed to the credential's scopes if it has any - from then on the
request is indistinguishable from a signed-in browser as far as routes, permissions and menus go.
"""

import base64
import binascii
from typing import Optional

from core.services.RequestAuthService import AuthenticationFailed, AuthResult
from public.apps.apiauth.services import Jwt, Signing
from public.apps.apiauth.services.CredentialService import CredentialService
from public.apps.apiauth.services.Crypto import equals, now
from public.apps.apiauth.services.Settings import Settings
from public.apps.apiauth.services.Throttle import nonces, throttle


class ApiAuthenticator:
    CHALLENGES = {
        "apikey": 'ApiKey realm="api"',
        "bearer": 'Bearer realm="api"',
        "jwt": 'Bearer realm="api"',
        "basic": 'Basic realm="api", charset="UTF-8"',
        "hmac": 'Signature realm="api"',
    }

    def __init__(self, db_connection):
        self.db = db_connection
        self.settings = Settings()
        self.credentials = CredentialService(db_connection, self.settings)

    # ------------------------------------------------------------------ #
    def authenticate(self, request) -> Optional[AuthResult]:
        s = self.settings

        api_key = request.headers.get("X-API-Key")
        if api_key and s.enabled("apikey"):
            return self._guarded(request, "apikey", self._identity(api_key), lambda: self._api_key(api_key.strip(), "apikey"))

        header = request.headers.get("Authorization", "")
        scheme, _, credentials = header.partition(" ")
        credentials = credentials.strip()
        if not credentials:
            return None

        scheme = scheme.lower()
        if scheme == "apikey" and s.enabled("apikey"):
            return self._guarded(request, "apikey", self._identity(credentials), lambda: self._api_key(credentials, "apikey"))
        if scheme == "bearer":
            if s.enabled("jwt") and Jwt.looks_like_jwt(credentials):
                return self._bearer(credentials, jwt=True)
            if s.enabled("bearer"):
                return self._bearer(credentials, jwt=False)
            return None
        if scheme == "basic" and s.enabled("basic"):
            return self._basic(request, credentials)
        if scheme == "signature" and s.enabled("hmac"):
            return self._signature(request, header)
        return None  # a scheme this app doesn't handle (or has switched off) - not ours to judge

    # ------------------------------------------------------------------ #
    # the schemes
    # ------------------------------------------------------------------ #
    def _api_key(self, plaintext: str, scheme: str) -> AuthResult:
        key = self.credentials.verify_key(plaintext)
        user = self.credentials.active_user(key.uid)
        return AuthResult(user=user, scheme=scheme, credential=key, scopes=key.scopes, expires_at=key.expires_at)

    def _bearer(self, token: str, jwt: bool) -> AuthResult:
        scheme = "jwt" if jwt else "bearer"
        try:
            row, user, scopes = self.credentials.verify_access_token(token, jwt=jwt)
        except AuthenticationFailed as exc:
            raise self._challenge(exc, scheme)
        return AuthResult(user=user, scheme=scheme, credential=row, scopes=scopes, expires_at=row.access_expires_at if row else None)

    def _basic(self, request, credentials: str) -> AuthResult:
        try:
            username, sep, password = base64.b64decode(credentials, validate=True).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            raise self._fail("basic", "malformed Basic credentials")
        if not sep or not username:
            raise self._fail("basic", "malformed Basic credentials")

        if username.startswith("pk_"):  # an API key: key id as the username, its secret as the password
            return self._guarded(request, "basic", username, lambda: self._api_key(f"{username}.{password}", "basic"))

        if not self.settings.basic_passwords:
            raise self._fail("basic", "account passwords are not accepted over HTTP Basic here - use an API key or the token endpoint")

        def by_password() -> AuthResult:
            user = self.credentials.check_password(username, password)
            if user is None:
                raise AuthenticationFailed("invalid_token", "invalid username or password")
            return AuthResult(user=user, scheme="basic", credential=None, scopes=None)

        return self._guarded(request, "basic", username.lower(), by_password)

    def _signature(self, request, header: str) -> AuthResult:
        s = self.settings
        if len(s.secret) < 32:
            raise AuthenticationFailed("server_error", "API_AUTH_SECRET is not configured, so signed requests can't be verified", status=500)

        params = Signing.parse_header(header)
        if not all(params.get(name) for name in ("keyId", "timestamp", "nonce", "signature")):
            raise self._fail("hmac", "the Signature header needs keyId, timestamp, nonce and signature")

        def verify() -> AuthResult:
            try:
                timestamp = int(params["timestamp"])
            except ValueError:
                raise AuthenticationFailed("invalid_token", "invalid timestamp")
            if abs(now() - timestamp) > s.hmac_tolerance:
                raise AuthenticationFailed("invalid_token", f"timestamp is more than {s.hmac_tolerance}s from the server's clock")

            key = self.credentials.key_by_id(params["keyId"])
            if key is None or not key.is_usable:
                raise AuthenticationFailed("invalid_token", "invalid signature")
            secret = self.credentials.signing_secret(key)
            expected = Signing.sign(secret, request.method, request.get_request_uri(), params["timestamp"], params["nonce"], request.content)
            if not equals(expected, params["signature"].lower()):
                raise AuthenticationFailed("invalid_token", "invalid signature")
            if not nonces.use(f"{key.key_id}|{params['nonce']}", ttl=s.hmac_tolerance * 2):
                raise AuthenticationFailed("invalid_token", "this request was already used (nonce replay)")

            user = self.credentials.active_user(key.uid)
            return AuthResult(user=user, scheme="hmac", credential=key, scopes=key.scopes, expires_at=key.expires_at)

        return self._guarded(request, "hmac", params["keyId"], verify)

    # ------------------------------------------------------------------ #
    # shared
    # ------------------------------------------------------------------ #
    def _guarded(self, request, scheme: str, identity: str, attempt) -> AuthResult:
        """Runs a verification under the failure throttle (see Throttle): too many recent failures for this
        client + identity -> 429; a failure is counted and re-raised with the right WWW-Authenticate; success clears it."""
        key = f"{request.client_ip}|{scheme}|{identity}"
        wait = throttle.retry_after(key, self.settings.max_failures, self.settings.failure_window)
        if wait:
            raise AuthenticationFailed("too_many_attempts", f"too many failed attempts - try again in {wait}s", status=429,
                                       headers={"Retry-After": str(wait)})
        try:
            result = attempt()
        except AuthenticationFailed as exc:
            if exc.status == 401:
                throttle.fail(key, self.settings.failure_window)
            raise self._challenge(exc, scheme)
        throttle.clear(key)
        return result

    @staticmethod
    def _identity(plaintext: str) -> str:
        """The part of a key that's safe to use as a throttle key / log: its public id."""
        return plaintext.strip().partition(".")[0][:40]

    def _challenge(self, exc: AuthenticationFailed, scheme: str) -> AuthenticationFailed:
        """Adds the WWW-Authenticate header a 401 must carry."""
        if exc.status == 401 and "WWW-Authenticate" not in exc.headers:
            challenge = self.CHALLENGES[scheme]
            if scheme in ("bearer", "jwt"):
                challenge += f', error="{exc.error}", error_description="{exc.description.replace(chr(34), chr(39))}"'
            exc.headers["WWW-Authenticate"] = challenge
        return exc

    def _fail(self, scheme: str, description: str) -> AuthenticationFailed:
        return self._challenge(AuthenticationFailed("invalid_token", description), scheme)
