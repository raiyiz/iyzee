"""``@traced``: say where a call stalled or failed, for notebooks and scripts.

    from iyzee.trace import traced

    @traced(stall_s=20)
    def run(): ...          # or:  with traced() as t: ...

While the call runs it records the library's own log lines (each sweep stage is one:
"run ab12: acquiring the squeezing trace"). Then:

* if it raises (an interrupt included), the exception gets a note with the last events and how
  far into the call it failed, so any traceback display shows what the run was doing;
* if it is quiet for ``stall_s`` seconds, the stack of every thread is printed, which names
  the line that is blocked (a VISA read, an HTTP request, a lock).

``timeline()`` returns the recorded events at any time, e.g. to show after a run.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from collections import deque
from contextlib import ContextDecorator
from types import TracebackType


class traced(logging.Handler, ContextDecorator):
    def __init__(self, stall_s: float = 30.0, keep: int = 60, logger: str = "iyzee") -> None:
        super().__init__(logging.INFO)
        self.stall_s, self.logger = stall_s, logging.getLogger(logger)
        self.events: deque[tuple[float, str]] = deque(maxlen=keep)
        self._t0 = self._last = time.monotonic()
        self._stop = threading.Event()

    # -- recording ------------------------------------------------------------------------------

    def _say(self, text: str) -> None:
        now = time.monotonic()
        self._last = now
        self.events.append((now - self._t0, text))

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += f"  [{type(record.exc_info[1]).__name__}: {record.exc_info[1]}]"
        self._say(text)
        if record.levelno >= logging.WARNING:  # what the default handler would have shown
            sys.stderr.write(f"{record.levelname}: {text}\n")

    def timeline(self) -> str:
        lines = [f"  +{when:7.1f}s  {text}" for when, text in self.events]
        return "iyzee trace, last events of the call:\n" + "\n".join(lines)

    # -- the watchdog ---------------------------------------------------------------------------

    def _watch(self) -> None:
        while not self._stop.wait(min(1.0, self.stall_s / 2)):
            quiet = time.monotonic() - self._last
            if quiet < self.stall_s:
                continue
            names = {t.ident: t.name for t in threading.enumerate()}
            where = [
                f"--- {names.get(ident, ident)}\n{''.join(traceback.format_stack(frame)[-4:])}"
                for ident, frame in sys._current_frames().items()
                if ident != threading.get_ident()
            ]
            self._say(f"quiet for {quiet:.0f} s; where each thread is:\n" + "".join(where))
            sys.stderr.write(self.events[-1][1] + "\n")

    # -- context manager ------------------------------------------------------------------------

    def __enter__(self) -> traced:
        self.events.clear()
        self._t0 = self._last = time.monotonic()
        self._stop.clear()
        self._level = self.logger.level
        if self.logger.getEffectiveLevel() > logging.INFO:
            self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self)
        threading.Thread(target=self._watch, name="iyzee-trace", daemon=True).start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        self.logger.removeHandler(self)
        self.logger.setLevel(self._level)
        if exc is not None:
            exc.add_note(
                f"failed {time.monotonic() - self._t0:.1f} s into the call\n{self.timeline()}"
            )
