"""
Dashboard sidebar items declared by the blog app - an example of how any app adds to the admin
sidebar (see core/apps/AppManager.py for the fields). This one only appears for users who hold the
"blog.create" permission declared in permissions.py (administrators hold every permission).
"""

MENUS = [
    {
        "key": "blog.new_post",
        "label": "New blog post",
        "path": "/blog/new",
        "group": "Content",
        "permission": ["blog.create"],
        "weight": 200,
    },
]
