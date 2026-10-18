"""
CorsService
-------------
Framework-level CORS, so a site built on Plinth can be used as a public API from pages on other origins
(a separate front end, a mobile web view, a third party's site). When it's switched on, apps don't deal
with CORS at all - they only need to authenticate their own requests (an API token in the Authorization
header, say), which is the point: the framework makes the site reachable, each app decides who gets in.

Switched on and configured in .env - with PUBLIC_API_ENABLED off (the default) nothing here does anything and
the server behaves exactly as it did before:

    PUBLIC_API_ENABLED=true
    PUBLIC_API_ALLOWED_ORIGINS=*          # "*" = any site; or a comma list: https://app.example.com,https://admin.example.com
    PUBLIC_API_ALLOWED_METHODS=GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS
    PUBLIC_API_ALLOWED_HEADERS=*          # "*" = allow whatever the browser asks for; or a list: Authorization,Content-Type
    PUBLIC_API_EXPOSE_HEADERS=            # response headers scripts may read, e.g. X-Total-Count
    PUBLIC_API_MAX_AGE=86400              # seconds a browser may cache a preflight answer

What it does (Server calls these - see core/http/http/Server.py)
    preflight   A browser asks permission before a "non-simple" cross-origin request (JSON body, PUT/DELETE,
                Authorization header ...) with OPTIONS + Origin + Access-Control-Request-Method. Server answers
                that itself with 204 and the Access-Control-Allow-* headers - no database, no session, no routing,
                so it works for every URL and costs nothing. An origin that isn't allowed gets 403.
    apply       Adds Access-Control-Allow-Origin (and Vary / Expose-Headers) to every response to a request that
                carried an Origin header - errors (401, 404, 405, 413 ...) included, because without the header
                the browser hides the response from scripts and the app can't even read its own error message.
                A response that already sets Access-Control-Allow-Origin is left alone, so a controller can
                override the policy for one route.

Deliberate choices
    * Credentials are not supported: Access-Control-Allow-Credentials is never sent, so browsers don't attach
      cookies to cross-origin requests and the session-cookie admin is not exposed to other sites. Token
      authentication (Authorization header) is what a public API uses instead.
    * With ALLOWED_HEADERS=* the browser's requested headers are echoed back (only well-formed header names).
      The CORS wildcard "*" would not cover Authorization, which is the header a public API needs most.
    * A plain OPTIONS request with no Origin / Access-Control-Request-Method is not a preflight and goes
      through normal routing like any other method.
"""

import re
from typing import Dict, List, Optional

from core.http.response import Response
from core.services.EnvService import EnvService

# a header field name (RFC 9110 "token") - used to refuse anything odd before echoing it into a response header
_TOKEN_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
DEFAULT_METHODS = "GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS"


class CorsService:
    def __init__(self, env: Optional[EnvService] = None):
        env = env or EnvService()
        self.enabled = env.get_bool("PUBLIC_API_ENABLED", False)
        self.origins: List[str] = [o.lower().rstrip("/") for o in env.get_list("PUBLIC_API_ALLOWED_ORIGINS", ["*"])]
        self.methods: List[str] = [m.upper() for m in env.get_list("PUBLIC_API_ALLOWED_METHODS", DEFAULT_METHODS.split(","))]
        self.allowed_headers: List[str] = env.get_list("PUBLIC_API_ALLOWED_HEADERS", ["*"])
        self.expose_headers: List[str] = env.get_list("PUBLIC_API_EXPOSE_HEADERS", [])
        self.max_age = env.get_int("PUBLIC_API_MAX_AGE", 86400)

    # ------------------------------------------------------------------ #
    @property
    def any_origin(self) -> bool:
        return "*" in self.origins

    def is_preflight(self, headers) -> bool:
        """True for OPTIONS requests that are CORS preflights (Origin + Access-Control-Request-Method present).
        Always False when the public API is switched off, so those requests keep their old behaviour."""
        return bool(
            self.enabled
            and headers.get("Origin")
            and headers.get("Access-Control-Request-Method")
        )

    def origin_allowed(self, origin: Optional[str]) -> bool:
        if not origin:
            return False
        return self.any_origin or origin.lower().rstrip("/") in self.origins

    # ------------------------------------------------------------------ #
    def preflight(self, headers) -> Response:
        """The answer to a preflight: 204 with the allow-headers, or 403 for an origin that isn't allowed."""
        origin = headers.get("Origin")
        if not self.origin_allowed(origin):
            return Response("Origin not allowed", Response.HTTP_FORBIDDEN, {"Vary": "Origin"})

        response = Response(b"", 204)
        self._allow_origin(response, origin)
        response.set_header("Access-Control-Allow-Methods", ", ".join(self.methods))

        allow_headers = self._allowed_request_headers(headers.get("Access-Control-Request-Headers"))
        if allow_headers:
            response.set_header("Access-Control-Allow-Headers", allow_headers)
        if self.max_age:
            response.set_header("Access-Control-Max-Age", str(self.max_age))

        self._vary(response, "Origin", "Access-Control-Request-Method", "Access-Control-Request-Headers")
        return response

    def apply(self, response: Response, headers) -> Response:
        """Adds the CORS headers to a normal response (a no-op unless enabled, the request had an Origin, and
        that origin is allowed). Returns the same response."""
        origin = headers.get("Origin") if self.enabled else None
        if not origin or not self.origin_allowed(origin):
            return response

        if not response.has_header("Access-Control-Allow-Origin"):  # a controller may set its own policy
            self._allow_origin(response, origin)
            if self.expose_headers:
                response.set_header("Access-Control-Expose-Headers", ", ".join(self.expose_headers))
        return response

    # ------------------------------------------------------------------ #
    def _allow_origin(self, response: Response, origin: str) -> None:
        if self.any_origin:
            response.set_header("Access-Control-Allow-Origin", "*")
        else:
            response.set_header("Access-Control-Allow-Origin", origin)  # echo the one origin that matched
            self._vary(response, "Origin")  # the answer depends on who asks - caches must not share it

    def _allowed_request_headers(self, requested: Optional[str]) -> str:
        """The Access-Control-Allow-Headers value: the configured list, or - for "*" - the headers the browser
        asked about (names only, and only well-formed ones, since this goes straight into a response header)."""
        if "*" not in self.allowed_headers:
            return ", ".join(self.allowed_headers)
        if not requested:
            return ""
        names = [name.strip() for name in requested.split(",")]
        return ", ".join(name for name in names if name and _TOKEN_RE.match(name))

    @staticmethod
    def _vary(response: Response, *names: str) -> None:
        existing = [v.strip() for v in (response.get_header("Vary") or "").split(",") if v.strip()]
        for name in names:
            if name.lower() not in (v.lower() for v in existing):
                existing.append(name)
        response.set_header("Vary", ", ".join(existing))
