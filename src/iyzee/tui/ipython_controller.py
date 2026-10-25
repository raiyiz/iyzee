"""Small, non-terminal IPython execution controller for the Textual console."""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import ctypes
import queue
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from IPython.core.completer import provisionalcompleter
from IPython.core.interactiveshell import InteractiveShell

from .ipython import AppState, lab_namespace, shell_config


def _raise_in_thread(thread_id: int) -> None:
    """Best-effort interrupt for a running Python cell (CPython only)."""
    result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread_id), ctypes.py_object(KeyboardInterrupt)
    )
    if result > 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), None)


class _ThreadStream:
    """Route writes from the execution worker to the console, others to the host."""

    def __init__(self, default: TextIO, session: IPythonSession) -> None:
        self._default = default
        self._session = session

    def write(self, text: str) -> int:
        if threading.get_ident() == self._session._worker_id:
            self._session._write_output(text)
            return len(text)
        return self._default.write(text)

    def flush(self) -> None:
        if threading.get_ident() == self._session._worker_id:
            self._session._flush_output()
        else:
            self._default.flush()

    def isatty(self) -> bool:
        if threading.get_ident() == self._session._worker_id:
            return False
        return bool(self._default.isatty())

    def fileno(self) -> int:
        if threading.get_ident() == self._session._worker_id:
            raise OSError("the embedded console has no file descriptor")
        return self._default.fileno()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._default, name)


class IPythonSession:
    """Persistent IPython shell whose output is delivered as ordinary text."""

    def __init__(
        self,
        app: AppState,
        *,
        namespace: Mapping[str, Any] | None = None,
        history_file: str | Path | None = None,
        on_output: Callable[[str], object] | None = None,
        on_busy: Callable[[bool], None] | None = None,
    ) -> None:
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        self._on_output = on_output
        self._on_busy = on_busy
        self._output_lock = threading.Lock()
        self._output_chunks: list[str] = []
        self._output_scheduled = False
        self._jobs: queue.Queue[str | None] = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self._state_lock = threading.Lock()
        self._worker_id: int | None = None
        self._thread: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._previous_shell = InteractiveShell._instance
        self._previous_input = builtins.input
        self._previous_streams: tuple[Any, Any] | None = None

        self.shell = InteractiveShell(
            config=shell_config(history_file), user_ns=lab_namespace(app, namespace)
        )
        InteractiveShell._instance = self.shell

    @property
    def executing(self) -> bool:
        return not self._idle.is_set()

    def start(self) -> None:
        if self._started or self._closed:
            return
        self._started = True
        stdout, stderr = sys.stdout, sys.stderr
        self._previous_streams = (stdout, stderr)
        sys.stdout = _ThreadStream(stdout, self)  # type: ignore[assignment]
        sys.stderr = _ThreadStream(stderr, self)  # type: ignore[assignment]
        builtins.input = self._input  # type: ignore[assignment]
        self._thread = threading.Thread(
            target=self._run, name="iyzee-ipython", daemon=True
        )
        self._thread.start()

    def submit(self, source: str) -> bool:
        """Queue one cell; returns False while another cell is running."""
        if not source.strip() or self._closed or not self._started:
            return False
        with self._state_lock:
            if self._closed or not self._idle.is_set():
                return False
            self._idle.clear()
            self._notify_busy(True)
            self._jobs.put(source)
        return True

    def complete(self, source: str, cursor_pos: int | None = None) -> list[str]:
        """Return IPython's insertion suffixes for the text at the cursor."""
        if self.executing or self._closed:
            return []
        with provisionalcompleter():
            return [
                item.text
                for item in self.shell.Completer.completions(
                    source, len(source) if cursor_pos is None else cursor_pos
                )
            ]

    def history(self) -> list[str]:
        """Return recent raw cells from IPython's configured history database."""
        if self.executing or self._closed:
            return []
        rows = self.shell.history_manager.get_tail(n=1000, raw=True, include_latest=True)
        return [source for _session, _line, source in rows if source.strip()]

    def interrupt(self) -> bool:
        """Interrupt the active cell if it has a live worker thread."""
        thread_id = self._worker_id
        thread = self._thread
        if (
            not self.executing
            or thread_id is None
            or thread is None
            or not thread.is_alive()
        ):
            return False
        _raise_in_thread(thread_id)
        return True

    def close(self, timeout: float = 3.0) -> bool:
        """Stop the worker. False means user code did not stop within the timeout."""
        if self._closed:
            return self._thread is None or not self._thread.is_alive()
        self._closed = True
        self.interrupt()
        if self._started:
            self._jobs.put(None)
            thread = self._thread
            if thread is not None:
                thread.join(timeout)
                return not thread.is_alive()
        self._restore_globals()
        return True

    def _run(self) -> None:
        self._worker_id = threading.get_ident()
        try:
            while True:
                source = self._jobs.get()
                if source is None:
                    break
                try:
                    self.shell.run_cell(source, store_history=True, silent=False)
                except (KeyboardInterrupt, SystemExit) as exc:
                    self._write_output(f"{type(exc).__name__}: {exc}\n")
                finally:
                    self._idle.set()
                    self._notify_busy(False)
        finally:
            self._worker_id = None
            self._restore_globals()

    def _input(self, prompt: str = "") -> str:
        if threading.get_ident() == self._worker_id:
            raise RuntimeError(
                "input() is not available in the cell console; "
                "define a variable in the editor instead"
            )
        return self._previous_input(prompt)

    def _write_output(self, text: str) -> None:
        if not text or self._on_output is None:
            return
        with self._output_lock:
            self._output_chunks.append(text)
            if self._output_scheduled:
                return
            self._output_scheduled = True
        if self._loop is None:
            self._flush_output()
        else:
            try:
                self._loop.call_soon_threadsafe(self._flush_output)
            except RuntimeError:
                with self._output_lock:
                    self._output_chunks.clear()
                    self._output_scheduled = False

    def _flush_output(self) -> None:
        with self._output_lock:
            text = "".join(self._output_chunks)
            self._output_chunks.clear()
            self._output_scheduled = False
        if text and self._on_output is not None:
            self._on_output(text)

    def _notify_busy(self, busy: bool) -> None:
        if self._on_busy is None:
            return
        if self._loop is None:
            self._on_busy(busy)
        else:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._on_busy, busy)

    def _restore_globals(self) -> None:
        previous = self._previous_streams
        if previous is not None:
            if isinstance(sys.stdout, _ThreadStream) and sys.stdout._session is self:
                sys.stdout = previous[0]
            if isinstance(sys.stderr, _ThreadStream) and sys.stderr._session is self:
                sys.stderr = previous[1]
            if builtins.input == self._input:
                builtins.input = self._previous_input
            self._previous_streams = None
        if InteractiveShell._instance is self.shell:
            InteractiveShell._instance = self._previous_shell
