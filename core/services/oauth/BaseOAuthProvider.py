"""
BaseOAuthProvider
-------------------
Shared logic for "social login" OAuth2 providers (Google/GitHub/Microsoft).
Each provider gets its own subclass because the authorize URL, token
endpoint, profile endpoint, and how each one shapes its user-info response
all differ - what's shared is the standard OAuth2 authorization-code flow
itself: build a redirect URL with a CSRF state, exchange the returned code
for an access token, then use that token to fetch a profile.

Uses stdlib urllib only - no third-party HTTP client dependency.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


class OAuthError(Exception):
    """Raised when a provider's token or profile endpoint returns something unusable."""


class BaseOAuthProvider:
    name: str = ""  # e.g. "google" - matches the /auth/<provider>/... route segment
    authorize_endpoint: str = ""
    token_endpoint: str = ""
    profile_endpoint: str = ""
    scope: str = ""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        if not client_id or not client_secret:
            raise ValueError(
                f"OAuth provider '{self.name}' is missing its client id/secret - "
                f"set them in .env before using it."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    # ------------------------------------------------------------------ #
    # step 1: send the user to the provider to log in / grant access
    # ------------------------------------------------------------------ #
    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "state": state,
            **self.extra_authorize_params(),
        }
        return f"{self.authorize_endpoint}?{urllib.parse.urlencode(params)}"

    def extra_authorize_params(self) -> Dict[str, str]:
        """Hook for provider-specific extra query params (e.g. Google's access_type/prompt)."""
        return {}

    # ------------------------------------------------------------------ #
    # step 2: exchange the authorization code for an access token
    # ------------------------------------------------------------------ #
    def fetch_access_token(self, code: str) -> str:
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        payload = self._post(self.token_endpoint, data, headers={"Accept": "application/json"})
        token = payload.get("access_token")
        if not token:
            raise OAuthError(f"OAuth provider '{self.name}': token response had no access_token: {payload}")
        return token

    # ------------------------------------------------------------------ #
    # step 3: use the access token to fetch a normalized profile
    # ------------------------------------------------------------------ #
    def fetch_profile(self, access_token: str) -> Dict[str, Any]:
        """Returns {"provider_user_id": str, "email": str|None, "name": str|None}."""
        raw = self._get(self.profile_endpoint, headers={"Authorization": f"Bearer {access_token}"})
        return self.normalize_profile(raw)

    def normalize_profile(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # http helpers (stdlib only)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _post(url: str, data: Dict[str, str], headers: Optional[Dict[str, str]] = None) -> Any:
        body = urllib.parse.urlencode(data).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OAuthError(
                f"OAuth request to {url} failed: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OAuthError(f"OAuth request to {url} failed: {exc.reason}") from exc

    @staticmethod
    def _get(url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OAuthError(
                f"OAuth request to {url} failed: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OAuthError(f"OAuth request to {url} failed: {exc.reason}") from exc
