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
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..devices.mxa import MXAError
from .core import ExperimentContext, RunAborted, RunRecord, Step, StepResult, run_sequence
from .io import create_dirs, save_step_results
from .procedures import TRACE_SHOT, TRACE_SQZ, Sweep, prepare_analyzer

log = logging.getLogger("iyzee.experiment")

PointCallback = Callable[[int, int, Step, StepResult | None, BaseException | None], None]


#: Seconds a run waits for an instrument that something else (the console, another page)
#: is using before it gives up and says so, instead of waiting for ever.
LOCK_WAIT_S = 15.0
#: A step that fails with a timeout or a lost reply is run once more before it counts as failed.
STEP_RETRIES = 1
RETRY_DELAY_S = 1.0
#: Which failures are worth a second try: network and timeout errors, and the analyzer's.
RETRYABLE = (OSError, MXAError)

#: Raised from the per-point hook, or from inside a step, to stop a run cleanly.
SweepAborted = RunAborted


class InstrumentBusyError(RuntimeError):
    """An instrument the run needs stayed in use by something else."""


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
        on_phase: Callable[[str], None] | None = None,
        step_retries: int = STEP_RETRIES,
        lock_timeout_s: float = LOCK_WAIT_S,
    ) -> None:
        self.sweep = sweep
        self.devices = devices
        self.locks = locks
        self.on_point = on_point
        self.should_abort = should_abort
        self.on_save_error = on_save_error
        self.trace_slots = trace_slots
        self.on_phase = on_phase
        self.step_retries = step_retries
        self.lock_timeout_s = lock_timeout_s
        self.record: RunRecord | None = None
        self.collected: list[StepResult] = []
        self.save_path: Path | None = None
        self._savedir: Path | None = None
        self._save_warned = False

    def run(self) -> SweepOutcome:
        """Run the sweep. Anything a step raises is handled per step; this never raises for them."""
        sweep = self.sweep
        with contextlib.ExitStack() as stack:
            try:
                for lock in self.locks:  # one logical operation: nothing may interleave with it
                    self._acquire(stack, lock)
                self._preflight()
                prepare_analyzer(self.devices["mxa"], self.trace_slots, sweep.config)
            except RunAborted:
                return SweepOutcome(record=None, aborted=True)
            except Exception as exc:
                log.exception("sweep: could not start")
                return SweepOutcome(record=None, setup_error=exc)

            record = self.record = RunRecord(config=asdict(sweep.config))
            ctx = ExperimentContext(
                mx=self.devices["mxa"],
                run_id=record.run_id,
                shutter=self.devices.get("shutter"),
                wavemeter=self.devices.get("wavemeter"),
                config=record.config,
                progress=self._progress,
                should_abort=self.should_abort,
            )
            aborted = failed = False
            try:
                run_sequence(
                    sweep.steps,
                    ctx,
                    on_error="skip",
                    on_step=self._on_step,
                    retries=self.step_retries,
                    retry_delay_s=RETRY_DELAY_S,
                    retry_on=RETRYABLE,
                )
            except SweepAborted:
                aborted = True
            except Exception:  # per-step errors are handled by on_error="skip"; this is a net
                log.exception("sweep: run_sequence raised unexpectedly")
                failed = True
            finally:
                self._secure_shutter()
            self.finish(aborted=aborted, failed=failed)
        return SweepOutcome(record, self.collected, self.save_path, aborted, failed)

    def _progress(self, text: str) -> None:
        if self.on_phase is not None:
            try:
                self.on_phase(text)
            except Exception:  # a display problem must never stop a measurement
                log.exception("sweep: the progress display failed")

    def _acquire(self, stack: contextlib.ExitStack, lock: AbstractContextManager[Any]) -> None:
        """Take ``lock`` for the whole run, waiting at most ``lock_timeout_s`` (and not through an
        abort request): a console cell or another page holding the instrument must not leave
        the run waiting for ever with nothing said."""
        acquire = getattr(lock, "acquire", None)
        if acquire is None:  # a plain context manager
            stack.enter_context(lock)
            return
        self._progress("waiting for the instruments")
        deadline = time.monotonic() + self.lock_timeout_s
        while not acquire(timeout=0.25):
            if self.should_abort():
                raise RunAborted
            if time.monotonic() >= deadline:
                raise InstrumentBusyError(
                    f"an instrument is still in use by something else after {self.lock_timeout_s:g} s "
                    "(a console cell or another page?)"
                )
        stack.callback(lock.release)  # type: ignore[attr-defined]

    def _preflight(self) -> None:
        """Refuse to start a run that cannot be valid: with the supply's master output off the
        shutter never opens, and 'squeezing' would be recorded as shot noise. Anything that
        merely cannot be read is logged and does not stop the run."""
        retrieve = getattr(self.devices.get("shutter"), "retrieve_state", None)
        if retrieve is None:
            return
        self._progress("checking the shutter supply")
        try:
            state = retrieve()
        except Exception:
            log.warning("sweep: could not read the supply before starting", exc_info=True)
            return
        if not getattr(state, "general_output", True):
            raise RuntimeError(
                "the power supply's master output is off, so the shutter cannot open "
                "(switch it on on the Power page)"
            )

    def _secure_shutter(self) -> None:
        """However the run ended, leave the shutter closed (and say so if that is not possible)."""
        close = getattr(self.devices.get("shutter"), "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:
            log.critical("sweep: could not close the shutter at the end of the run", exc_info=True)

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
