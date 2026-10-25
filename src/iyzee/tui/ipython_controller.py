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
from typing import Any, NamedTuple, TextIO

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


_NO_INPUT = "input() is not available in the cell console; define a variable in the editor instead"


def _clear_pending_interrupt(thread_id: int) -> None:
    """Drop an interrupt that was delivered but not yet raised in ``thread_id``."""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), None)


class Completion(NamedTuple):
    """One completion: ``text`` replaces ``source[start:end]`` (it is not a suffix)."""

    start: int
    end: int
    text: str


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
        if threading.get_ident() != self._session._worker_id:
            self._default.flush()
        # Worker writes are already scheduled onto the UI loop by write().
        # Draining here would call Textual widgets directly from the worker.

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


class _ThreadStdin:
    """Make stdin unreadable from the execution worker; other threads see the real one.

    A worker blocked in a real read can never be interrupted and would steal
    keystrokes from Textual, so reads fail fast instead.
    """

    def __init__(self, default: TextIO, session: IPythonSession) -> None:
        self._default = default
        self._session = session

    def _on_worker(self) -> bool:
        return threading.get_ident() == self._session._worker_id

    def read(self, size: int = -1) -> str:
        if self._on_worker():
            raise RuntimeError(_NO_INPUT)
        return self._default.read(size)

    def readline(self, size: int = -1) -> str:
        if self._on_worker():
            raise RuntimeError(_NO_INPUT)
        return self._default.readline(size)

    def readlines(self, hint: int = -1) -> list[str]:
        if self._on_worker():
            raise RuntimeError(_NO_INPUT)
        return self._default.readlines(hint)

    def __iter__(self) -> _ThreadStdin:
        if self._on_worker():
            raise RuntimeError(_NO_INPUT)
        return self

    def __next__(self) -> str:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def isatty(self) -> bool:
        return False if self._on_worker() else bool(self._default.isatty())

    def fileno(self) -> int:
        if self._on_worker():
            raise RuntimeError(_NO_INPUT)
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
        # True only while the worker is inside run_cell. Interrupts are
        # delivered under this lock, so they can never land in the worker's
        # own bookkeeping (queue wait, busy notifications), where a stray
        # KeyboardInterrupt would kill the thread and leave the console busy.
        self._interrupt_lock = threading.Lock()
        self._in_cell = False
        self._started = False
        self._closed = False
        self._previous_shell = InteractiveShell._instance
        self._previous_input = builtins.input
        self._previous_streams: tuple[Any, Any, Any] | None = None

        self.shell = InteractiveShell(
            config=shell_config(history_file), user_ns=lab_namespace(app, namespace)
        )
        InteractiveShell._instance = self.shell  # type: ignore[assignment]

    @property
    def executing(self) -> bool:
        return not self._idle.is_set()

    def start(self) -> None:
        if self._started or self._closed:
            return
        self._started = True
        stdin, stdout, stderr = sys.stdin, sys.stdout, sys.stderr
        self._previous_streams = (stdin, stdout, stderr)
        sys.stdin = _ThreadStdin(stdin, self)  # type: ignore[assignment]
        sys.stdout = _ThreadStream(stdout, self)  # type: ignore[assignment]
        sys.stderr = _ThreadStream(stderr, self)  # type: ignore[assignment]
        builtins.input = self._input  # type: ignore[assignment]
        self._thread = threading.Thread(target=self._run, name="iyzee-ipython", daemon=True)
        self._thread.start()

    def submit(self, source: str) -> bool:
        """Queue one cell; returns False while another cell is running."""
        if not source.strip() or self._closed or not self._started:
            return False
        if self._thread is None or not self._thread.is_alive():
            return False
        with self._state_lock:
            if self._closed or not self._idle.is_set():
                return False
            self._idle.clear()
            self._notify_busy(True)
            self._jobs.put(source)
        return True

    def complete(self, source: str, cursor_pos: int | None = None) -> list[Completion]:
        """Return IPython's completions for the text at the cursor."""
        if self.executing or self._closed:
            return []
        with provisionalcompleter():
            return [
                Completion(item.start, item.end, item.text)
                for item in self.shell.Completer.completions(
                    source, len(source) if cursor_pos is None else cursor_pos
                )
            ]

    def history(self) -> list[str]:
        """Return recent raw cells from IPython's configured history database."""
        if self.executing or self._closed:
            return []
        manager = self.shell.history_manager
        if manager is None:
            return []
        rows = manager.get_tail(n=1000, raw=True, include_latest=True)
        return [source for _session, _line, source in rows if source.strip()]

    def interrupt(self) -> bool:
        """Interrupt the cell being run; False if no cell is running right now."""
        with self._interrupt_lock:
            thread_id = self._worker_id
            thread = self._thread
            if not self._in_cell or thread_id is None or thread is None or not thread.is_alive():
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
                    self._run_cell(source)
                finally:
                    self._idle.set()
                    self._notify_busy(False)
        finally:
            self._idle.set()
            self._notify_busy(False)
            self._worker_id = None
            self._restore_globals()

    def _run_cell(self, source: str) -> None:
        try:
            with self._interrupt_lock:
                self._in_cell = True
            self.shell.run_cell(source, store_history=True, silent=False)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._write_output(f"{type(exc).__name__}: {exc}\n")
        finally:
            self._leave_cell()

    def _leave_cell(self) -> None:
        # An interrupt sent just before the cell ended may still be pending or
        # may arrive right here; swallow it and retry until the flag is down.
        while True:
            try:
                with self._interrupt_lock:
                    self._in_cell = False
                    _clear_pending_interrupt(threading.get_ident())
                return
            except KeyboardInterrupt:
                continue

    def _input(self, prompt: str = "") -> str:
        if threading.get_ident() == self._worker_id:
            raise RuntimeError(_NO_INPUT)
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
            if isinstance(sys.stdin, _ThreadStdin) and sys.stdin._session is self:
                sys.stdin = previous[0]
            if isinstance(sys.stdout, _ThreadStream) and sys.stdout._session is self:
                sys.stdout = previous[1]
            if isinstance(sys.stderr, _ThreadStream) and sys.stderr._session is self:
                sys.stderr = previous[2]
            if builtins.input == self._input:
                builtins.input = self._previous_input
            self._previous_streams = None
        if InteractiveShell._instance is self.shell:
            InteractiveShell._instance = self._previous_shell
