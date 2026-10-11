from typing import Any, Dict

from core.services.oauth.BaseOAuthProvider import BaseOAuthProvider


class GithubOAuthProvider(BaseOAuthProvider):
    name = "github"
    authorize_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    profile_endpoint = "https://api.github.com/user"
    scope = "read:user user:email"

    def fetch_profile(self, access_token: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        raw = self._get(self.profile_endpoint, headers=headers)

        # GitHub omits `email` from /user when the user has it set private -
        # fall back to the emails endpoint and use their verified primary address.
        email = raw.get("email")
        if not email:
            emails = self._get("https://api.github.com/user/emails", headers=headers)
            email = next(
                (e["email"] for e in emails if e.get("primary") and e.get("verified")),
                next((e["email"] for e in emails if e.get("verified")), None),
            )

        return self.normalize_profile({**raw, "_resolved_email": email})

    def normalize_profile(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "provider_user_id": str(raw.get("id")) if raw.get("id") is not None else None,
            "email": raw.get("_resolved_email") or raw.get("email"),
            "name": raw.get("name") or raw.get("login"),
        }
