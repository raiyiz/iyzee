"""Embedded IPython execution for the iyzee TUI."""

from __future__ import annotations

import contextlib
import io
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from IPython.core.interactiveshell import InteractiveShell
from traitlets.config import Config


@dataclass(frozen=True, slots=True)
class ExecutionOutput:
    """Captured result of one IPython cell execution."""

    source: str
    stdout: str
    stderr: str
    execution_count: int
    success: bool


class IyzeeIPython:
    """Own one embedded :class:`InteractiveShell` for the running TUI."""

    def __init__(self, namespace: Mapping[str, Any] | None = None) -> None:
        config = Config()
        config.InteractiveShell.automagic = True
        config.InteractiveShell.autoawait = True
        config.InteractiveShell.autoindent = True
        config.InteractiveShell.display_page = True
        config.InteractiveShell.history_load_length = 0
        config.InteractiveShell.log_output = False
        config.InteractiveShell.banner1 = ""
        config.InteractiveShell.banner2 = ""
        self.shell = InteractiveShell(config=config, user_ns=dict(namespace or {}))
        self.shell.init_completer()
        self._lock = threading.RLock()

    def update_namespace(self, namespace: Mapping[str, Any]) -> None:
        """Refresh application-owned names without deleting user variables."""
        with self._lock:
            self.shell.user_ns.update(namespace)

    def execute(self, source: str) -> ExecutionOutput:
        """Execute one cell and capture terminal-oriented output."""
        source = source.rstrip()
        if not source.strip():
            return ExecutionOutput(
                source="", stdout="", stderr="",
                execution_count=self.shell.execution_count, success=True,
            )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with self._lock, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.shell.run_cell(source, store_history=True, silent=False)
        return ExecutionOutput(
            source=source,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            execution_count=self.shell.execution_count,
            success=result.error_in_exec is None and result.error_before_exec is None,
        )

    def complete(self, source: str, cursor_pos: int) -> tuple[str, list[str]]:
        """Return IPython's completion edit and candidate list."""
        with self._lock:
            return self.shell.complete(source, line=source, cursor_pos=cursor_pos)

    def inspect(self, source: str, cursor_pos: int, detail_level: int = 0) -> dict[str, Any]:
        """Return IPython's object inspection payload."""
        with self._lock:
            return self.shell.object_inspect(source, cursor_pos, detail_level=detail_level)

    @property
    def history(self) -> list[str]:
        """Return the current session's input history."""
        with self._lock:
            return list(self.shell.user_ns.get("In", []))[1:]

    def close(self) -> None:
        """Run IPython's shutdown hooks."""
        with self._lock:
            self.shell.atexit_operations()


def namespace_from_handles(handles: Mapping[str, Any]) -> dict[str, Any]:
    """Build the live-device namespace exposed to the console."""
    namespace: dict[str, Any] = {}
    mxa = handles.get("mxa")
    if mxa is not None:
        namespace["mx"] = getattr(mxa, "device", mxa)
    shutter = handles.get("shutter")
    if shutter is not None:
        live_shutter = getattr(shutter, "shutter", None)
        namespace["shutter"] = live_shutter if live_shutter is not None else shutter
    scope = handles.get("scope")
    if scope is not None:
        namespace["scope"] = getattr(scope, "scope", scope)
    namespace["handles"] = handles
    return namespace
