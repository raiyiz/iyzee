"""Evidence for the day the process dies without a word.

A full-screen app that vanishes leaves nothing to read: the terminal is restored, the log
file stops mid-sentence, and the shell may not say anything either. Three things are done
here so the next time there is something to look at:

* :func:`install` makes uncaught exceptions (main thread or any other) land in the log,
  and turns on ``faulthandler`` so a hard crash (segfault, abort) writes every thread's
  stack to ``faulthandler.log``.
* :class:`SessionMarker` keeps one small file per running session and removes it on a clean
  exit. A marker whose process is gone is proof that session did **not** end cleanly:
  killed (out of memory, ``kill -9``), crashed hard, or its terminal vanished. The next
  start says so, and says what the session was doing.

``install`` is process-wide and is called from the real entry point only (see ``app.run``),
never from the constructor, so tests and embedding code are not affected.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger("iyzee.crash")

# faulthandler writes to the file descriptor at crash time, so the file must stay open for
# the life of the process.
_faulthandler_file: TextIO | None = None


def install(log_dir: Path) -> None:
    """Log uncaught exceptions and dump all thread stacks on a hard crash."""
    global _faulthandler_file
    log_dir.mkdir(parents=True, exist_ok=True)
    _faulthandler_file = open(log_dir / "faulthandler.log", "a", buffering=1)  # noqa: SIM115
    _faulthandler_file.write(f"\n=== iyzee pid {os.getpid()} started {_now()} ===\n")
    faulthandler.enable(file=_faulthandler_file, all_threads=True)

    previous_excepthook = sys.excepthook

    def excepthook(exc_type: Any, exc: Any, tb: Any) -> None:
        log.critical("uncaught exception, the process is ending", exc_info=(exc_type, exc, tb))
        previous_excepthook(exc_type, exc, tb)

    sys.excepthook = excepthook

    previous_thread_hook = threading.excepthook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        exc_info = (
            (args.exc_type, args.exc_value, args.exc_traceback)
            if args.exc_value is not None
            else None
        )
        log.critical("uncaught exception in thread %r", name, exc_info=exc_info)
        previous_thread_hook(args)

    threading.excepthook = thread_hook


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _alive(pid: object) -> bool:
    """Whether process ``pid`` exists. Errs towards "alive": a false alarm is worse than a miss."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        return True  # os.kill(pid, 0) would *terminate* the process there
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SessionMarker:
    """``session-<pid>.json`` in ``directory``: present exactly while this session runs."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / f"session-{os.getpid()}.json"
        self._started = _now()

    def start(self) -> list[dict[str, Any]]:
        """Claim this session and return what is known about sessions that never ended.

        Markers of processes that are still running (another instance) are left alone; those
        of dead processes are removed, so each unclean end is reported exactly once.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        unclean: list[dict[str, Any]] = []
        for other in sorted(self.directory.glob("session-*.json")):
            if other == self.path:
                continue
            info = self._read(other)
            if _alive(info.get("pid")):
                continue
            unclean.append(info)
            other.unlink(missing_ok=True)
        self.note("idle")
        return unclean

    def note(self, activity: str) -> None:
        """Record what the session is doing now (what a crash report will say it was doing)."""
        record = {
            "pid": os.getpid(),
            "started": self._started,
            "activity": activity,
            "noted": _now(),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record))
        temporary.replace(self.path)

    def stop(self) -> None:
        """A clean exit: remove the marker."""
        self.path.unlink(missing_ok=True)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text())
        except OSError, ValueError:
            return {}
        return data if isinstance(data, dict) else {}


def describe(info: dict[str, Any]) -> str:
    """One line about a session that ended unexpectedly."""
    if not info:
        return "no details were recorded"
    return (
        f"started {info.get('started', '?')}, pid {info.get('pid', '?')}; "
        f"last known activity: {info.get('activity', '?')} (as of {info.get('noted', '?')})"
    )
