"""
Settings
----------
Everything about how the apiauth app behaves, read from .env on every request (so changing a setting needs no
restart). All optional; the defaults give API keys and opaque bearer tokens, which need no extra configuration.

    API_AUTH_SCHEMES            which ways of authenticating are accepted, comma separated. Default: apikey,bearer
                                    apikey   a long-lived key, sent as  X-API-Key: <key>  or  Authorization: ApiKey <key>
                                    bearer   an opaque access token from the token endpoint, sent as  Authorization: Bearer <token>
                                    jwt      a signed JWT access token (HS256), sent as  Authorization: Bearer <jwt>
                                    basic    HTTP Basic - the key id as username and key secret as password (and, only if
                                             API_AUTH_BASIC_PASSWORDS=true, an account's email + password)
                                    hmac     signed requests: Authorization: Signature keyId=..., timestamp=..., nonce=..., signature=...
    API_AUTH_SECRET             a long random string (32+ characters) - REQUIRED for jwt and hmac. Generate one with
                                    python -c "import secrets; print(secrets.token_urlsafe(48))"
    API_AUTH_GRANTS             which grants the token endpoint accepts. Default: password,client_credentials,refresh_token
    API_AUTH_TOKEN_FORMAT       what the token endpoint issues as the access token: opaque (default) or jwt
    API_AUTH_ACCESS_TTL         access token lifetime in seconds. Default 3600
    API_AUTH_REFRESH_TTL        refresh token lifetime in seconds. Default 2592000 (30 days)
    API_AUTH_ISSUER             the "iss" claim of issued JWTs. Default: APP_URL, or "plinth"
    API_AUTH_JWT_CHECK_REVOCATION  true (default): a JWT is also checked against the database, so revoking it takes
                                    effect at once. false: fully stateless - valid until it expires.
    API_AUTH_HMAC_TOLERANCE     how many seconds a signed request's timestamp may differ from the server's clock. Default 300
    API_AUTH_BASIC_PASSWORDS    allow account email + password over HTTP Basic. Default false (it sends the password on
                                    every request; prefer a key or the token endpoint)
    API_AUTH_MAX_FAILURES       failed attempts allowed per client + identity within the window before 429. Default 10
    API_AUTH_FAILURE_WINDOW     that window in seconds. Default 900
"""

from typing import List, Optional

from core.services.EnvService import EnvService

SCHEMES = ("apikey", "bearer", "jwt", "basic", "hmac")
GRANTS = ("password", "client_credentials", "refresh_token")
MIN_SECRET_LENGTH = 32


class Settings:
    def __init__(self, env: Optional[EnvService] = None):
        env = env or EnvService()
        self.schemes: List[str] = [s.lower() for s in env.get_list("API_AUTH_SCHEMES", ["apikey", "bearer"])]
        self.grants: List[str] = [g.lower() for g in env.get_list("API_AUTH_GRANTS", list(GRANTS))]
        self.secret: str = env.get("API_AUTH_SECRET", "") or ""
        self.token_format: str = (env.get("API_AUTH_TOKEN_FORMAT", "opaque") or "opaque").lower()
        self.access_ttl: int = env.get_int("API_AUTH_ACCESS_TTL", 3600)
        self.refresh_ttl: int = env.get_int("API_AUTH_REFRESH_TTL", 2592000)
        self.issuer: str = env.get("API_AUTH_ISSUER") or env.get("APP_URL") or "plinth"
        self.jwt_check_revocation: bool = env.get_bool("API_AUTH_JWT_CHECK_REVOCATION", True)
        self.hmac_tolerance: int = env.get_int("API_AUTH_HMAC_TOLERANCE", 300)
        self.basic_passwords: bool = env.get_bool("API_AUTH_BASIC_PASSWORDS", False)
        self.max_failures: int = env.get_int("API_AUTH_MAX_FAILURES", 10)
        self.failure_window: int = env.get_int("API_AUTH_FAILURE_WINDOW", 900)

    def enabled(self, scheme: str) -> bool:
        return scheme in self.schemes

    def grant_enabled(self, grant: str) -> bool:
        return grant in self.grants

    @property
    def needs_secret(self) -> bool:
        return self.enabled("jwt") or self.enabled("hmac") or self.token_format == "jwt"

    def problems(self) -> List[str]:
        """Configuration mistakes worth refusing to run with - shown by the token endpoint and /api/auth/schemes."""
        problems = []
        for scheme in self.schemes:
            if scheme not in SCHEMES:
                problems.append(f"API_AUTH_SCHEMES: unknown scheme '{scheme}' (known: {', '.join(SCHEMES)})")
        for grant in self.grants:
            if grant not in GRANTS:
                problems.append(f"API_AUTH_GRANTS: unknown grant '{grant}' (known: {', '.join(GRANTS)})")
        if self.token_format not in ("opaque", "jwt"):
            problems.append("API_AUTH_TOKEN_FORMAT must be 'opaque' or 'jwt'")
        elif self.token_format == "opaque" and not self.enabled("bearer"):
            problems.append("API_AUTH_TOKEN_FORMAT=opaque needs the 'bearer' scheme in API_AUTH_SCHEMES")
        elif self.token_format == "jwt" and not self.enabled("jwt"):
            problems.append("API_AUTH_TOKEN_FORMAT=jwt needs the 'jwt' scheme in API_AUTH_SCHEMES")
        if self.needs_secret and len(self.secret) < MIN_SECRET_LENGTH:
            problems.append(f"API_AUTH_SECRET must be set to a random string of at least {MIN_SECRET_LENGTH} characters (needed for jwt/hmac)")
        return problems
