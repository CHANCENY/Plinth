from typing import Any

from .ParameterBag import ParameterBag


class FileBag(ParameterBag):
    """Uploaded files associated with the request."""

    def get_uploaded_file(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        return self.get(key, default)
