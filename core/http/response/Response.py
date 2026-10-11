from http.server import BaseHTTPRequestHandler
from http.cookies import SimpleCookie, Morsel

class Response:
    HTTP_OK = 200
    HTTP_CREATED = 201
    HTTP_ACCEPTED = 202
    HTTP_NO_CONTENT = 204
    HTTP_MOVED_PERMANENTLY = 301
    HTTP_FOUND = 302
    HTTP_SEE_OTHER = 303
    HTTP_NOT_MODIFIED = 304
    HTTP_TEMPORARY_REDIRECT = 307
    HTTP_PERMANENT_REDIRECT = 308
    HTTP_BAD_REQUEST = 400
    HTTP_UNAUTHORIZED = 401
    HTTP_FORBIDDEN = 403
    HTTP_NOT_FOUND = 404
    HTTP_METHOD_NOT_ALLOWED = 405
    HTTP_INTERNAL_SERVER_ERROR = 500
    HTTP_SERVICE_UNAVAILABLE = 503

    def __init__(self, content=b"", status=200, headers=None):
        self.status_code = status
        self.headers = {}
        self.cookies = SimpleCookie()
        self.set_headers(headers or {})
        self.set_content(content)

    def set_content(self, content):
        self._content = content.encode("utf-8") if isinstance(content, str) else content
        return self

    def get_content(self):
        return self._content

    def get_content_as_text(self, encoding="utf-8"):
        return self._content.decode(encoding, errors="replace")

    @property
    def content(self):
        return self._content

    def set_status_code(self, status):
        if not 100 <= status <= 599:
            raise ValueError("HTTP status code must be between 100 and 599.")
        self.status_code = status
        return self

    def get_status_code(self):
        return self.status_code

    def set_header(self, name, value):
        self.headers[name] = str(value)
        return self

    def set_headers(self, headers):
        for name, value in headers.items():
            self.set_header(name, value)
        return self

    def get_header(self, name, default=None):
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return default

    def has_header(self, name):
        return self.get_header(name) is not None

    def remove_header(self, name):
        for key in list(self.headers):
            if key.lower() == name.lower():
                del self.headers[key]
        return self

    def get_headers(self):
        return dict(self.headers)

    def set_cookie(self, name, value="", *, max_age=None, expires=None,
                   path="/", domain=None, secure=False,
                   httponly=False, samesite=None):
        self.cookies[name] = value
        cookie: Morsel = self.cookies[name]
        if path:
            cookie["path"] = path
        if max_age is not None:
            cookie["max-age"] = str(max_age)
        if expires is not None:
            cookie["expires"] = expires
        if domain is not None:
            cookie["domain"] = domain
        if secure:
            cookie["secure"] = True
        if httponly:
            cookie["httponly"] = True
        if samesite is not None:
            cookie["samesite"] = samesite
        return self

    def delete_cookie(self, name, *, path="/", domain=None):
        return self.set_cookie(name, "", max_age=0, path=path, domain=domain)

    def prepare(self):
        if self.status_code in {204, 304}:
            self._content = b""
        if not self.has_header("Content-Length") and self.status_code not in {204, 304}:
            self.set_header("Content-Length", len(self._content))
        if self._content and not self.has_header("Content-Type"):
            self.set_header("Content-Type", "text/html; charset=utf-8")
        return self

    def send(self, handler: BaseHTTPRequestHandler):
        self.prepare()
        handler.send_response(self.status_code)
        for name, value in self.headers.items():
            handler.send_header(name, value)
        for morsel in self.cookies.values():
            handler.send_header("Set-Cookie", morsel.OutputString())
        handler.end_headers()
        if self._content and handler.command != "HEAD":
            handler.wfile.write(self._content)
