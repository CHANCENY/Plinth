from http.server import BaseHTTPRequestHandler

from core.database.DatabaseConnection import DatabaseConnection
from core.http.request import Request
from core.http.response import Response
from core.http.routing.Routing import Routing
from core.services.AuthService import AuthService
from core.services.SessionService import SessionService


class Server(BaseHTTPRequestHandler):

    def _handle_request(self):
        request = Request.from_handler(self)

        # Resolve who's making this request once, here, so Routing can enforce each route's
        # "permission" list for real (previously current_user was never set, so
        # PermissionMiddleware/AuthenticationMiddleware could never actually pass). Cached on
        # the request itself (request._current_user) so ControllerBase.require_auth() reuses
        # this instead of re-querying the session/user from scratch in every controller method
        # and every view() call.
        db = DatabaseConnection()
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

        response.send(self)

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