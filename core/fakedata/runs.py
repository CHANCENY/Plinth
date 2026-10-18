"""
runs
------
A small log of what fake-data runs created, so a run can be undone later (`console.py fake:undo`, or the Undo button
in the dashboard): for each run, which rows were inserted into which tables.

It's a plain JSON file, storage/fakedata/runs.json - development bookkeeping, deliberately not a database table, so
it never needs a migration and can be deleted any time (you just lose the ability to undo old runs). Only the most
recent MAX_RUNS runs are kept.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "storage" / "fakedata" / "runs.json"
MAX_RUNS = 50


class RunLog:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_PATH

    def _read(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _write(self, runs: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(runs, stream, indent=1)
            os.replace(temp, self.path)  # atomic: a crash never leaves a half-written log
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise

    def add(self, run: Dict[str, Any]) -> None:
        runs = self._read()
        runs.append(run)
        self._write(runs[-MAX_RUNS:])

    def all(self) -> List[Dict[str, Any]]:
        """Newest first."""
        return list(reversed(self._read()))

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        return next((run for run in self._read() if run.get("id") == run_id), None)

    def remove(self, run_id: str) -> None:
        self._write([run for run in self._read() if run.get("id") != run_id])
