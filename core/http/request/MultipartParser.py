"""
MultipartParser
-----------------
Parses a multipart/form-data request body - what a browser sends for a form with
enctype="multipart/form-data" (any form with a file input). Request.from_handler() uses it to fill
`request.request` (the text fields) and `request.files` (the uploads), the same places a
urlencoded form's fields end up.

    fields, files = MultipartParser.parse(body, content_type_header)

    fields   {"name": "value"} - a field sent more than once becomes a list, as for urlencoded forms
    files    {"files": UploadedFile} - several files under one name become a list of UploadedFile

An empty file input (no file chosen: filename="" and no content) is skipped, so a form with an
optional upload doesn't produce a phantom empty file. The whole body is held in memory, bounded by
the max_body_size check Request.from_handler already applies before reading it.
"""

import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote

from .UploadedFile import UploadedFile

_PARAM_RE = re.compile(r';\s*([A-Za-z0-9_*\-]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)')


class MultipartParser:

    @staticmethod
    def boundary_from(content_type: str) -> Optional[bytes]:
        """The boundary token out of 'multipart/form-data; boundary=...', or None."""
        boundary = MultipartParser._params(content_type).get("boundary")
        return boundary.encode("latin-1", errors="ignore") if boundary else None

    @classmethod
    def parse(cls, body: bytes, content_type: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        boundary = cls.boundary_from(content_type)
        if not boundary:
            raise ValueError("Malformed multipart/form-data request: no boundary")

        fields: Dict[str, Any] = {}
        files: Dict[str, Any] = {}

        # Delimiters are CRLF + "--" + boundary. Prefixing a CRLF lets the very first one match too,
        # and means a "--boundary" appearing mid-line inside file content can't split it.
        delimiter = b"\r\n--" + boundary
        pieces = (b"\r\n" + body).split(delimiter)

        # A real delimiter is followed by CRLF (next part) or "--" (end). Anything else is the
        # boundary text happening to appear inside content - glue that piece back on.
        merged = [pieces[0]]
        for piece in pieces[1:]:
            if piece.startswith((b"\r\n", b"--")):
                merged.append(piece)
            else:
                merged[-1] += delimiter + piece
        pieces = merged

        for piece in pieces[1:]:  # pieces[0] is the preamble
            if piece.startswith(b"--"):
                break  # closing delimiter
            if piece.startswith(b"\r\n"):
                piece = piece[2:]
            head, separator, content = piece.partition(b"\r\n\r\n")
            if not separator:
                continue

            headers = cls._parse_headers(head)
            disposition = headers.get("content-disposition", "")
            params = cls._params(disposition)
            name = params.get("name")
            if name is None:
                continue

            if "filename*" in params:  # RFC 5987: filename*=UTF-8''%e2%82%ac.txt
                params["filename"] = cls._decode_rfc5987(params["filename*"])

            if "filename" in params:
                filename = params["filename"]
                if filename == "" and not content:
                    continue  # no file chosen
                value: Any = UploadedFile(
                    filename,
                    content,
                    content_type=headers.get("content-type") or None,
                    size=len(content),
                )
                target = files
            else:
                value = content.decode("utf-8", errors="replace")
                target = fields

            if name not in target:
                target[name] = value
            elif isinstance(target[name], list):
                target[name].append(value)
            else:
                target[name] = [target[name], value]

        return fields, files

    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_headers(head: bytes) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for line in head.decode("utf-8", errors="replace").split("\r\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        return headers

    @staticmethod
    def _params(header_value: str) -> Dict[str, str]:
        """The '; key=value' parameters of a header value, keys lower-cased, quotes removed."""
        if ";" not in header_value:
            return {}
        return {
            key.lower(): MultipartParser._unquote(value)
            for key, value in _PARAM_RE.findall(header_value[header_value.index(";"):])
        }

    @staticmethod
    def _unquote(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = re.sub(r"\\(.)", r"\1", value[1:-1])
        # browsers percent-encode these three inside quoted values (WHATWG form-data rules)
        return value.replace("%22", '"').replace("%0D", "\r").replace("%0A", "\n")

    @staticmethod
    def _decode_rfc5987(value: str) -> str:
        parts = value.split("'", 2)
        if len(parts) == 3:
            return unquote(parts[2], encoding=parts[0] or "utf-8", errors="replace")
        return value
