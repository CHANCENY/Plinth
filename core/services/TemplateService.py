"""
TemplateService
-----------------
Renders HTML views out of public/views/<theme>/<view>.html using Jinja2,
where each top-level directory under public/views/ is a "theme" (matching
how the project wants views organized: public/views/admin_theme/, public/
views/storefront_theme/, etc.). Which theme is used by default comes from
.env:

    THEME_DEFAULT=admin_theme

Controllers call render() (or render_response() to get a ready-to-return
Response object) to turn a view name + a context dict into HTML. Each theme
gets its own Jinja2 Environment, rooted at that theme's directory, so
{% extends %}/{% include %} paths are always relative to the current theme
and one theme's views can't reach into another's by accident.

Project layout assumed:
    project/
        .env
        core/
            services/
                TemplateService.py   <- this file
        public/
            views/
                admin_theme/          <- one directory per theme (THEME_DEFAULT picks one)
                    layouts/
                        admin.html
                    users/
                        index.html
                    partials/
                        nav.html

This is plain Jinja2 syntax - see https://jinja.palletsprojects.com/templates/
for the full language ({{ }}, {% if %}/{% for %}, {% extends %}/{% block %},
{% include %}, filters like |default('x')/|upper/|length/|join(', ')/|safe,
comments {# #}, etc.). Autoescaping is on for .html/.htm/.xml templates, so
output is HTML-escaped by default - use the |safe filter for trusted HTML.

Missing context values use Jinja's ChainableUndefined rather than the
stricter default, so a chain like {{ current_user.name }} degrades to an
empty string when current_user itself is missing, instead of raising -
matching how a plain dict.get() lookup would behave, so a view doesn't 500
just because an optional piece of context wasn't passed in.

Trust model: this uses Jinja2's normal (non-sandboxed) Environment, the same
default Flask and most other Python web frameworks use - a template can call
methods and access attributes on whatever objects you pass into its context.
That's the right default for theme files written by this project's own
developers, but it is NOT a barrier against template code written by an
untrusted party (e.g. templates editable by end users). If that ever becomes
a requirement, swap Environment for jinja2.sandbox.SandboxedEnvironment.

Expected .env keys
---------------------
THEME_DEFAULT   default theme directory name under public/views/ (default: "default")

Usage
-----
    from core.services.TemplateService import TemplateService

    views = TemplateService()

    html = views.render("users/index", {"users": User.all(db)})

    # in a controller, returns a ready-to-send Response
    return views.render_response("users/index", {"users": User.all(db)})

    # override the theme for one render (e.g. a storefront-facing page from an admin app)
    html = views.render("checkout/cart", {"cart": cart}, theme="storefront_theme")

App views: prefix the view name with "<app_name>:" to render out of
public/apps/<app_name>/views/ instead of a theme (see core/apps/AppManager.py
for the rest of the apps system). An app's views are fully independent of
every theme - its own {% extends %}/{% include %} only ever resolve within
that same app's views/ directory:

    html = views.render("blog:posts/index", {"posts": posts})
    # -> public/apps/blog/views/posts/index.html
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from core.services.EnvService import EnvService


class _ExtensionlessLoader(FileSystemLoader):
    """
    FileSystemLoader that fills in the .html extension when a template
    reference doesn't already have one - so {% extends "layouts/admin" %} and
    {% include "partials/nav" %} inside templates work the same extension-free
    way as views.render("users/index") does from Python.
    """

    def get_source(self, environment, template):
        if not template.endswith((".html", ".htm", ".xml")):
            template = f"{template}.html"
        return super().get_source(environment, template)


class TemplateService:
    def __init__(self, theme: Optional[str] = None, env: Optional[EnvService] = None):
        env = env or EnvService()
        self.default_theme = theme or env.get("THEME_DEFAULT", "default")
        project_root = Path(self._project_root())
        self.views_root = project_root / "public" / "views"
        self.apps_root = project_root / "public" / "apps"
        self._environments: Dict[str, Environment] = {}  # keyed by root dir, one Environment each

    # ------------------------------------------------------------------ #
    # setup
    # ------------------------------------------------------------------ #
    def _project_root(self) -> str:
        # this file lives at project/core/services/TemplateService.py
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "../", ".."))

    def _environment_for(self, root: Path) -> Environment:
        """One Jinja2 Environment per root directory (a theme, or an app's views/), created
        lazily and cached for the life of this service."""
        key = str(root)
        if key not in self._environments:
            self._environments[key] = Environment(
                loader=_ExtensionlessLoader(str(root)),
                autoescape=select_autoescape(["html", "htm", "xml"]),
                trim_blocks=True,
                lstrip_blocks=True,
                # lets `missing.attr.deeper` degrade to "" instead of raising, matching how
                # a controller-side dict.get()-style lookup would behave - only the LAST access
                # in a chain still fails loudly if you try to do real work with the result
                # (e.g. call it, iterate a non-iterable) rather than just print/test it
                undefined=ChainableUndefined,
            )
        return self._environments[key]

    @staticmethod
    def _template_name(view: str) -> str:
        view = view.strip()
        # a view already given with a real template extension is used as-is - only a bare
        # name/dotted-path like "index" or "admin.index" gets ".html" appended and its dots
        # turned into path separators (Blade-style: "admin.index" -> "admin/index.html")
        if view.endswith((".html", ".htm", ".xml")):
            return view.lstrip("/")
        return view.replace(".", "/").lstrip("/") + ".html"

    def _resolve(self, view: str, theme: Optional[str]):
        """Returns (environment, template_name, root) - root is only used for error messages."""
        if ":" in view:
            app_name, _, inner_view = view.partition(":")
            root = self.apps_root / app_name / "views"
            return self._environment_for(root), self._template_name(inner_view), root
        root = self.views_root / (theme or self.default_theme)
        return self._environment_for(root), self._template_name(view), root

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def render(self, view: str, context: Optional[Dict[str, Any]] = None, theme: Optional[str] = None) -> str:
        environment, template_name, root = self._resolve(view, theme)
        try:
            template = environment.get_template(template_name)
        except TemplateNotFound as exc:
            raise FileNotFoundError(
                f"TemplateService: view '{view}' not found (looked for {root / template_name})"
            ) from exc
        return template.render(**(context or {}))

    def render_response(
        self,
        view: str,
        context: Optional[Dict[str, Any]] = None,
        theme: Optional[str] = None,
        status: int = 200,
        headers: Optional[Dict[str, str]] = None,
    ):
        """Renders a view and wraps it in a ready-to-return Response."""
        from core.http.response import Response

        return Response(self.render(view, context, theme), status=status, headers=headers or {})

    def exists(self, view: str, theme: Optional[str] = None) -> bool:
        _, template_name, root = self._resolve(view, theme)
        return (root / template_name).exists()
