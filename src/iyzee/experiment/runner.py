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
    savedir: Path | None = None
    save_path: Path | None = None
    save_warned = False

    def checkpoint(record: RunRecord) -> None:
        nonlocal savedir, save_path, save_warned
        try:
            if savedir is None:
                savedir = create_dirs()
            save_path = save_step_results(
                collected,
                savedir,
                record.as_metadata(),
                name=sweep.kind,
                path=save_path,
            )
        except Exception as exc:
            if not save_warned:
                save_warned = True
                log.exception("sweep: could not save results")
                if on_save_error is not None:
                    on_save_error(exc)

    with contextlib.ExitStack() as stack:
        for lock in locks:
            stack.enter_context(lock)

        try:
            prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), sweep.config)
        except Exception as exc:
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

        def observe(
            index: int,
            total: int,
            step: Step,
            result: StepResult | None,
            error: BaseException | None,
        ) -> None:
            record.on_step(index, total, step, result, error)
            if result is not None:
                collected.append(result)
                checkpoint(record)
            if on_step is not None:
                on_step(index, total, step, result, error)
            if should_abort is not None and should_abort():
                raise SweepAborted()

        try:
            run_sequence(sweep.steps, ctx, on_error="skip", on_step=observe)
        except SweepAborted:
            outcome.aborted = True
        except Exception as exc:
            outcome.failed = True
            outcome.error = exc
            log.exception("sweep: run failed unexpectedly")
        finally:
            record.finish(aborted=outcome.aborted, failed=outcome.failed)
            if collected:
                checkpoint(record)

    outcome.savedir = savedir
    outcome.save_path = save_path
    return outcome
