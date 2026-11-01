"""Run one sweep from start to finish, without any user interface.

The TUI's sweep page and the ``reproduce_frequency_sweep`` notebook both do exactly this, so it
lives here once. A run is: hold every required instrument's lock for the whole sweep, set the
analyzer up, then execute the steps, **saving everything collected so far after every point** (a
crash, a power cut or a quit part-way through then costs at most the step in flight), and finally
rewrite the file with how the run ended instead of leaving it saying "running".

Callers keep their own concerns through two hooks: ``on_point`` (show progress, draw a live plot)
and ``should_abort`` (a button, a shutdown request). A failing save never stops the measurement:
it is logged and reported once through ``on_save_error``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .core import ExperimentContext, RunRecord, Step, StepResult, run_sequence
from .io import create_dirs, save_step_results
from .procedures import TRACE_SHOT, TRACE_SQZ, Sweep, prepare_analyzer

log = logging.getLogger("iyzee.experiment")

PointCallback = Callable[[int, int, Step, StepResult | None, BaseException | None], None]


class SweepAborted(Exception):
    """Raised from the per-point hook to stop a run cleanly after the current point."""


@dataclass
class SweepOutcome:
    """How a run ended. ``record`` is ``None`` only when the analyzer could not be set up."""

    record: RunRecord | None
    collected: list[StepResult] = field(default_factory=list)
    save_path: Path | None = None
    aborted: bool = False
    failed: bool = False
    setup_error: Exception | None = None


class SweepRunner:
    """Executes one ``Sweep`` against already-connected devices."""

    def __init__(
        self,
        sweep: Sweep,
        devices: Mapping[str, Any],
        locks: Sequence[AbstractContextManager[Any]] = (),
        *,
        on_point: PointCallback | None = None,
        should_abort: Callable[[], bool] = lambda: False,
        on_save_error: Callable[[Exception], None] | None = None,
        trace_slots: tuple[int, int] = (TRACE_SQZ, TRACE_SHOT),
    ) -> None:
        self.sweep = sweep
        self.devices = devices
        self.locks = locks
        self.on_point = on_point
        self.should_abort = should_abort
        self.on_save_error = on_save_error
        self.trace_slots = trace_slots
        self.record: RunRecord | None = None
        self.collected: list[StepResult] = []
        self.save_path: Path | None = None
        self._savedir: Path | None = None
        self._save_warned = False

    def run(self) -> SweepOutcome:
        """Run the sweep. Anything a step raises is handled per step; this never raises for them."""
        sweep = self.sweep
        with contextlib.ExitStack() as stack:
            for lock in self.locks:  # one logical operation: nothing may interleave with it
                stack.enter_context(lock)
            try:
                prepare_analyzer(self.devices["mxa"], self.trace_slots, sweep.config)
            except Exception as exc:
                log.exception("sweep: failed to configure analyzer")
                return SweepOutcome(record=None, setup_error=exc)

            record = self.record = RunRecord(config=asdict(sweep.config))
            ctx = ExperimentContext(
                mx=self.devices["mxa"],
                run_id=record.run_id,
                shutter=self.devices.get("shutter"),
                wavemeter=self.devices.get("wavemeter"),
                config=record.config,
            )
            aborted = failed = False
            try:
                run_sequence(sweep.steps, ctx, on_error="skip", on_step=self._on_step)
            except SweepAborted:
                aborted = True
            except Exception:  # per-step errors are handled by on_error="skip"; this is a net
                log.exception("sweep: run_sequence raised unexpectedly")
                failed = True
            self.finish(aborted=aborted, failed=failed)
        return SweepOutcome(record, self.collected, self.save_path, aborted, failed)

    def finish(self, *, aborted: bool = False, failed: bool = False) -> None:
        """Rewrite the file with how the run ended (until now it says "running")."""
        if self.record is not None:
            self.record.finish(aborted=aborted, failed=failed)
        if self.collected:
            self.checkpoint()

    def mark_died(self) -> None:
        """For a caller whose worker died: leave "failed" on disk, never "running"."""
        with contextlib.suppress(Exception):
            self.finish(failed=True)

    def checkpoint(self, *, on_error: Callable[[Exception], None] | None = None) -> None:
        """Write everything collected so far to disk, overwriting the same file pair atomically.

        A save that fails (disk full, permissions) is logged and reported once, not per point.
        """
        try:
            if self._savedir is None:
                self._savedir = create_dirs()
            self.save_path = save_step_results(
                list(self.collected),
                self._savedir,
                self.record.as_metadata() if self.record is not None else None,
                name=self.sweep.kind,
                path=self.save_path,
            )
        except Exception as exc:
            log.exception("sweep: could not save results")
            if not self._save_warned:
                self._save_warned = True
                report = on_error or self.on_save_error
                if report is not None:
                    report(exc)

    def _on_step(
        self,
        index: int,
        total: int,
        step: Step,
        result: StepResult | None,
        error: BaseException | None,
    ) -> None:
        if self.record is None:  # only reachable if a caller drives the hook by hand
            raise RuntimeError("the run has not started")
        self.record.on_step(index, total, step, result, error)
        if result is not None:
            self.collected.append(result)
            self.checkpoint()
        if self.on_point is not None:
            self.on_point(index, total, step, result, error)
        if self.should_abort():
            raise SweepAborted
