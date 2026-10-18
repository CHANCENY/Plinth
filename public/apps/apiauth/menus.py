"""Dashboard sidebar items for the apiauth app (see core/apps/AppManager.py)."""

MENUS = [
    {
        "key": "apiauth.keys",
        "label": "API keys",
        "path": "/admin/api-keys",
        "group": "API",
        "permission": ["apiauth.keys.manage"],
        "weight": 300,
    },
    {
        "key": "apiauth.credentials",
        "label": "API credentials",
        "path": "/admin/api-credentials",
        "group": "API",
        "permission": ["apiauth.keys.admin"],
        "weight": 310,
    },
]
