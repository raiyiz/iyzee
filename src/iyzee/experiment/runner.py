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
from .procedures import (
    TRACE_SHOT,
    TRACE_SQZ,
    FatalSweepError,
    Sweep,
    acquisition_timeout_s,
    prepare_analyzer,
)

log = logging.getLogger("iyzee.experiment")

PointCallback = Callable[[int, int, Step, StepResult | None, BaseException | None], None]


class SweepAborted(Exception):
    """Raised from the per-point hook to stop a run cleanly after the current point."""


class SweepFailed(Exception):
    """Stop after an unrecoverable instrument error rather than repeating it at each point."""


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
        on_stage: Callable[[str], None] | None = None,
        should_abort: Callable[[], bool] = lambda: False,
        on_save_error: Callable[[Exception], None] | None = None,
        trace_slots: tuple[int, int] = (TRACE_SQZ, TRACE_SHOT),
    ) -> None:
        self.sweep = sweep
        self.devices = devices
        self.locks = locks
        self.on_point = on_point
        self.on_stage = on_stage
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
                timeout_s = acquisition_timeout_s(sweep.config)
                self._report_stage("Configuring MXA for sweep")
                prepare_analyzer(self.devices["mxa"], self.trace_slots, sweep.config)
                self._report_stage("MXA configured; starting sweep points")
            except Exception as exc:
                log.exception("sweep: failed to configure analyzer")
                return SweepOutcome(record=None, setup_error=exc)

            config = asdict(sweep.config)
            config["acquisition_timeout_s"] = timeout_s
            record = self.record = RunRecord(config=config)
            ctx = ExperimentContext(
                mx=self.devices["mxa"],
                run_id=record.run_id,
                shutter=self.devices.get("shutter"),
                wavemeter=self.devices.get("wavemeter"),
                config=record.config,
                on_stage=self._report_stage,
            )
            aborted = failed = False
            try:
                run_sequence(sweep.steps, ctx, on_error="skip", on_step=self._on_step)
            except SweepAborted:
                aborted = True
            except SweepFailed as exc:
                log.error("sweep: stopped after an unrecoverable step failure: %s", exc)
                failed = True
            except Exception:  # per-step errors are handled by on_error="skip"; this is a net
                log.exception("sweep: run_sequence raised unexpectedly")
                failed = True
            self.finish(aborted=aborted, failed=failed)
        return SweepOutcome(record, self.collected, self.save_path, aborted, failed)

    def _report_stage(self, message: str) -> None:
        if self.on_stage is not None:
            try:
                self.on_stage(message)
            except Exception:
                log.exception("sweep: stage callback failed")

    def finish(self, *, aborted: bool = False, failed: bool = False) -> None:
        """Rewrite the file with how the run ended (until now it says "running")."""
        if self.record is not None:
            self.record.finish(aborted=aborted, failed=failed)
        if self.record is not None:
            # Keep a failed first point visible on disk instead of leaving no trace of the run.
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
            self._report_stage(f"{step_label(step, index)}: saving checkpoint")
            self.checkpoint()
        if self.on_point is not None:
            self.on_point(index, total, step, result, error)
        if isinstance(error, FatalSweepError):
            label = getattr(step, "label", f"step[{index}]")
            raise SweepFailed(f"{label}: {error}") from error
        if self.should_abort():
            raise SweepAborted
