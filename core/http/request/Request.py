from __future__ import annotations

import json
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from .ParameterBag import ParameterBag
from .InputBag import InputBag
from .HeaderBag import HeaderBag
from .ServerBag import ServerBag
from .CookieBag import CookieBag
from .FileBag import FileBag
from .MultipartParser import MultipartParser


class Request:
    """
    Symfony-inspired HTTP Request object.

    Designed to be created directly or from Python's
    BaseHTTPRequestHandler.
    """

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"
    TRACE = "TRACE"
    CONNECT = "CONNECT"

    def __init__(
        self,
        *,
        method: str = "GET",
        uri: str = "/",
        headers: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        request: Mapping[str, Any] | None = None,
        cookies: Mapping[str, Any] | None = None,
        server: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        content: bytes | str = b"",
        handler: BaseHTTPRequestHandler | None = None,
    ) -> None:
        self.method = method.upper()
        self.uri = uri
        self.handler = handler
        self.route_parameters = {}

        parsed = urlsplit(uri)

        self.path = parsed.path or "/"
        self.query_string = parsed.query
        self.fragment = parsed.fragment

        self.headers = HeaderBag(headers)
        self.query = InputBag(query)
        self.request = InputBag(request)
        self.cookies = CookieBag(cookies)
        self.server = ServerBag(server)
        self.files = FileBag(files)

        if isinstance(content, str):
            content = content.encode("utf-8")

        self._content = content
        self._json_loaded = False
        self._json_data: Any = None

        self.scheme = self._detect_scheme()

    @classmethod
    def from_handler(
        cls,
        handler: BaseHTTPRequestHandler,
        *,
        max_body_size: int | None = 50 * 1024 * 1024,
    ) -> "Request":
        """Create a Request from BaseHTTPRequestHandler."""

        uri = handler.path
        parsed = urlsplit(uri)

        headers = {
            key: value
            for key, value in handler.headers.items()
        }

        query = cls._parse_query(parsed.query)

        try:
            content_length = int(headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        if max_body_size is not None and content_length > max_body_size:
            raise ValueError(
                f"Request body exceeds maximum size of "
                f"{max_body_size} bytes"
            )

        content = b""

        if content_length > 0:
            content = handler.rfile.read(content_length)

        content_type = headers.get("Content-Type", "")
        request_data = {}
        files = {}

        media_type = content_type.lower().split(";", 1)[0].strip()

        if content and media_type == "application/x-www-form-urlencoded":
            request_data = cls._parse_query(
                content.decode("utf-8", errors="replace")
            )
        elif content and media_type == "multipart/form-data":
            # text fields land in request_data like a urlencoded form's; uploads in request.files
            request_data, files = MultipartParser.parse(content, content_type)

        cookies = cls._parse_cookies(
            headers.get("Cookie", "")
        )

        server = cls._build_server(
            handler,
            parsed,
            headers,
        )

        return cls(
            method=handler.command,
            uri=uri,
            headers=headers,
            query=query,
            request=request_data,
            cookies=cookies,
            server=server,
            files=files,
            content=content,
            handler=handler,
        )

    @staticmethod
    def _parse_query(query: str) -> dict[str, Any]:
        result: dict[str, Any] = {}

        for key, value in parse_qsl(
            query,
            keep_blank_values=True,
        ):
            if key not in result:
                result[key] = value
            elif isinstance(result[key], list):
                result[key].append(value)
            else:
                result[key] = [
                    result[key],
                    value,
                ]

        return result

    @staticmethod
    def _parse_cookies(
        cookie_header: str,
    ) -> dict[str, str]:
        if not cookie_header:
            return {}

        cookie = SimpleCookie()

        try:
            cookie.load(cookie_header)
        except Exception:
            return {}

        return {
            key: morsel.value
            for key, morsel in cookie.items()
        }

    @staticmethod
    def _build_server(
        handler: BaseHTTPRequestHandler,
        parsed: Any,
        headers: Mapping[str, Any],
    ) -> dict[str, Any]:
        host = headers.get("Host", "")

        if host:
            server_name = host.split(":", 1)[0]
        else:
            server_name = handler.server.server_address[0]

        client_address = handler.client_address

        remote_addr = (
            client_address[0]
            if client_address
            else ""
        )

        remote_port = (
            client_address[1]
            if len(client_address) > 1
            else ""
        )

        server_address = handler.server.server_address

        return {
            "REQUEST_METHOD": handler.command,
            "REQUEST_URI": handler.path,
            "QUERY_STRING": parsed.query,
            "SERVER_NAME": server_name,
            "SERVER_PORT": server_address[1],
            "REMOTE_ADDR": remote_addr,
            "REMOTE_PORT": remote_port,
            "SERVER_PROTOCOL": handler.request_version,
            "HTTP_HOST": host,
            "SERVER_ADDRESS": server_address[0],
            "SERVER_SOFTWARE": getattr(
                handler.server,
                "server_version",
                "",
            ),
            "GATEWAY_INTERFACE": "CGI/1.1",
        }

    def _detect_scheme(self) -> str:
        forwarded = self.headers.get(
            "X-Forwarded-Proto",
            "",
        )

        if forwarded:
            return forwarded.split(",", 1)[0].strip().lower()

        return "http"

    def get(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        if self.request.has(key):
            return self.request.get(key, default)

        return self.query.get(key, default)

    def has(self, key: str) -> bool:
        return (
            self.request.has(key)
            or self.query.has(key)
        )

    def all(self) -> dict[str, Any]:
        values = self.query.all()
        values.update(self.request.all())

        return values

    def get_content(
        self,
        as_text: bool = False,
    ) -> bytes | str:
        if as_text:
            return self._content.decode(
                "utf-8",
                errors="replace",
            )

        return self._content

    @property
    def content(self) -> bytes:
        return self._content

    def json(self, default: Any = None) -> Any:
        if self._json_loaded:
            return self._json_data

        self._json_loaded = True

        if not self._content:
            self._json_data = default
            return default

        try:
            self._json_data = json.loads(
                self._content.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            self._json_data = default

        return self._json_data

    def is_method(self, method: str) -> bool:
        return self.method == method.upper()

    def is_secure(self) -> bool:
        return self.scheme == "https"

    def get_scheme(self) -> str:
        return self.scheme

    def get_host(self) -> str:
        host = self.headers.get("Host", "")

        if host:
            return host.split(":", 1)[0]

        return str(
            self.server.get(
                "SERVER_NAME",
                "",
            )
        )

    def get_port(self) -> int | None:
        host = self.headers.get("Host", "")

        if ":" in host:
            try:
                return int(host.rsplit(":", 1)[1])
            except ValueError:
                pass

        try:
            return int(
                self.server.get(
                    "SERVER_PORT"
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            return None

    def get_uri(self) -> str:
        return self.uri

    def get_path_info(self) -> str:
        return self.path

    def get_query_string(self) -> str:
        return self.query_string

    def get_request_uri(self) -> str:
        return self.uri

    def get_content_type(self) -> str | None:
        content_type = self.headers.get(
            "Content-Type"
        )

        if not content_type:
            return None

        return content_type.split(
            ";",
            1,
        )[0].strip().lower()

    def is_json(self) -> bool:
        content_type = self.get_content_type()

        if not content_type:
            return False

        return (
            content_type == "application/json"
            or content_type.endswith("+json")
        )

    def get_client_ip(
        self,
        trusted_proxies: set[str] | None = None,
    ) -> str | None:
        remote_addr = self.server.get(
            "REMOTE_ADDR"
        )

        if not remote_addr:
            return None

        if (
            trusted_proxies
            and remote_addr in trusted_proxies
        ):
            forwarded = self.headers.get(
                "X-Forwarded-For"
            )

            if forwarded:
                return forwarded.split(
                    ",",
                    1,
                )[0].strip()

            real_ip = self.headers.get(
                "X-Real-IP"
            )

            if real_ip:
                return real_ip.strip()

        return str(remote_addr)

    @property
    def client_ip(self) -> str | None:
        return self.get_client_ip()

    def user_agent(self) -> str | None:
        return self.headers.get("User-Agent")

    def referer(self) -> str | None:
        return self.headers.get("Referer")

    def is_xml_http_request(self) -> bool:
        return (
            self.headers.get(
                "X-Requested-With",
                "",
            ).lower()
            == "xmlhttprequest"
        )

    def bearer_token(self) -> str | None:
        authorization = self.headers.get(
            "Authorization",
            "",
        )

        if authorization.lower().startswith(
            "bearer "
        ):
            return authorization[7:].strip() or None

        return None

    def get_languages(self) -> list[str]:
        value = self.headers.get(
            "Accept-Language",
            "",
        )

        languages = []

        for item in value.split(","):
            item = item.strip()

            if not item:
                continue

            parts = [
                part.strip()
                for part in item.split(";")
            ]

            language = parts[0]
            quality = 1.0

            for part in parts[1:]:
                if part.startswith("q="):
                    try:
                        quality = float(
                            part[2:]
                        )
                    except ValueError:
                        pass

            languages.append(
                (quality, language)
            )

        languages.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        return [
            language
            for _, language in languages
        ]

    def __repr__(self) -> str:
        return (
            f"<Request "
            f"{self.method} "
            f"{self.uri!r}>"
        )

    def set_route_parameters(self, parameters):
        self.route_parameters = dict(parameters)
        return self

    def get_route_parameter(self, name, default=None):
        return self.route_parameters.get(
            name,
            default
        )

    def get_route_parameters(self):
        return dict(self.route_parameters)