"""
Jwt
-----
Minimal JSON Web Tokens - HS256 (HMAC-SHA256) only, standard library only. Deliberately strict:

    * only alg "HS256" is accepted - "none", RS256, anything else is rejected, so a token can't talk the server
      into skipping or downgrading signature checking
    * the signature is checked (in constant time) BEFORE the payload is trusted
    * "exp" is required; "nbf" and "iss" are enforced when present / expected
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional


class JwtError(Exception):
    """A token that can't be accepted. `code` is a short machine-friendly reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(segment: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (ValueError, TypeError) as exc:
        raise JwtError("malformed", "token is not valid base64url") from exc


def _sign(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def encode(claims: Dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        _b64encode(json.dumps(part, separators=(",", ":"), sort_keys=True).encode("utf-8")) for part in (header, claims)
    )
    return f"{signing_input}.{_b64encode(_sign(signing_input.encode('ascii'), secret))}"


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and token.startswith("ey")


def decode(token: str, secret: str, issuer: Optional[str] = None, now: Optional[int] = None) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise JwtError("malformed", "token must have three parts")

    header_segment, payload_segment, signature_segment = parts
    try:
        header = json.loads(_b64decode(header_segment))
    except ValueError as exc:
        raise JwtError("malformed", "token header is not valid JSON") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise JwtError("unsupported_algorithm", "only HS256 tokens are accepted")

    expected = _sign(f"{header_segment}.{payload_segment}".encode("ascii"), secret)
    if not hmac.compare_digest(expected, _b64decode(signature_segment)):
        raise JwtError("bad_signature", "token signature is invalid")

    try:
        claims = json.loads(_b64decode(payload_segment))
    except ValueError as exc:
        raise JwtError("malformed", "token payload is not valid JSON") from exc
    if not isinstance(claims, dict):
        raise JwtError("malformed", "token payload must be an object")

    moment = int(time.time()) if now is None else now
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise JwtError("missing_exp", "token has no expiry")
    if exp <= moment:
        raise JwtError("expired", "token has expired")
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and not isinstance(nbf, bool) and nbf > moment:
        raise JwtError("not_yet_valid", "token is not valid yet")
    if issuer is not None and claims.get("iss") != issuer:
        raise JwtError("wrong_issuer", "token was issued by someone else")
    return claims
