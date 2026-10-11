"""
Roles and permissions declared by the blog app - an example of the convention every app follows
(see core/apps/AppManager.py): keys start with the app's own name, and `python console.py
apps:discover` syncs them into the database, where they show up as rows/columns on the admin
Permissions page.

Declaring "blog.create" doesn't protect anything by itself - a route opts in with
@route("/blog/new", permission=["blog.create"]) (PostController.create is still public for now).
"""

PERMISSIONS = [
    {
        "key": "blog.create",
        "label": "Create blog posts",
        "description": "Write and publish new posts.",
    },
]

ROLES = [
    {
        "key": "blog.editor",
        "label": "Blog editor",
        "description": "Can write blog posts.",
        "permissions": ["blog.create"],
    },
]
