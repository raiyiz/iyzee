"""Background-thread plumbing shared by the TUI screens.

Every device call in this codebase is blocking I/O (PyVISA sockets, raw
TCP for the scope, ``urllib`` for the wavemeter). None of it may run on
Textual's UI event loop — a single sweep step can take from milliseconds
to seconds, and doing that on the main thread would freeze the whole app
for the duration of the run.

The pattern used throughout the TUI is: a screen method decorated with
``@work(thread=True)`` does the blocking work and calls back into the UI
only via ``self.app.call_from_thread(...)``, which is the only
thread-safe way to touch widgets from a worker thread. These dataclasses
are the messages that cross that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..experiment import Step, StepResult


@dataclass(frozen=True)
class ConnectOutcome:
    """Result of attempting to connect (or disconnect) one instrument."""

    key: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class StepProgress:
    """One ``on_step`` callback from ``run_sequence``, made UI-friendly.

    Mirrors ``iyzee.experiment.runner.StepCallback`` but as a plain value
    that's easy to pass through ``call_from_thread`` and easy to test.
    """

    index: int
    total: int
    step: Step
    result: StepResult | None
    error: BaseException | None

    @property
    def label(self) -> str:
        return getattr(self.step, "label", None) or f"step[{self.index}]"

    @property
    def ok(self) -> bool:
        return self.error is None
