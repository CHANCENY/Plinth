"""
CredentialService
-------------------
Creating, verifying, rotating and revoking everything the apiauth app hands out: API keys, and the access /
refresh tokens issued by the OAuth token endpoint. The authenticator (ApiAuthenticator) and the controllers
are thin layers over this.

Failures are raised as core.services.RequestAuthService.AuthenticationFailed with an OAuth-style error code, which
Server / the controllers turn into a JSON response - so every route reports problems the same way.

Rules worth knowing
    * Keys and tokens are random 256-bit values; only their SHA-256 hashes are stored, and the plaintext is
      shown exactly once, when created.
    * Scopes are permission keys. A credential can only be given scopes its creator themselves holds, and at use
      time the user's permissions are intersected with them - a scoped key can never do more than its owner can.
    * Refresh tokens rotate: using one revokes it and issues a new pair. If an already-used refresh token is
      presented again (stolen, or a client replaying), the whole family of tokens from that login is revoked.
    * Revoking a key (or rotating its secret) also revokes the tokens that were issued from it.
    * An account that isn't active (suspended, blocked, deleted ...) can't use any credential.
"""

from typing import Any, Dict, List, Optional, Tuple

from core.models.Permission import Permission
from core.models.User import User
from core.services.RequestAuthService import AuthenticationFailed
from public.apps.apiauth.models.ApiKey import ApiKey
from public.apps.apiauth.models.ApiToken import ApiToken
from public.apps.apiauth.services import Jwt, Signing
from public.apps.apiauth.services.Crypto import equals, now, random_hex, random_secret, sha256_hex
from public.apps.apiauth.services.Settings import Settings

KEY_PREFIX = "pk_"
ACCESS_PREFIX = "plt_"
REFRESH_PREFIX = "plr_"
TOUCH_INTERVAL = 60  # last_used_at is written at most once a minute per credential - not on every request


class CredentialService:
    def __init__(self, db_connection, settings: Optional[Settings] = None):
        self.db = db_connection
        self.settings = settings or Settings()

    # ------------------------------------------------------------------ #
    # scopes
    # ------------------------------------------------------------------ #
    def resolve_scopes(self, user: User, requested: Optional[List[str]], ceiling: Optional[List[str]] = None) -> Optional[List[str]]:
        """
        The scopes a new credential gets. `requested` None means "as much as allowed" (the ceiling, or
        unrestricted). Every requested scope must be a real permission the user holds, and - when issuing
        from a scoped key or refresh token - within that ceiling. A scope of "*" means unrestricted.
        Returns a sorted list, or None for unrestricted.
        """
        if requested is None:
            return None if ceiling is None else sorted(set(ceiling))
        requested = sorted({s.strip() for s in requested if s and s.strip()})
        if not requested or "*" in requested:
            return None if ceiling is None else sorted(set(ceiling))

        known = {p.permission_key for p in Permission.objects(self.db).filter(permission_key__in=requested)}
        for scope in requested:
            if scope not in known:
                raise AuthenticationFailed("invalid_scope", f"unknown scope '{scope}'", status=400)
            if not user.has_permission(scope):
                raise AuthenticationFailed("invalid_scope", f"you don't hold the permission '{scope}'", status=400)
            if ceiling is not None and scope not in ceiling:
                raise AuthenticationFailed("invalid_scope", f"scope '{scope}' is outside what this credential allows", status=400)
        return requested

    @staticmethod
    def parse_scope_param(value: Any) -> Optional[List[str]]:
        """A `scope` request parameter - a space-separated string (OAuth) or a list - into a list, or None if absent."""
        if value is None or value == "":
            return None
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        return str(value).replace(",", " ").split()

    # ------------------------------------------------------------------ #
    # users
    # ------------------------------------------------------------------ #
    def active_user(self, uid: Any) -> User:
        user = User.find(self.db, uid)
        if user is None or user.status != "active":
            raise AuthenticationFailed("invalid_token", "the account for this credential isn't active")
        return user

    def check_password(self, email: str, password: str) -> Optional[User]:
        """The active User for an email + password, or None. Takes about as long whether or not the email exists, so
        the response time doesn't reveal which accounts are real (User.authenticate alone returns instantly for an
        unknown email and only does the slow password hash for a real one)."""
        user = User.find_by_email(self.db, email) if email else None
        if user is None:
            User.hash_password(password or "")  # burn the same amount of time a real check would
            return None
        if user.status == "active" and user.verify_password(password or ""):
            return user
        return None

    # ------------------------------------------------------------------ #
    # API keys
    # ------------------------------------------------------------------ #
    def create_key(self, user: User, name: str, scopes: Optional[List[str]] = None,
                   expires_in_days: Optional[int] = None) -> Tuple[ApiKey, str]:
        """Returns (key row, the plaintext "<key_id>.<secret>" - never retrievable again)."""
        key_id = KEY_PREFIX + random_hex(6)
        secret = random_secret()
        key = ApiKey(
            self.db,
            uid=user.uid,
            key_id=key_id,
            secret_hash=sha256_hex(secret),
            name=name.strip()[:100],
            scopes=scopes,
            expires_at=(now() + int(expires_in_days) * 86400) if expires_in_days else None,
        ).save()
        return key, f"{key_id}.{secret}"

    def verify_key(self, plaintext: str) -> ApiKey:
        """The usable ApiKey for "<key_id>.<secret>", or AuthenticationFailed. The message is the same whether
        the id is unknown or the secret is wrong, so it can't be used to discover which key ids exist."""
        key_id, _, secret = plaintext.partition(".")
        key = ApiKey.objects(self.db).filter(key_id=key_id).first() if key_id and secret else None
        if key is None or not equals(key.secret_hash, sha256_hex(secret)):
            raise AuthenticationFailed("invalid_token", "invalid API key")
        if not key.is_usable:
            raise AuthenticationFailed("invalid_token", "this API key has been revoked or has expired")
        self._touch(ApiKey, key)
        return key

    def key_by_id(self, key_id: str) -> Optional[ApiKey]:
        return ApiKey.objects(self.db).filter(key_id=key_id).first()

    def signing_secret(self, key: ApiKey) -> Optional[str]:
        """The secret a client signs requests with for this key (see Signing) - None if signing isn't configured."""
        if not self.settings.enabled("hmac") or len(self.settings.secret) < 32:
            return None
        return Signing.derive_signing_secret(self.settings.secret, key.key_id, key.secret_hash)

    def rotate_key(self, key: ApiKey) -> str:
        """New secret for the same key id; the old one stops working at once, along with tokens issued from the key."""
        secret = random_secret()
        key.secret_hash = sha256_hex(secret)
        key.save()
        self._revoke_key_tokens(key)
        return f"{key.key_id}.{secret}"

    def revoke_key(self, key: ApiKey) -> None:
        if key.revoked_at is None:
            key.revoked_at = now()
            key.save()
        self._revoke_key_tokens(key)

    def _revoke_key_tokens(self, key: ApiKey) -> None:
        ApiToken.objects(self.db).filter(api_key_id=key.id, revoked_at__isnull=True).update(revoked_at=now())

    # ------------------------------------------------------------------ #
    # issuing tokens
    # ------------------------------------------------------------------ #
    def issue_tokens(self, user: User, scopes: Optional[List[str]], key: Optional[ApiKey] = None, ip: str = "",
                     user_agent: str = "", refresh: bool = True, family: Optional[str] = None) -> Dict[str, Any]:
        """Creates a token pair and returns the OAuth token response (the plaintext tokens appear only here)."""
        problems = self.settings.problems()
        if problems:
            raise AuthenticationFailed("server_error", "; ".join(problems), status=500)

        issued_at = now()
        access_expires = issued_at + self.settings.access_ttl
        jti = random_hex(16)

        if self.settings.token_format == "jwt":
            claims = {"iss": self.settings.issuer, "sub": str(user.uid), "iat": issued_at, "exp": access_expires, "jti": jti}
            if scopes:
                claims["scope"] = " ".join(scopes)
            access_token, access_hash = Jwt.encode(claims, self.settings.secret), None
        else:
            access_token = ACCESS_PREFIX + random_secret()
            access_hash = sha256_hex(access_token)

        refresh_token = REFRESH_PREFIX + random_secret() if refresh else None
        ApiToken(
            self.db,
            uid=user.uid,
            api_key_id=key.id if key is not None else None,
            family=family or random_hex(16),
            jti=jti,
            access_hash=access_hash,
            access_expires_at=access_expires,
            refresh_hash=sha256_hex(refresh_token) if refresh_token else None,
            refresh_expires_at=(issued_at + self.settings.refresh_ttl) if refresh_token else None,
            scopes=scopes,
            ip=(ip or "")[:45] or None,
            user_agent=(user_agent or "")[:255] or None,
        ).save()

        response: Dict[str, Any] = {"access_token": access_token, "token_type": "Bearer", "expires_in": self.settings.access_ttl}
        if refresh_token:
            response["refresh_token"] = refresh_token
        if scopes:
            response["scope"] = " ".join(scopes)
        return response

    def refresh(self, refresh_token: str, requested_scope: Optional[List[str]], ip: str = "", user_agent: str = "") -> Dict[str, Any]:
        row = ApiToken.objects(self.db).filter(refresh_hash=sha256_hex(refresh_token)).first() if refresh_token else None
        if row is None:
            raise AuthenticationFailed("invalid_grant", "invalid refresh token", status=400)
        if row.is_revoked:
            # An already-used (rotated) or revoked refresh token showing up again: assume it leaked and end the whole login.
            self.revoke_family(row.family)
            raise AuthenticationFailed("invalid_grant", "this refresh token was already used or revoked", status=400)
        if row.refresh_expired:
            raise AuthenticationFailed("invalid_grant", "refresh token has expired", status=400)

        user = User.find(self.db, row.uid)
        if user is None or user.status != "active":
            raise AuthenticationFailed("invalid_grant", "the account isn't active", status=400)

        scopes = self.resolve_scopes(user, requested_scope, ceiling=row.scopes)
        row.revoked_at = now()  # rotation: this refresh token is now spent
        row.save()
        return self.issue_tokens(user, scopes, key=None, ip=ip, user_agent=user_agent, family=row.family)

    # ------------------------------------------------------------------ #
    # verifying access tokens
    # ------------------------------------------------------------------ #
    def verify_access_token(self, token: str, jwt: bool) -> Tuple[Optional[ApiToken], User, Optional[List[str]]]:
        """(token row or None, the user, the scopes) for an access token, or AuthenticationFailed."""
        if jwt:
            return self._verify_jwt(token)

        row = ApiToken.objects(self.db).filter(access_hash=sha256_hex(token)).first()
        if row is None:
            raise AuthenticationFailed("invalid_token", "invalid access token")
        if row.is_revoked:
            raise AuthenticationFailed("invalid_token", "this access token has been revoked")
        if row.access_expired:
            raise AuthenticationFailed("invalid_token", "the access token has expired")
        user = self.active_user(row.uid)
        self._touch(ApiToken, row)
        return row, user, row.scopes

    def _verify_jwt(self, token: str) -> Tuple[Optional[ApiToken], User, Optional[List[str]]]:
        if len(self.settings.secret) < 32:
            raise AuthenticationFailed("server_error", "API_AUTH_SECRET is not configured", status=500)
        try:
            claims = Jwt.decode(token, self.settings.secret, issuer=self.settings.issuer)
        except Jwt.JwtError as exc:
            raise AuthenticationFailed("invalid_token", f"invalid token: {exc}") from exc

        row = ApiToken.objects(self.db).filter(jti=str(claims.get("jti", ""))).first()
        if self.settings.jwt_check_revocation:
            if row is None or row.is_revoked:
                raise AuthenticationFailed("invalid_token", "this token has been revoked")
            scopes = row.scopes
            self._touch(ApiToken, row)
        else:  # stateless: the signature and expiry are all that count
            scope_claim = claims.get("scope")
            scopes = scope_claim.split() if isinstance(scope_claim, str) and scope_claim else None
        return row, self.active_user(claims.get("sub")), scopes

    # ------------------------------------------------------------------ #
    # revoking tokens
    # ------------------------------------------------------------------ #
    def find_token(self, token: str) -> Optional[ApiToken]:
        """The row behind an access token (opaque or JWT) or a refresh token, if there is one."""
        digest = sha256_hex(token)
        row = ApiToken.objects(self.db).filter(access_hash=digest).first() or ApiToken.objects(self.db).filter(refresh_hash=digest).first()
        if row is None and Jwt.looks_like_jwt(token) and len(self.settings.secret) >= 32:
            try:
                claims = Jwt.decode(token, self.settings.secret, issuer=self.settings.issuer)
            except Jwt.JwtError:
                return None
            row = ApiToken.objects(self.db).filter(jti=str(claims.get("jti", ""))).first()
        return row

    def revoke_token(self, token: str) -> bool:
        row = self.find_token(token)
        if row is None:
            return False
        self.revoke_row(row)
        return True

    def revoke_row(self, row: ApiToken) -> None:
        if row.revoked_at is None:
            row.revoked_at = now()
            row.save()

    def revoke_family(self, family: str) -> None:
        ApiToken.objects(self.db).filter(family=family, revoked_at__isnull=True).update(revoked_at=now())

    # ------------------------------------------------------------------ #
    # presenting rows
    # ------------------------------------------------------------------ #
    def key_summary(self, key: ApiKey) -> Dict[str, Any]:
        """A key as the management API shows it - never the secret."""
        return {
            "id": key.id, "key_id": key.key_id, "name": key.name, "scopes": key.scopes,
            "created_at": str(key.created_at), "expires_at": key.expires_at, "last_used_at": key.last_used_at,
            "revoked": key.is_revoked, "expired": key.is_expired,
        }

    def token_summary(self, token: ApiToken) -> Dict[str, Any]:
        return {
            "id": token.id, "scopes": token.scopes, "created_at": str(token.created_at),
            "access_expires_at": token.access_expires_at, "refresh_expires_at": token.refresh_expires_at,
            "last_used_at": token.last_used_at, "revoked": token.is_revoked, "ip": token.ip, "user_agent": token.user_agent,
            "key_id": token.api_key.key_id if token.api_key_id else None,
        }

    # ------------------------------------------------------------------ #
    def _touch(self, model, row) -> None:
        moment = now()
        if row.last_used_at is None or moment - row.last_used_at >= TOUCH_INTERVAL:
            model.objects(self.db).filter(pk=row.id).update(last_used_at=moment)
