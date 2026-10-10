from collections.abc import Mapping
from typing import Any

from .ParameterBag import ParameterBag


class HeaderBag(ParameterBag):
    """Case-insensitive HTTP header collection."""

    @staticmethod
    def _normalize(key: str) -> str:
        return str(key).lower().replace("_", "-")

    def __init__(self, headers: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        self.replace(headers or {})

    def replace(self, headers: Mapping[str, Any]) -> None:
        self.parameters = {
            self._normalize(key): value
            for key, value in headers.items()
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.parameters.get(self._normalize(key), default)

    def has(self, key: str) -> bool:
        return self._normalize(key) in self.parameters

    def set(self, key: str, value: Any) -> None:
        self.parameters[self._normalize(key)] = value

    def add(self, headers: Mapping[str, Any]) -> None:
        for key, value in headers.items():
            self.set(key, value)

    def remove(self, key: str) -> None:
        self.parameters.pop(self._normalize(key), None)
