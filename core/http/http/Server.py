from http.server import BaseHTTPRequestHandler

from core.database.DatabaseConnection import DatabaseConnection
from core.http.request import Request
from core.http.response import Response
from core.http.routing.Routing import Routing
from core.services.AuthService import AuthService
from core.services.CorsService import CorsService
from core.services.RequestAuthService import AuthenticationFailed, RequestAuthService
from core.services.SessionService import SessionService


class Server(BaseHTTPRequestHandler):

    def _send(self, response):
        """Every response leaves through here, so CORS headers (when PUBLIC_API_ENABLED - see CorsService) reach
        errors and early rejections too, not just controller responses."""
        CorsService().apply(response, self.headers)
        response.send(self)

    def _handle_request(self):
        cors = CorsService()
        if self.command == "OPTIONS" and cors.is_preflight(self.headers):
            # A browser asking permission for a cross-origin request. Answered right here: no body to read,
            # no database, no session, no routing - it works for every URL, even one that has no route.
            self._send(cors.preflight(self.headers))
            return

        try:
            request = Request.from_handler(self)
        except ValueError as exc:
            # body over the size limit, or a malformed multipart body. The body was not read, so
            # the connection can't be reused - tell the client and close it.
            too_large = "exceeds maximum size" in str(exc)
            response = Response(
                "Payload Too Large" if too_large else f"Bad Request: {exc}",
                413 if too_large else Response.HTTP_BAD_REQUEST,
                {"Connection": "close"},
            )
            self.close_connection = True
            self._send(response)
            return

        # Resolve who's making this request once, here, so Routing can enforce each route's
        # "permission" list for real (previously current_user was never set, so
        # PermissionMiddleware/AuthenticationMiddleware could never actually pass). Cached on
        # the request itself (request._current_user) so ControllerBase.require_auth() reuses
        # this instead of re-querying the session/user from scratch in every controller method
        # and every view() call.
        db = DatabaseConnection()

        # Credentials in the request itself (API key, bearer token, signature ...) are checked first, by whatever
        # authenticators registered apps provide (core/services/RequestAuthService.py). Bad credentials end the
        # request here with a 401; none at all means "not an API call" and the session decides, as it always did.
        try:
            auth_result = RequestAuthService(db).authenticate(request)
        except AuthenticationFailed as exc:
            db.close()
            self._send(exc.to_response())
            return

        if auth_result is not None:
            current_user = auth_result.user
            request._auth = auth_result
        else:
            session = SessionService(db).start(request)
            current_user = AuthService(db, session).user()
        request._current_user = current_user

        routing = Routing(request)
        routing.set_current_user(current_user)

        response = routing.dispatch()
        db.close()

        if not isinstance(response, Response):
            response = Response(
                "Controller must return a Response instance.",
                Response.HTTP_INTERNAL_SERVER_ERROR
            )

        self._send(response)

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def do_PUT(self):
        self._handle_request()

    def do_HEAD(self):
        self._handle_request()

    def do_DELETE(self):
        self._handle_request()

    def do_OPTIONS(self):
        self._handle_request()

    def do_PATCH(self):
        self._handle_request()