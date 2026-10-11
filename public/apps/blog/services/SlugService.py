"""
SlugService
-------------
Demo service for the "blog" app. Nothing special about this file - app
services aren't discovered/cached the way routes and models are (see
core/apps/AppManager.py's docstring for why not); they're just normal
classes an app's own controllers import directly, the same as any service
in core/services/.
"""

import re


class SlugService:
    @staticmethod
    def slugify(title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return slug or "post"
