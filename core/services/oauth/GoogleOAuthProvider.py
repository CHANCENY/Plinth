from typing import Any, Dict

from core.services.oauth.BaseOAuthProvider import BaseOAuthProvider


class GoogleOAuthProvider(BaseOAuthProvider):
    name = "google"
    authorize_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    profile_endpoint = "https://www.googleapis.com/oauth2/v3/userinfo"
    scope = "openid email profile"

    def extra_authorize_params(self) -> Dict[str, str]:
        return {"access_type": "online", "prompt": "select_account"}

    def normalize_profile(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "provider_user_id": raw.get("sub"),
            "email": raw.get("email"),
            "name": raw.get("name") or raw.get("email"),
        }
