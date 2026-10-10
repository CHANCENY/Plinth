from pathlib import Path
from typing import BinaryIO
import mimetypes


class UploadedFile:
    """Represents a file uploaded through an HTTP request."""

    def __init__(
        self,
        filename: str,
        content: bytes | BinaryIO,
        *,
        content_type: str | None = None,
        size: int | None = None,
        error: int = 0,
        tmp_name: str | None = None,
    ) -> None:
        self.filename = filename
        self.original_name = filename
        self.content_type = (
            content_type
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        self.error = error
        self.tmp_name = tmp_name

        if isinstance(content, bytes):
            self._content = content
            self._stream = None
            self.size = len(content) if size is None else size
        else:
            self._content = None
            self._stream = content
            self.size = size

    def is_valid(self) -> bool:
        return self.error == 0

    def get_client_original_name(self) -> str:
        return self.original_name

    def get_client_mime_type(self) -> str:
        return self.content_type

    def get_size(self) -> int | None:
        return self.size

    def get_error(self) -> int:
        return self.error

    def get_content(self) -> bytes:
        if self._content is not None:
            return self._content

        if self._stream is None:
            return b""

        current_position = self._stream.tell()
        self._stream.seek(0)

        content = self._stream.read()

        self._stream.seek(current_position)

        return content

    def move(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if self._stream is not None:
            self._stream.seek(0)

            with destination.open("wb") as output:
                while True:
                    chunk = self._stream.read(1024 * 1024)

                    if not chunk:
                        break

                    output.write(chunk)
        else:
            destination.write_bytes(self._content or b"")

        self.tmp_name = str(destination)

        return destination

    def __repr__(self) -> str:
        return (
            f"UploadedFile("
            f"filename={self.filename!r}, "
            f"content_type={self.content_type!r}, "
            f"size={self.size!r}"
            f")"
        )
