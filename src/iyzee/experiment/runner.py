"""Headless execution and checkpointing for measurement sweeps.

Instrument ownership stays with the caller. The runner only holds the supplied locks, configures
the analyzer, runs the selected sweep, records results, and saves checkpoints. UI or notebook
callbacks observe progress without owning the measurement lifecycle.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .core import ExperimentContext, RunRecord, Step, StepCallback, StepResult, run_sequence
from .io import create_dirs, save_step_results
from .procedures import TRACE_SHOT, TRACE_SQZ, Sweep, prepare_analyzer

log = logging.getLogger("iyzee.experiment")


class SweepAborted(Exception):
    """Raised by a progress callback to stop after the current attempted step."""


@dataclass(slots=True)
class SweepOutcome:
    """Final state of a recorded sweep, including setup or unexpected failures."""

    record: RunRecord | None = None
    results: list[StepResult] = field(default_factory=list)
    total: int = 0
    save_path: Path | None = None
    savedir: Path | None = None
    aborted: bool = False
    failed: bool = False
    setup_error: Exception | None = None
    error: Exception | None = None


@dataclass(slots=True)
class _CheckpointWriter:
    sweep: Sweep
    results: list[StepResult]
    on_checkpoint: Callable[[Path, Path], None] | None = None
    on_save_error: Callable[[Exception], None] | None = None
    savedir: Path | None = None
    save_path: Path | None = None
    warned: bool = False

    def write(self, record: RunRecord) -> None:
        try:
            if self.savedir is None:
                self.savedir = create_dirs()
            self.save_path = save_step_results(
                self.results,
                self.savedir,
                record.as_metadata(),
                name=self.sweep.kind,
                path=self.save_path,
            )
        except Exception as exc:
            if not self.warned:
                self.warned = True
                log.exception("sweep: could not save results")
                if self.on_save_error is not None:
                    self.on_save_error(exc)
            return
        if self.on_checkpoint is not None:
            self.on_checkpoint(self.savedir, self.save_path)


@dataclass(slots=True)
class _StepObserver:
    record: RunRecord
    results: list[StepResult]
    checkpoint: _CheckpointWriter
    on_step: StepCallback | None
    should_abort: Callable[[], bool] | None

    def __call__(
        self,
        index: int,
        total: int,
        step: Step,
        result: StepResult | None,
        error: BaseException | None,
    ) -> None:
        self.record.on_step(index, total, step, result, error)
        if result is not None:
            self.results.append(result)
            self.checkpoint.write(self.record)
        if self.on_step is not None:
            self.on_step(index, total, step, result, error)
        if self.should_abort is not None and self.should_abort():
            raise SweepAborted()


def run_recorded_sweep(
    sweep: Sweep,
    *,
    mx: Any,
    locks: Sequence[AbstractContextManager[Any]] = (),
    shutter: Any | None = None,
    wavemeter: Any | None = None,
    results: list[StepResult] | None = None,
    on_step: StepCallback | None = None,
    should_abort: Callable[[], bool] | None = None,
    on_record: Callable[[RunRecord], None] | None = None,
    on_checkpoint: Callable[[Path, Path], None] | None = None,
    on_save_error: Callable[[Exception], None] | None = None,
) -> SweepOutcome:
    """Run, checkpoint, and finalize a sweep using already-connected devices.

    Every successful point is checkpointed immediately. Checkpoint failures are non-fatal and
    reported once through `on_save_error`; a final checkpoint stores the completed/aborted status.
    The supplied lock contexts are held for the whole run. `on_step` runs after persistence and
    can update a UI or print a notebook progress line. Returning a `SweepOutcome` lets callers
    decide how to present setup and unexpected errors.
    """
    collected = results if results is not None else []
    outcome = SweepOutcome(results=collected, total=len(sweep.steps))
    checkpoint = _CheckpointWriter(
        sweep, collected, on_checkpoint=on_checkpoint, on_save_error=on_save_error
    )

    with contextlib.ExitStack() as stack:
        for lock in locks:
            stack.enter_context(lock)

        try:
            prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), sweep.config)
        except Exception as exc:  # noqa: BLE001 - preserve analyzer setup failure for the caller
            outcome.setup_error = exc
            return outcome

        record = RunRecord(config=asdict(sweep.config))
        outcome.record = record
        if on_record is not None:
            on_record(record)
        ctx = ExperimentContext(
            mx=mx,
            run_id=record.run_id,
            shutter=shutter,
            wavemeter=wavemeter,
            config=record.config,
        )
        observer = _StepObserver(record, collected, checkpoint, on_step, should_abort)

        try:
            run_sequence(sweep.steps, ctx, on_error="skip", on_step=observer)
        except SweepAborted:
            outcome.aborted = True
        except Exception as exc:
            outcome.failed = True
            outcome.error = exc
            log.exception("sweep: run failed unexpectedly")
        finally:
            record.finish(aborted=outcome.aborted, failed=outcome.failed)
            if collected:
                checkpoint.write(record)

    outcome.savedir = checkpoint.savedir
    outcome.save_path = checkpoint.save_path
    return outcome
