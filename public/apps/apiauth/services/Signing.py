"""
Signing
---------
Signed requests (the "hmac" scheme): instead of sending a secret, the client proves it holds one by signing
the request - the secret itself never crosses the wire, a captured request can't be altered or replayed, and
the server doesn't have to store anything it could be tricked into revealing.

    Authorization: Signature keyId="pk_3f9c...", timestamp="1767225600", nonce="b1c2...", signature="9a8f..."

    signature = HMAC-SHA256( signing_secret, canonical_string ) as hex, where canonical_string is these five
    lines joined by "\\n":
        METHOD                       upper case, e.g. POST
        TARGET                       the request target exactly as sent - path AND query string, e.g. /api/notes?page=2
        TIMESTAMP                    unix seconds (must be within API_AUTH_HMAC_TOLERANCE of the server's clock)
        NONCE                        a fresh random string per request (a reused nonce is rejected)
        SHA256(body) as hex          of the raw request body ("" for no body)

The signing secret is given to the key's owner when the key is created. It isn't stored: the server recomputes
it as HMAC(API_AUTH_SECRET, key_id + the key's secret hash), so it changes if the key is rotated and every
signing secret dies if API_AUTH_SECRET is changed.

signed_headers() is the client side of this, handy for Python clients and for tests.
"""

import hashlib
import hmac
import re
import time
from typing import Dict, Optional

from public.apps.apiauth.services.Crypto import random_hex

SCHEME = "Signature"
_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')


def derive_signing_secret(master_secret: str, key_id: str, secret_hash: str) -> str:
    message = f"plinth-signing-v1|{key_id}|{secret_hash}".encode("utf-8")
    return hmac.new(master_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def canonical_string(method: str, target: str, timestamp: str, nonce: str, body: bytes) -> str:
    return "\n".join([method.upper(), target, str(timestamp), nonce, hashlib.sha256(body or b"").hexdigest()])


def sign(signing_secret: str, method: str, target: str, timestamp: str, nonce: str, body: bytes = b"") -> str:
    message = canonical_string(method, target, timestamp, nonce, body).encode("utf-8")
    return hmac.new(signing_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def parse_header(value: str) -> Dict[str, str]:
    """{"keyId": ..., "timestamp": ..., "nonce": ..., "signature": ...} from an Authorization header value."""
    return dict(_PARAM_RE.findall(value))


def signed_headers(key_id: str, signing_secret: str, method: str, target: str, body: bytes = b"",
                   timestamp: Optional[int] = None, nonce: Optional[str] = None) -> Dict[str, str]:
    """The Authorization header a client should send for this request."""
    timestamp_text = str(int(time.time()) if timestamp is None else timestamp)
    nonce = nonce or random_hex(12)
    signature = sign(signing_secret, method, target, timestamp_text, nonce, body)
    return {
        "Authorization": f'{SCHEME} keyId="{key_id}", timestamp="{timestamp_text}", nonce="{nonce}", signature="{signature}"'
    }
