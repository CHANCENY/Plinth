from typing import Any, Dict, Optional

from core.services.oauth.BaseOAuthProvider import BaseOAuthProvider


class MicrosoftOAuthProvider(BaseOAuthProvider):
    name = "microsoft"
    profile_endpoint = "https://graph.microsoft.com/v1.0/me"
    scope = "openid email profile User.Read"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, tenant: Optional[str] = None):
        super().__init__(client_id, client_secret, redirect_uri)
        self.tenant = tenant or "common"
        self.authorize_endpoint = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/authorize"
        self.token_endpoint = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"

    def normalize_profile(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "provider_user_id": raw.get("id"),
            "email": raw.get("mail") or raw.get("userPrincipalName"),
            "name": raw.get("displayName"),
        }
