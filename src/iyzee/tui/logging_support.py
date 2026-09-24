"""Capturing the app's own logging: a bounded in-memory buffer for the
current session, and a rotating file for history across sessions.

There is no logging configuration anywhere else in this application —
every ``log.exception``/``log.warning``/``log.info`` call across
``app.py``, the screens, and ``experiment.core`` has always gone through
the standard library's ``logging`` module, but until :func:`install` runs,
nothing ever attaches a handler to catch any of it: Python's own "handler
of last resort" would print WARNING+ straight to stderr, which is either
invisible or actively corrupts a full-screen Textual app's display. So
this module isn't new logging calls — it's a sink for logging that
already exists.
"""

from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.markup import escape

# A sibling of experiment.io.DATA_ROOT, not inside it: log files are
# operational/debugging material, not measurement data, and mixing them
# into data/ would make TracesScreen's **/*.npz glob — and a human
# skimming that folder for actual runs — work harder for no reason.
LOG_ROOT = Path(__file__).resolve().parents[3] / "logs"

# The shared parent of every logger this application actually uses today
# ("iyzee.tui", "iyzee.experiment") — attaching here, once, catches both
# via ordinary propagation, plus anything logged under "iyzee.*" in the
# future, without each module needing to know this handler exists.
_LOGGER_NAME = "iyzee"

_LEVEL_STYLE = {
    logging.DEBUG: "dim",
    logging.INFO: "",
    logging.WARNING: "yellow",
    logging.ERROR: "red",
    logging.CRITICAL: "bold red",
}


@dataclass(frozen=True)
class LogEntry:
    """One captured record, already formatted for display.

    Formatted once, here, rather than re-formatted by every reader (the
    live view, a level filter, a future search) — rendering ``exc_info``
    into a traceback string is the expensive part, and it only needs to
    happen once per record either way.
    """

    when: datetime
    level: int
    level_name: str
    logger_name: str
    message: str

    @classmethod
    def from_record(cls, record: logging.LogRecord, formatted: str) -> LogEntry:
        return cls(
            when=datetime.fromtimestamp(record.created).astimezone(),
            level=record.levelno,
            level_name=record.levelname,
            logger_name=record.name,
            message=formatted,
        )

    def rich_markup(self) -> str:
        """One markup-ready line: ``HH:MM:SS LEVEL logger: message``, with
        an exception's traceback (already folded into ``message`` by the
        standard library's own ``Formatter``) on indented lines after it.
        """
        style = _LEVEL_STYLE.get(self.level, "")
        head = f"{self.when:%H:%M:%S} {self.level_name:<8} {escape(self.logger_name)}"
        if style:
            head = f"[{style}]{head}[/{style}]"
        first_line, _, rest = escape(self.message).partition("\n")
        line = f"{head}  {first_line}"
        if rest:
            line += "\n" + rest
        return line


class TuiLogHandler(logging.Handler):
    """Captures every record into a bounded in-memory buffer, and calls
    back so a currently-mounted ``LogScreen`` can show it live.

    ``emit()`` can run on any thread — every worker across this app logs
    from a background thread (a driver timeout, a failed checkpoint, ...).
    The buffer is a ``deque(maxlen=...)``: CPython's append/eviction on a
    bounded deque is atomic, so it needs no lock of its own. Only
    ``on_record`` (supplied by the app) is responsible for getting back
    onto the app's own thread before touching any widget — the same
    ``call_from_thread`` pattern each screen's own worker->UI callbacks
    already use (see e.g. ``ScopeScreen._ui``).
    """

    def __init__(self, on_record: Callable[[LogEntry], None], maxlen: int = 2000) -> None:
        super().__init__()
        self._on_record = on_record
        self.records: deque[LogEntry] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = LogEntry.from_record(record, self.format(record))
        except Exception:
            self.handleError(record)
            return
        self.records.append(entry)
        try:
            self._on_record(entry)
        except Exception:
            pass  # no app/event loop yet, or it's shutting down — buffer still has it


def install(on_record: Callable[[LogEntry], None]) -> TuiLogHandler:
    """Attach the capturing handler (session buffer + live callback) and a
    rotating file handler (history across restarts) to the shared
    ``"iyzee"`` logger.

    Call once, as early as possible in ``IyzeeApp`` startup, so no early
    failure is lost before the sink exists. Idempotent rather than
    additive: every previous handler *this module* installed is removed
    first, so calling it again (every test in this codebase constructs its
    own ``IyzeeApp()``, in the same process) replaces rather than stacks —
    stacking would leak duplicate log lines and duplicate file writes
    across otherwise-unrelated app instances. Removing the old file
    handler first also means a ``LOG_ROOT`` the caller changed since the
    last call (as tests do, via ``monkeypatch`` — see ``conftest.py``)
    takes effect immediately, rather than the first call's path sticking
    around forever.

    File retention: 2 MiB x 5 backups (10 MiB total) under ``LOG_ROOT`` —
    bounded rather than kept forever, but generous for a lab session;
    adjust here if that tradeoff is ever wrong.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)

    for existing in list(logger.handlers):
        if getattr(existing, "_iyzee_owned", False):
            logger.removeHandler(existing)
            existing.close()

    handler = TuiLogHandler(on_record)
    handler._iyzee_owned = True
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_ROOT / "iyzee.log", maxBytes=2 * 1024 * 1024, backupCount=5
    )
    file_handler._iyzee_owned = True
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logger.addHandler(file_handler)

    return handler
