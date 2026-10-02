"""Core abstractions and the runner for composable, hardware-driving
experiment steps.

``Step``/``StepResult``/``ExperimentContext`` describe one reproducible
measurement point; ``run_sequence`` runs a list of them against a shared,
already-connected context. See ``experiment/procedures.py`` for concrete
steps built from these, and ``experiment/io.py`` for saving/plotting
results.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


def utc_now() -> str:
    """The current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass
class StepResult:
    """The outcome of running one experiment step.

    ``traces`` holds named acquisitions (e.g. ``"squeezing"``, ``"shot_noise"``)
    so a step can capture more than the historical squeezing/shot-noise pair
    without changing this schema. ``meta`` carries whatever instrument state
    or context is relevant to reproducing this specific point (e.g. RBW/VBW,
    averaging count, laser setpoint) and is what makes a saved measurement
    self-describing rather than a bare array of numbers.
    """

    label: str
    x_value: float
    x_unit: str
    traces: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentContext:
    """Already-connected hardware handles and run-level metadata shared by
    every step in a sequence.

    A context is built once per run and passed to each step's ``run()``;
    steps do not open or close connections themselves — that stays the
    responsibility of the procedure that builds the context, the same way
    ``record_bw_seq``/``record_freq_seq`` used to own connect/disconnect.
    """

    mx: Any
    run_id: str
    shutter: Any | None = None
    wavemeter: Any | None = None
    config: dict[str, Any] = field(default_factory=dict)


class Step(Protocol):
    """A single reproducible measurement point in an experiment sequence."""

    def run(self, ctx: ExperimentContext) -> StepResult: ...


log = logging.getLogger("iyzee.experiment")


def step_label(step: object, index: int) -> str:
    """A step's display name, falling back to its position."""
    return getattr(step, "label", None) or f"step[{index}]"


@dataclass(frozen=True)
class StepFailure:
    """One step that raised: which one, and what went wrong."""

    index: int
    label: str
    error_type: str
    error: str


@dataclass
class RunRecord:
    """How one run ended, saved next to its data.

    Without this a saved sweep cannot say whether it is complete: a point that
    failed simply is not there, and a run that was aborted (or whose process
    died) looks like a short, finished one. The file is first written with
    ``status == "running"`` and rewritten with the outcome at the end, so a
    file still saying ``running`` means the run never finished.

    Statuses: ``running``, ``completed``, ``completed_with_errors`` (some steps
    failed), ``aborted`` (stopped by the user or by shutdown), ``failed``
    (setup or an unexpected error stopped the run).
    """

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    config: dict[str, Any] = field(default_factory=dict)
    started_at_utc: str = field(default_factory=utc_now)
    finished_at_utc: str | None = None
    status: str = "running"
    failed_steps: list[StepFailure] = field(default_factory=list)

    def on_step(
        self,
        index: int,
        total: int,
        step: object,
        result: StepResult | None,
        error: BaseException | None,
    ) -> None:
        """A :data:`StepCallback`: note any step that failed."""
        if error is not None:
            self.failed_steps.append(
                StepFailure(index, step_label(step, index), type(error).__name__, str(error))
            )

    def finish(self, *, aborted: bool = False, failed: bool = False) -> None:
        """Fix the final status (aborted wins over failed, failed over the rest)."""
        if aborted:
            self.status = "aborted"
        elif failed:
            self.status = "failed"
        else:
            self.status = "completed_with_errors" if self.failed_steps else "completed"
        self.finished_at_utc = utc_now()

    def as_metadata(self) -> dict[str, Any]:
        """Plain-JSON form, for ``save_step_results(run_metadata=...)``."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "config": dict(self.config),
            "failed_steps": [asdict(failure) for failure in self.failed_steps],
        }


# Called after each step is attempted, whether it succeeded or failed:
# on_step(index, total, step, result, error). Exactly one of
# ``result``/``error`` is not None. This is the seam a UI (or any other
# observer) hooks into for live progress; ``run_sequence`` itself stays
# UI-agnostic and keeps working with no callback at all.
StepCallback = Callable[[int, int, Step, "StepResult | None", "BaseException | None"], None]


def run_sequence(
    steps: Sequence[Step],
    ctx: ExperimentContext,
    *,
    on_error: str = "raise",
    on_step: StepCallback | None = None,
) -> list[StepResult]:
    """Run ``steps`` in order against ``ctx``, returning their results.

    ``on_error`` controls what happens when a step raises:

    - ``"raise"`` (default): propagate immediately. This matches the
      historical behavior of the hand-written procedures, where a failed
      point aborted the whole scan.
    - ``"skip"``: log the failure and continue with the remaining steps.
      Useful for long unattended scans where losing one point is
      preferable to losing the rest of the run.

    ``on_step``, if given, is called after every step is attempted (see
    :data:`StepCallback`). It is the hook a caller uses for live progress
    reporting (a progress bar, a live-updating trace plot, ...) without
    ``run_sequence`` needing to know anything about how that progress is
    displayed. Any exception raised *inside* ``on_step`` itself propagates
    immediately, regardless of ``on_error``.

    Connection lifecycle (connect/disconnect) is not this function's
    responsibility; ``ctx`` is expected to already be connected, and the
    caller is expected to tear it down (typically in a ``finally`` block
    around this call), the same way the previous hand-written procedures did.
    """
    if on_error not in ("raise", "skip"):
        raise ValueError(f"on_error must be 'raise' or 'skip', got {on_error!r}")

    total = len(steps)
    results: list[StepResult] = []
    for index, step in enumerate(steps):
        step_name = step_label(step, index)
        log.info("run %s: starting %s", ctx.run_id, step_name)
        try:
            result = step.run(ctx)
        except Exception as exc:
            log.exception("run %s: %s failed", ctx.run_id, step_name)
            if on_step is not None:
                on_step(index, total, step, None, exc)
            if on_error == "raise":
                raise
            continue
        log.info("run %s: finished %s", ctx.run_id, step_name)
        results.append(result)
        if on_step is not None:
            on_step(index, total, step, result, None)
    return results
