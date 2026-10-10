from typing import Any

from .ParameterBag import ParameterBag


class InputBag(ParameterBag):
    """Bag for HTTP input such as query and form parameters."""

    def get_string(self, key: str, default: str | None = None) -> str | None:
        value = self.get(key, default)

        if value is None:
            return default

        if isinstance(value, (dict, list, tuple)):
            return default

        return str(value)

    def get_int(self, key: str, default: int | None = None) -> int | None:
        value = self.get(key)

        if value is None or isinstance(value, (dict, list, tuple)):
            return default

        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float | None = None) -> float | None:
        value = self.get(key)

        if value is None or isinstance(value, (dict, list, tuple)):
            return default

        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool | None = None) -> bool | None:
        value = self.get(key)

        if value is None:
            return default

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {"1", "true", "yes", "on"}:
                return True

            if normalized in {"0", "false", "no", "off"}:
                return False

        if isinstance(value, (int, float)):
            return bool(value)

        return default
