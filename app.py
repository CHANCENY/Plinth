"""
app.py
-------
Registered apps - each is a self-contained folder under public/apps/<n>/,
optionally with its own controllers/, services/, views/, and models/ (see
core/apps/AppManager.py for exactly what's discovered from each, and
public/apps/blog/ for a working example of the expected layout).

"admin" is the built-in app that owns the default routes: login, register,
logout, forgot/reset password, OAuth sign-in, the admin dashboard, and the
users/files admin sections (public/apps/admin/). Routing no longer has any
routes of its own, so keep "admin" registered unless you're replacing all of that.

After adding/removing an app, or changing any of its routes or models,
refresh the cache the live server and the migration CLI actually read from:

    python console.py apps:discover
"""

REGISTERED_APPS = [
    "admin",
    "blog",
    "apiauth",
    "fakedata"
]
