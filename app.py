"""
app.py
-------
Registered apps - each is a self-contained folder under public/apps/<name>/,
optionally with its own controllers/, services/, views/, and models/ (see
core/apps/AppManager.py for exactly what's discovered from each, and
public/apps/blog/ for a working example of the expected layout).

After adding/removing an app, or changing any of its routes or models,
refresh the cache the live server and the migration CLI actually read from:

    python console.py apps:discover
"""

REGISTERED_APPS = [
    "blog",
]
