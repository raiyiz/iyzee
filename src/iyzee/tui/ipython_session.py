"""A real IPython terminal shell, running inside the iyzee process.

The goal is the whole IPython experience in the console page — IPython's own
prompt with its own editing (emacs or vi mode), completion menu, history
search, auto-suggestions, multi-line handling, ``%magics``, ``?`` help,
``%debug`` — rather than a hand-built imitation of it.

That UI is prompt_toolkit's, and it is a *terminal* application: it reads raw
keystrokes and writes VT100 escape sequences. It needs no *real* terminal,
though. This module gives it virtual ones:

* **input** is a prompt_toolkit pipe input; the console page writes the
  encoded keystrokes into it (see ``termkeys.py``);
* **output** is a VT100 writer aimed at a sink; the sink hands the bytes to
  the console page, which feeds them to a terminal emulator (``vterm.py``).

Everything else is IPython itself, in the same process as the app — so
``lab.mx`` is the live instrument, not a copy or a proxy over a socket. That
is the reason not to use a separate ``ipython`` subprocess (or a Jupyter
kernel plus console client), which would put the instruments on the other
side of a process boundary.

The shell runs in its own thread, since ``prompt()`` blocks. Consequences
handled here:

* ``print()`` inside a cell writes to ``sys.stdout``, which is process-wide;
  a thread-aware router sends the shell thread's output to the virtual
  terminal and everyone else's to whatever was there before (Textual's).
* ``input()`` would read the *real* stdin (owned by Textual); it is replaced
  with a prompt_toolkit prompt on the virtual terminal, for this thread only.
* Ctrl+C cannot be delivered as a signal to one thread, so it is delivered as
  an asynchronous ``KeyboardInterrupt`` (see ``_raise_in_thread``) while a cell
  runs, and as the ordinary keystroke while a prompt is showing.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import ctypes
import io
import logging
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO, cast

import IPython
from IPython.core.interactiveshell import InteractiveShell
from IPython.terminal.interactiveshell import TerminalInteractiveShell
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.output.vt100 import Vt100_Output

from .ipython import AppState, lab_namespace, shell_config

log = logging.getLogger("iyzee.tui")

_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def _raise_in_thread(thread_id: int, exc_type: type[BaseException]) -> None:
    """Asynchronously raise ``exc_type`` inside the thread ``thread_id``.

    There is no stdlib-blessed way to interrupt an arbitrary running
    thread — ordinary threads have no safe cancellation point — so this uses
    CPython's ``PyThreadState_SetAsyncExc``. The target only raises at its
    next bytecode boundary, so a call blocked entirely inside a C extension
    with no GIL release point (rare for this app's VISA/socket-based
    instrument calls, which do release it) may not stop immediately.
    """
    result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread_id), ctypes.py_object(exc_type)
    )
    if result > 1:
        # Pending-exception state landed on more than one thread (should
        # never happen with a single valid id) — undo rather than risk
        # corrupting an unrelated thread's state.
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), None)


class _Sink(io.TextIOBase):
    """Where the shell's terminal output goes.

    Written to from the shell's thread, read on the UI thread. Writes are
    batched: a burst of ``print()`` calls (or one prompt_toolkit redraw,
    which is many small writes) becomes one delivery per event-loop turn
    instead of one thread hop each.
    """

    encoding = "utf-8"

    def __init__(
        self, deliver: Callable[[str], object], loop: asyncio.AbstractEventLoop | None
    ) -> None:
        super().__init__()
        self._deliver = deliver
        self._loop = loop
        self._lock = threading.Lock()
        self._chunks: list[str] = []
        self._scheduled = False

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0
        with self._lock:
            self._chunks.append(s)
            if self._scheduled:
                return len(s)
            self._scheduled = True
        if self._loop is None:
            self._drain()
        else:
            try:
                self._loop.call_soon_threadsafe(self._drain)
            except RuntimeError:  # loop already closed: the app is going away
                with self._lock:
                    self._scheduled = False
        return len(s)

    def _drain(self) -> None:
        with self._lock:
            text = "".join(self._chunks)
            self._chunks.clear()
            self._scheduled = False
        if text:
            self._deliver(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise io.UnsupportedOperation("the console's terminal has no file descriptor")


class _StreamRouter:
    """A ``sys.stdout``/``sys.stderr`` replacement that routes by thread.

    ``sys.stdout`` is one object for the whole process, but only the shell's
    thread should write to the virtual terminal: the rest of the app (and
    Textual itself, which captures stdout) must keep seeing what was there.
    """

    def __init__(self, default: Any, shell_thread_id: Callable[[], int | None], sink: _Sink):
        self._default = default
        self._shell_thread_id = shell_thread_id
        self._sink = sink

    def _here(self) -> bool:
        return threading.get_ident() == self._shell_thread_id()

    def write(self, s: str) -> int:
        if self._here():
            return self._sink.write(s)
        return self._default.write(s)  # type: ignore[no-any-return]

    def flush(self) -> None:
        if not self._here():
            self._default.flush()

    def isatty(self) -> bool:
        return True if self._here() else bool(self._default.isatty())

    def fileno(self) -> int:
        if self._here():
            return self._sink.fileno()
        return self._default.fileno()  # type: ignore[no-any-return]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._default, name)


class IPythonSession:
    """One IPython terminal shell, wired to a virtual terminal.

    Construct and :meth:`start` on the UI thread (with a running asyncio
    loop). ``on_output`` is called *on the UI thread's loop* with batches of
    VT100 text to feed to a terminal emulator; :meth:`send` takes the bytes
    to type into the shell; :meth:`resize` tells the shell the terminal size.
    """

    def __init__(
        self,
        app: AppState,
        *,
        namespace: Mapping[str, Any] | None = None,
        history_file: str | Path | None = None,
        editing_mode: str = "vi",
        rows: int = 24,
        cols: int = 80,
        on_output: Callable[[str], object] | None = None,
        on_busy: Callable[[bool], None] | None = None,
    ) -> None:
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._on_busy = on_busy
        self._size = Size(rows=max(1, rows), columns=max(1, cols))
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._closing = False
        self._closed = False
        self._prompt_depth = 0  # >0 while a prompt_toolkit prompt is showing
        self._started_prompting = False
        self._original_input = builtins.input
        self._router_out: _StreamRouter | None = None
        self._router_err: _StreamRouter | None = None
        self._previous_streams: tuple[Any, Any] | None = None

        self._stack = contextlib.ExitStack()
        self._pipe = self._stack.enter_context(create_pipe_input())
        self._sink = _Sink(on_output or (lambda _text: None), self._loop)
        self._output = Vt100_Output(
            cast(TextIO, self._sink),
            lambda: self._size,
            term="xterm-256color",
            default_color_depth=ColorDepth.DEPTH_24_BIT,
            # The terminal emulator does not answer cursor-position requests,
            # so asking would make prompt_toolkit wait for a reply.
            enable_cpr=False,
        )

        config = shell_config(history_file)
        terminal = config.TerminalInteractiveShell
        # These are what make it a full-UI shell rather than the plain
        # `input()`-style fallback IPython picks when stdin is not a tty.
        terminal.simple_prompt = False
        terminal.editing_mode = editing_mode
        terminal.true_color = True
        terminal.colors = "linux"
        terminal.confirm_exit = False
        # modal_cursor makes IPython write raw cursor-shape escapes straight
        # to `sys.stdout` — from whichever thread changes vi mode, including
        # this one during construction, i.e. onto the *real* terminal Textual
        # owns — and monkeypatches prompt_toolkit's ViState class for the
        # whole process to do it. The prompt's [ins]/[nav] prefix already
        # shows the mode.
        terminal.modal_cursor = False
        # IPython would otherwise write a window-title escape sequence to the
        # *real* terminal Textual owns.
        terminal.term_title = False

        # The shell (and its PromptSession/Application) must be created while
        # the virtual input/output are the app-session defaults, or
        # prompt_toolkit would bind them to the real terminal.
        with create_app_session(input=self._pipe, output=self._output):
            self.shell = TerminalInteractiveShell(
                config=config, user_ns=lab_namespace(app, namespace)
            )
        # IPython's get_ipython() (used by the prompt's own key bindings,
        # page(), %pdoc, ...) reads InteractiveShell's class-level singleton
        # pointer, which a plain constructor never sets. Assigning it directly
        # (rather than InteractiveShell.instance(), which would silently
        # *reuse* an existing shell) activates this shell without weakening
        # the isolation between separate sessions.
        InteractiveShell._instance = self.shell  # type: ignore[assignment]

        self.shell.ask_exit = self._ask_exit  # type: ignore[method-assign]
        self._real_prompt_for_code = self.shell.prompt_for_code
        self.shell.prompt_for_code = self._prompt_for_code  # type: ignore[method-assign]
        # `?`/`??` route through a pager, which by default would spawn `less`
        # attached to the *real* terminal. Print into the virtual one instead.
        self.shell.set_hook("show_in_pager", self._show_in_pager_hook)

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        """Start the shell thread and route the shell's stdio to the terminal."""
        if self._thread is not None:
            return
        self._router_out = _StreamRouter(sys.stdout, lambda: self._thread_id, self._sink)
        self._router_err = _StreamRouter(sys.stderr, lambda: self._thread_id, self._sink)
        self._previous_streams = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = self._router_out, self._router_err  # type: ignore[assignment]
        builtins.input = self._input  # type: ignore[assignment]
        self._thread = threading.Thread(target=self._run, name="iyzee-ipython", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._thread_id = threading.get_ident()
        with create_app_session(input=self._pipe, output=self._output):
            try:
                self._welcome()
                self.shell.mainloop()
            except BaseException:  # noqa: BLE001 - the thread must never die silently
                log.exception("IPython session ended unexpectedly")

    def _welcome(self) -> None:
        mode = self.shell.editing_mode
        self._sink.write(
            f"{_BOLD}iyzee console{_RESET}{_DIM} · IPython {IPython.__version__} · "
            f"{mode} mode{_RESET}\n"
            f"{_DIM}lab.mx / lab.shutter / lab.scope · F1-F4 switch pages · "
            f"Ctrl+Q quits iyzee{_RESET}\n\n"
        )

    def close(self, timeout: float = 3.0) -> None:
        """Stop the shell and release its resources. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        self._closing = True
        thread = self._thread
        if thread is not None and thread.is_alive():
            if self._prompt_depth == 0 and self._started_prompting:
                # Mid-cell: interrupt it so the shell can reach its next prompt.
                if self._thread_id is not None:
                    _raise_in_thread(self._thread_id, KeyboardInterrupt)
            self._exit_prompt()
            thread.join(timeout)
        self._restore_streams()
        with contextlib.suppress(Exception):
            self._stack.close()
        # Deliberately not calling shell.atexit_operations() here: IPython
        # registers it with `atexit` itself (that is what closes the history
        # session), and it is not safe to run twice.

    def _restore_streams(self) -> None:
        if self._previous_streams is None:
            return
        # Only put things back if nobody has wrapped ours since; a later
        # session's router still forwards to ours, which now passes through.
        if sys.stdout is self._router_out:
            sys.stdout = self._previous_streams[0]
        if sys.stderr is self._router_err:
            sys.stderr = self._previous_streams[1]
        if builtins.input == self._input:
            builtins.input = self._original_input
        self._previous_streams = None

    def _exit_prompt(self) -> None:
        """Make a blocked ``prompt()`` return (as EOF) from another thread."""
        app = getattr(getattr(self.shell, "pt_app", None), "app", None)
        loop = getattr(app, "loop", None)
        if app is None or loop is None or not app.is_running:
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(lambda: app.is_running and app.exit(exception=EOFError()))

    # -- what the terminal sends us -----------------------------------------------

    def send(self, data: bytes) -> None:
        """Type ``data`` (already encoded as terminal input) into the shell."""
        if not self._closed:
            self._pipe.send_bytes(data)

    def resize(self, rows: int, cols: int) -> None:
        """Tell the shell the terminal is now ``rows`` x ``cols``."""
        self._size = Size(rows=max(1, rows), columns=max(1, cols))
        app = getattr(getattr(self.shell, "pt_app", None), "app", None)
        loop = getattr(app, "loop", None)
        if app is None or loop is None or not app.is_running:
            return  # no prompt showing: the next one reads the new size
        with contextlib.suppress(RuntimeError, AttributeError):
            loop.call_soon_threadsafe(app._on_resize)

    # -- state -------------------------------------------------------------------------

    @property
    def executing(self) -> bool:
        """True while a cell (not a prompt) is running."""
        return self._started_prompting and self._prompt_depth == 0 and not self._closed

    def interrupt(self) -> None:
        """Ctrl+C: stop the running cell, or clear the line at a prompt."""
        if self._closed:
            return
        if self._prompt_depth > 0 or not self._started_prompting:
            # At a prompt, Ctrl+C is just a keystroke prompt_toolkit handles
            # (clears the line). Before the first prompt there is nothing
            # to interrupt.
            if self._prompt_depth > 0:
                self.send(b"\x03")
            return
        if self._thread_id is not None:
            _raise_in_thread(self._thread_id, KeyboardInterrupt)

    def _set_busy(self, busy: bool) -> None:
        callback = self._on_busy
        if callback is None:
            return
        if self._loop is None:
            callback(busy)
        else:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(callback, busy)

    # -- hooks installed on the shell (all run on the shell's thread) ---------------

    def _prompt_for_code(self) -> str:
        self._started_prompting = True
        self._prompt_depth += 1
        self._set_busy(False)
        try:
            return str(self._real_prompt_for_code())
        finally:
            self._prompt_depth -= 1
            self._set_busy(True)

    def _ask_exit(self) -> None:
        if self._closing:
            self.shell.keep_running = False
            return
        print(f"{_DIM}This console stays open — F1-F4 switch pages, Ctrl+Q quits iyzee.{_RESET}")

    @staticmethod
    def _show_in_pager_hook(shell: Any, data: Any, start: int = 0, screen_lines: int = 0) -> None:
        # IPython calls hooks as hook(shell, ...), hence the leading `shell`.
        text = data.get("text/plain", "") if isinstance(data, dict) else str(data)
        print(text)

    def _input(self, prompt: object = "") -> str:
        """``input()`` for the shell's thread: a prompt on the virtual terminal.

        The builtin reads the real stdin, which Textual owns. Other threads
        keep the original builtin.
        """
        if threading.get_ident() != self._thread_id:
            return self._original_input(prompt)  # type: ignore[no-any-return,call-arg]
        from prompt_toolkit import prompt as pt_prompt

        self._prompt_depth += 1
        try:
            return str(pt_prompt(str(prompt)))
        finally:
            self._prompt_depth -= 1
