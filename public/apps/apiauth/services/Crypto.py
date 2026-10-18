"""Small helpers shared by the apiauth services: random secrets, hashing, constant-time comparison, the clock."""

import hashlib
import hmac
import secrets
import time


def now() -> int:
    return int(time.time())


def random_secret(nbytes: int = 32) -> str:
    """A URL-safe random string (256 bits by default) - what keys and tokens are made of."""
    return secrets.token_urlsafe(nbytes)


def random_hex(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def equals(a: str, b: str) -> bool:
    """Constant-time string comparison - never compare secrets or signatures with ==."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
