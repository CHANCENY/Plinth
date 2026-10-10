from collections.abc import Iterator, Mapping
from typing import Any


class ParameterBag:
    """Generic container for request parameters."""

    def __init__(self, parameters: Mapping[str, Any] | None = None) -> None:
        self.parameters: dict[str, Any] = dict(parameters or {})

    def all(self) -> dict[str, Any]:
        return dict(self.parameters)

    def keys(self) -> list[str]:
        return list(self.parameters.keys())

    def has(self, key: str) -> bool:
        return key in self.parameters

    def get(self, key: str, default: Any = None) -> Any:
        return self.parameters.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.parameters[key] = value

    def add(self, parameters: Mapping[str, Any]) -> None:
        self.parameters.update(parameters)

    def remove(self, key: str) -> None:
        self.parameters.pop(key, None)

    def replace(self, parameters: Mapping[str, Any]) -> None:
        self.parameters = dict(parameters)

    def clear(self) -> None:
        self.parameters.clear()

    def __getitem__(self, key: str) -> Any:
        return self.parameters[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.parameters[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.parameters

    def __iter__(self) -> Iterator[str]:
        return iter(self.parameters)

    def __len__(self) -> int:
        return len(self.parameters)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.parameters!r})"
