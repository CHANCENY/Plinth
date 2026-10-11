"""
UploadController
------------------
Serves files from the public disk (public/uploads/ by default - see core/services/StorageService.py)
at /uploads/<path>, which is where StorageService.url() points. Public: anyone with the URL can fetch
a file, the same as Drupal's public files.

    GET/HEAD  /uploads/<path:path>

Because these are user-uploaded files served from the site's own origin, a few precautions apply:
  * the path is checked so nothing outside the uploads directory can ever be read (no "..", no
    absolute paths, no symlink escapes, no encoded tricks - it's URL-decoded before checking)
  * X-Content-Type-Options: nosniff on everything, so a browser can't reinterpret a file's type
  * types that can run script in the page's origin (HTML, SVG, XML, JavaScript) are always sent as
    a download (Content-Disposition: attachment) instead of being rendered
  * the Content-Type comes from the file's extension, never from anything the uploader claimed

Also supported, because browsers expect them of media files: ETag / If-None-Match (304), and a
single Range request (206) - video and audio need it to seek.
The whole file is read into memory per request - fine for the dev server this project ships with;
put real static serving (nginx, a CDN) in front of /uploads/ for production.
"""

import mimetypes
import re
from email.utils import formatdate
from urllib.parse import unquote

from core.controllers.ControllerBase import ControllerBase
from core.http.response import Response
from core.http.routing.route import route
from core.services.MediaService import TYPES
from core.services.StorageService import StorageService

# types a browser would execute/render with the site's privileges if opened directly
FORCE_DOWNLOAD = {
    "text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml",
    "text/javascript", "application/javascript", "application/x-javascript",
}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class UploadController(ControllerBase):

    @route("/uploads/<path:path>", methods=["GET", "HEAD"])
    def show(self, request):
        root = StorageService(None).paths["public"].resolve()  # only paths are used - no database needed

        relative = unquote(request.get_route_parameter("path") or "")
        parts = relative.split("/")
        if any(part in ("", ".", "..") or "\\" in part or "\x00" in part for part in parts):
            return self.not_found("File not found")

        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            return self.not_found("File not found")

        stat = target.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        headers = {
            "ETag": etag,
            "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
            "Cache-Control": "public, max-age=3600",
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
        }
        if request.headers.get("If-None-Match") == etag:
            return Response(b"", Response.HTTP_NOT_MODIFIED, headers)

        extension = target.suffix.lower().lstrip(".")
        mime = (TYPES[extension][1] if extension in TYPES else None) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        headers["Content-Type"] = mime
        if mime in FORCE_DOWNLOAD:
            headers["Content-Disposition"] = 'attachment; filename="download"'

        content = target.read_bytes()

        range_header = request.headers.get("Range")
        if range_header:
            match = _RANGE_RE.match(range_header.strip())
            span = self._resolve_range(match, len(content)) if match else None
            if span is None:
                headers["Content-Range"] = f"bytes */{len(content)}"
                return Response(b"", 416, headers)
            start, end = span
            headers["Content-Range"] = f"bytes {start}-{end}/{len(content)}"
            return Response(content[start:end + 1], 206, headers)

        return Response(content, Response.HTTP_OK, headers)

    @staticmethod
    def _resolve_range(match, size):
        """(start, end) inclusive for a single 'bytes=a-b' / 'bytes=a-' / 'bytes=-n' range, or None if unsatisfiable."""
        first, last = match.group(1), match.group(2)
        if first == "" and last == "":
            return None
        if first == "":  # the final N bytes
            length = int(last)
            if length == 0:
                return None
            return max(size - length, 0), size - 1
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
        if start >= size or start > end:
            return None
        return start, end
