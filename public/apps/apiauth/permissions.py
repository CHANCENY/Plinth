"""
Permissions and a role declared by the apiauth app (see core/apps/AppManager.py for the rules).

Anyone with an active account can already sign in to the token endpoint with their password. What these control
is who may create long-lived API keys - which is more power to hand out - and who can see everyone's.
"""

PERMISSIONS = [
    {
        "key": "apiauth.keys.manage",
        "label": "Manage own API keys",
        "description": "Create, rotate and revoke API keys that act as this account.",
    },
    {
        "key": "apiauth.keys.admin",
        "label": "Manage everyone's API keys",
        "description": "See and revoke the API keys and tokens of every account.",
    },
]

ROLES = [
    {
        "key": "apiauth.developer",
        "label": "API developer",
        "description": "Can create their own API keys.",
        "permissions": ["apiauth.keys.manage"],
    },
]
