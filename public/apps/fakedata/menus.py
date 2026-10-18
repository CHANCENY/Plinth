"""Dashboard sidebar item for the fakedata app (see core/apps/AppManager.py). Administrators only."""

MENUS = [
    {
        "key": "fakedata.generate",
        "label": "Fake data",
        "path": "/admin/fake-data",
        "group": "Developer",
        "permission": ["administrator"],
        "weight": 900,
    },
]
