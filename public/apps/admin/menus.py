"""
Dashboard sidebar items declared by the built-in admin app. `python console.py apps:discover`
collects every registered app's menus.py (rules and fields: core/apps/AppManager.py) and
partials/nav.html shows each signed-in user only the items they're allowed to see.

"permission" is the same list a route takes, and each item here mirrors the route it links to -
"administrator" means administrators only, exactly as on /admin/permissions itself.
"""

MENUS = [
    {
        "key": "admin.dashboard",
        "label": "Dashboard",
        "path": "/admin",
        "group": "Overview",
        "permission": ["authenticated"],
        "weight": 0,
        "exact": True,  # /admin is the prefix of every admin page - only light up on the dashboard itself
    },
    {
        "key": "admin.users",
        "label": "Users",
        "path": "/admin/users",
        "group": "Manage",
        "permission": ["admin.users.manage"],
        "weight": 100,
    },
    {
        "key": "admin.files",
        "label": "Files",
        "path": "/admin/files",
        "group": "Manage",
        "permission": ["admin.files.manage"],
        "weight": 110,
    },
    {
        "key": "admin.permissions",
        "label": "Permissions",
        "path": "/admin/permissions",
        "group": "Manage",
        "permission": ["administrator"],
        "weight": 120,
    },
]
