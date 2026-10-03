"""Per-user TUI preferences: one small JSON file in the user config directory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

log = logging.getLogger("iyzee.tui")


def default_prefs_file() -> Path:
    """Where preferences live for real runs (``iyzee.tui.app.run``); tests pass no file."""
    return Path(user_config_dir("iyzee")) / "tui.json"


class Prefs:
    """Key/value preferences, saved on every ``set``. With no path they stay in memory."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._data: dict[str, Any] = {}
        if path is not None:
            try:
                loaded = json.loads(path.read_text())
            except OSError, ValueError:
                loaded = None  # missing or unreadable: start from defaults
            if isinstance(loaded, dict):
                self._data = loaded

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2))
            tmp.replace(self._path)
        except OSError:
            log.warning("could not save preferences to %s", self._path, exc_info=True)
