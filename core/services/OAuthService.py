"""
OAuthService
--------------
Facade for "social login" providers (Google/GitHub/Microsoft), configured
entirely from .env. AuthController talks to this instead of to individual
provider classes directly.

Expected .env keys - only the providers you actually configure need to be
set; an unconfigured provider raises a clear error only when it's actually
used (check is_configured() first if you want to hide unconfigured buttons):

    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI
    GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET / GITHUB_REDIRECT_URI
    MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET / MICROSOFT_REDIRECT_URI
    MICROSOFT_TENANT   (default: "common" - use your tenant id to restrict to one org)

Usage
-----
    oauth = OAuthService()

    url = oauth.authorize_url("google", state=csrf_state)   # step 1: send the user here

    profile = oauth.handle_callback("google", code=request.get("code"))  # step 2: provider redirected back
    # profile == {"provider_user_id": ..., "email": ..., "name": ...}
"""

from typing import Any, Dict, Optional

from core.services.EnvService import EnvService
from core.services.oauth.GithubOAuthProvider import GithubOAuthProvider
from core.services.oauth.GoogleOAuthProvider import GoogleOAuthProvider
from core.services.oauth.MicrosoftOAuthProvider import MicrosoftOAuthProvider

PROVIDERS = {
    "google": GoogleOAuthProvider,
    "github": GithubOAuthProvider,
    "microsoft": MicrosoftOAuthProvider,
}


class OAuthService:
    def __init__(self, env: Optional[EnvService] = None):
        self.env = env or EnvService()

    def is_configured(self, provider_name: str) -> bool:
        prefix = provider_name.upper()
        return bool(self.env.get(f"{prefix}_CLIENT_ID")) and bool(self.env.get(f"{prefix}_CLIENT_SECRET"))

    def get_provider(self, provider_name: str):
        provider_class = PROVIDERS.get(provider_name)
        if provider_class is None:
            raise ValueError(f"OAuthService: unknown provider '{provider_name}'. Expected one of {list(PROVIDERS)}")

        prefix = provider_name.upper()
        client_id = self.env.get(f"{prefix}_CLIENT_ID")
        client_secret = self.env.get(f"{prefix}_CLIENT_SECRET")
        redirect_uri = self.env.get(f"{prefix}_REDIRECT_URI")

        if provider_name == "microsoft":
            return provider_class(client_id, client_secret, redirect_uri, tenant=self.env.get("MICROSOFT_TENANT", "common"))

        return provider_class(client_id, client_secret, redirect_uri)

    def authorize_url(self, provider_name: str, state: str) -> str:
        return self.get_provider(provider_name).authorize_url(state)

    def handle_callback(self, provider_name: str, code: str) -> Dict[str, Any]:
        provider = self.get_provider(provider_name)
        access_token = provider.fetch_access_token(code)
        return provider.fetch_profile(access_token)
