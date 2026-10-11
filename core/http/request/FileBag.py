from typing import Any, List

from .ParameterBag import ParameterBag


class FileBag(ParameterBag):
    """Uploaded files associated with the request."""

    def get_uploaded_file(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        """The upload under `key` - the first one if several were sent under that name."""
        value = self.get(key, default)
        if isinstance(value, list):
            return value[0] if value else default
        return value

    def get_uploaded_files(self, key: str) -> List[Any]:
        """Every upload under `key` (<input type="file" name="..." multiple>) as a list - empty if none."""
        value = self.get(key)
        if value is None:
            return []
        return list(value) if isinstance(value, (list, tuple)) else [value]
