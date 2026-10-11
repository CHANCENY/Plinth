"""
EnvService
-----------
Central place for reading and writing the project's .env file: typed reads
(get_bool/get_int/get_float/get_list), an in-memory + os.environ view of the
current values, and a set() that can persist a change back to the .env file
on disk (updating the existing line if the key is already there, otherwise
appending it - comments and unrelated lines are left untouched).

Project layout assumed - same as DatabaseConnection / MailService:
    project/
        .env
        core/
            services/
                EnvService.py   <- this file

This class doesn't depend on python-dotenv - it parses KEY=VALUE lines
itself, so .env can always be re-read/re-written exactly regardless of
whether that package is installed. Every loaded value is still pushed into
os.environ, so plain os.getenv() keeps working everywhere else in the app
(DatabaseConnection, MailService, etc. all still work unmodified).

Usage
-----
    from core.services.EnvService import EnvService

    env = EnvService()

    env.get("APP_NAME", "MyApp")
    env.get_bool("APP_DEBUG", False)
    env.get_int("DB_PORT", 3306)
    env.get_list("ALLOWED_HOSTS")                 # "a,b,c" -> ["a", "b", "c"]
    env.required("MAIL_FROM_ADDRESS")             # raises if missing/blank

    env.has("MAIL_HOST")
    env.all()                                     # dict of every parsed key/value

    env.set("APP_DEBUG", True)                    # updates in-memory + os.environ only
    env.set("APP_DEBUG", True, persist=True)       # also rewrites .env on disk

    env.reload()                                   # re-read .env from disk, discarding in-memory changes
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}

_LINE_RE = re.compile(
    r"""^\s*
        (?:export\s+)?              # optional leading "export "
        (?P<key>[A-Za-z_][A-Za-z0-9_]*)
        \s*=\s*
        (?P<value>.*)
        \s*$""",
    re.VERBOSE,
)


class EnvService:
    def __init__(self, env_path: Optional[str] = None, autoload: bool = True):
        """
        env_path: optional explicit path to the .env file. If omitted, EnvService
                  walks up from core/services/ to the project root and uses .env there.
        autoload: whether to parse the file immediately (default True).
        """
        self.path = Path(env_path) if env_path else Path(self._project_root()) / ".env"
        self._data: Dict[str, str] = {}
        if autoload:
            self.reload()

    # ------------------------------------------------------------------ #
    # setup
    # ------------------------------------------------------------------ #
    def _project_root(self) -> str:
        # this file lives at project/core/services/EnvService.py
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "../", ".."))

    # ------------------------------------------------------------------ #
    # loading
    # ------------------------------------------------------------------ #
    def reload(self) -> "EnvService":
        """Re-parses the .env file from disk and pushes every value into os.environ."""
        self._data = self._parse_file(self.path)
        for key, value in self._data.items():
            os.environ[key] = value
        return self

    @staticmethod
    def _parse_file(path: Path) -> Dict[str, str]:
        data: Dict[str, str] = {}
        if not path.exists():
            return data
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _LINE_RE.match(line)
            if not match:
                continue
            key = match.group("key")
            value = EnvService._unquote(match.group("value").strip())
            data[key] = value
        return data

    @staticmethod
    def _unquote(value: str) -> str:
        # strip a matching pair of quotes; otherwise drop a trailing inline "# comment"
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
        if "#" in value:
            value = value.split("#", 1)[0].rstrip()
        return value

    # ------------------------------------------------------------------ #
    # reading
    # ------------------------------------------------------------------ #
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self._data.get(key)
        if value is None:
            return default
        return value.strip().lower() in TRUE_VALUES

    def get_int(self, key: str, default: Optional[int] = None) -> Optional[int]:
        value = self._data.get(key)
        if value is None or value == "":
            return default
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"EnvService: '{key}' is not a valid int: {value!r}")

    def get_float(self, key: str, default: Optional[float] = None) -> Optional[float]:
        value = self._data.get(key)
        if value is None or value == "":
            return default
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"EnvService: '{key}' is not a valid float: {value!r}")

    def get_list(self, key: str, default: Optional[List[str]] = None, separator: str = ",") -> List[str]:
        value = self._data.get(key)
        if value is None or value == "":
            return default if default is not None else []
        return [item.strip() for item in value.split(separator) if item.strip() != ""]

    def has(self, key: str) -> bool:
        return key in self._data

    def all(self) -> Dict[str, str]:
        return dict(self._data)

    def required(self, key: str) -> str:
        """Like get(), but raises if the key is missing or blank - use for must-have config."""
        value = self._data.get(key)
        if value is None or value == "":
            raise KeyError(f"EnvService: required env var '{key}' is not set")
        return value

    # ------------------------------------------------------------------ #
    # writing
    # ------------------------------------------------------------------ #
    def set(self, key: str, value: Union[str, int, float, bool], persist: bool = False) -> "EnvService":
        """
        Updates the value in memory and in os.environ. With persist=True, also
        rewrites the .env file on disk - replacing the existing KEY=value line
        if present, or appending a new one otherwise. Every other line
        (comments, blank lines, unrelated keys) is left exactly as-is.
        """
        str_value = self._to_env_string(value)
        self._data[key] = str_value
        os.environ[key] = str_value
        if persist:
            self._persist(key, str_value)
        return self

    @staticmethod
    def _to_env_string(value: Union[str, int, float, bool]) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _persist(self, key: str, value: str) -> None:
        line_value = self._quote_if_needed(value)
        new_line = f"{key}={line_value}"

        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(new_line + "\n", encoding="utf-8")
            return

        lines = self.path.read_text(encoding="utf-8").splitlines()
        updated = False
        for i, raw_line in enumerate(lines):
            match = _LINE_RE.match(raw_line.strip())
            if match and match.group("key") == key:
                lines[i] = new_line
                updated = True
                break
        if not updated:
            lines.append(new_line)

        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _quote_if_needed(value: str) -> str:
        if value == "" or re.search(r"""[\s#"']""", value):
            escaped = value.replace('"', '\\"')
            return f'"{escaped}"'
        return value
