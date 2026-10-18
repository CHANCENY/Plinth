"""Small request/response helpers shared by the apiauth controllers."""

import base64
import binascii
from typing import Any, Dict, Optional, Tuple

from core.http.response import JsonResponse


def params(request) -> Dict[str, Any]:
    """The request's parameters whether they came as a JSON body or as a form / query string."""
    if request.is_json():
        data = request.json()
        if isinstance(data, dict):
            return data
    return request.all()


def no_store(data: Any, status: int = 200) -> JsonResponse:
    """A JSON response that must never be cached (token responses - RFC 6749 section 5.1)."""
    return JsonResponse(data, status=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def error(code: str, description: str, status: int = 400, **extra: Any) -> JsonResponse:
    body = {"error": code, "error_description": description, **extra}
    return JsonResponse(body, status=status, headers={"Cache-Control": "no-store"})


def basic_credentials(request) -> Optional[Tuple[str, str]]:
    """(username, password) from an Authorization: Basic header, or None."""
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic" or not value.strip():
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    username, sep, password = decoded.partition(":")
    return (username, password) if sep else None
