"""
Roles and permissions declared by the built-in admin app.

`python console.py apps:discover` reads PERMISSIONS and ROLES from every registered app's
permissions.py and syncs them into the database (see core/apps/AppManager.py for the rules -
the main one: every key starts with the app's own name, "admin." here).

The two roles at the bottom are what the framework itself relies on (core/models/Role.py):
the first account ever registered becomes the administrator, and everyone who signs up
afterwards gets the default "member" role. Keep both defined.

Managing permissions themselves (the admin Permissions pages, and assigning roles/permissions
to users) is deliberately NOT a permission you can grant: those routes are administrator-only.
"""

PERMISSIONS = [
    {
        "key": "admin.users.manage",
        "label": "Manage users",
        "description": "List, add, edit and delete user accounts.",
    },
    {
        "key": "admin.media.manage",
        "label": "Manage media",
        "description": "Upload, edit and delete items in the media library.",
    },
    {
        "key": "admin.files.manage",
        "label": "Manage files",
        "description": "List, rename and delete uploaded files.",
    },
]

ROLES = [
    {
        "key": "admin.administrator",
        "label": "Administrator",
        "description": "Full access to everything, including permissions. Holds every permission automatically.",
        "superuser": True,
    },
    {
        "key": "admin.member",
        "label": "Member",
        "description": "The default role for new accounts.",
        "permissions": [],
    },
]
