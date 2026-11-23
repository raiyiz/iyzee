"""The shared sweep runner: locks, a save after every point, and how the run ended."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from iyzee.experiment import StepResult
from iyzee.experiment import runner as runner_mod
from iyzee.experiment.core import step_label
from iyzee.experiment.procedures import AnalyzerConfig, Sweep
from iyzee.experiment.runner import SweepRunner


class _Point:
    """A step that measures one trivial point, or raises."""

    def __init__(self, label: str, *, fail: bool = False) -> None:
        self.label, self.fail = label, fail

    def run(self, ctx: Any) -> StepResult:
        if self.fail:
            raise ValueError(f"{self.label} broke")
        trace = np.arange(3.0)
        return StepResult(self.label, 1.0, "Hz", {"squeezing": trace, "shot_noise": trace})


def _sweep(*steps: Any) -> Sweep:
    return Sweep(kind="bandwidth", steps=list(steps), config=AnalyzerConfig())


@pytest.fixture
def lab_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(runner_mod, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(runner_mod, "prepare_analyzer", lambda *args, **kwargs: None)
    return tmp_path


def _saved_status(directory: Path) -> str:
    (sidecar,) = directory.glob("*.json")  # one file pair, overwritten after every point
    return str(json.loads(sidecar.read_text())["run_metadata"]["status"])


def test_a_run_holds_the_locks_saves_after_each_point_and_records_its_end(lab_dir: Path):
    events: list[str] = []

    @contextmanager
    def lock():
        events.append("lock")
        yield
        events.append("unlock")

    def on_point(index, total, step, result, error):
        events.append(f"{index + 1}/{total} {'error' if error else 'ok'}")
        if result is not None:  # saved before the hook runs, so the file already has this point
            assert _saved_status(lab_dir) == "running"

    sweep = _sweep(_Point("a"), _Point("b", fail=True), _Point("c"))
    outcome = SweepRunner(sweep, {"mxa": object()}, [lock()], on_point=on_point).run()

    assert events == ["lock", "1/3 ok", "2/3 error", "3/3 ok", "unlock"]
    assert [r.label for r in outcome.collected] == ["a", "c"]
    assert outcome.record is not None and outcome.record.status == "completed_with_errors"
    assert outcome.save_path is not None and outcome.save_path.exists()
    assert _saved_status(lab_dir) == "completed_with_errors"  # rewritten at the end


def test_aborting_a_failed_setup_a_failing_save_and_a_dead_worker_all_end_cleanly(
    lab_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    # Abort: stops after the point in flight; the file says so.
    seen: list[str] = []
    runner = SweepRunner(
        _sweep(_Point("a"), _Point("b"), _Point("c")),
        {"mxa": object()},
        on_point=lambda i, t, step, r, e: seen.append(step_label(step, i)),
        should_abort=lambda: len(seen) >= 1,
    )
    outcome = runner.run()
    assert outcome.aborted and not outcome.failed and seen == ["a"]
    assert [r.label for r in outcome.collected] == ["a"]
    assert _saved_status(lab_dir) == "aborted"

    # A worker that died: whatever is on disk ends as "failed", never "running".
    dying = SweepRunner(_sweep(_Point("a")), {"mxa": object()})
    dying.record = None
    dying.mark_died()  # nothing started: must not raise
    runner.mark_died()
    assert _saved_status(lab_dir) == "failed"

    # A failing save never stops the measurement, and is reported once, not per point.
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(runner_mod, "save_step_results", broken)
    reports: list[Exception] = []
    outcome = SweepRunner(
        _sweep(_Point("a"), _Point("b"), _Point("c")),
        {"mxa": object()},
        on_save_error=reports.append,
    ).run()
    assert len(outcome.collected) == 3 and outcome.save_path is None
    assert len(reports) == 1 and isinstance(reports[0], OSError)

    # An analyzer that cannot be set up: nothing is measured, nothing is saved, the cause is kept.
    def refuse(*args, **kwargs):
        raise RuntimeError("no analyzer")

    monkeypatch.setattr(runner_mod, "prepare_analyzer", refuse)
    outcome = SweepRunner(_sweep(_Point("a")), {"mxa": object()}).run()
    assert outcome.record is None and not outcome.collected
    assert isinstance(outcome.setup_error, RuntimeError)


# -- an instrument in use, a supply that is off, a point that times out ----------------------------


def test_a_run_does_not_wait_for_ever_for_an_instrument_in_use(lab_dir: Path):
    import threading

    lock = threading.RLock()
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with lock:
            held.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    held.wait(5)
    try:
        outcome = SweepRunner(
            _sweep(_Point("a")), {"mxa": object()}, [lock], lock_timeout_s=0.3
        ).run()
        assert isinstance(outcome.setup_error, runner_mod.InstrumentBusyError)
        assert not outcome.collected and outcome.record is None

        asked = SweepRunner(
            _sweep(_Point("a")), {"mxa": object()}, [lock], should_abort=lambda: True
        ).run()
        assert asked.aborted and asked.setup_error is None  # an abort is heard while waiting
    finally:
        release.set()
        thread.join()
    assert lock.acquire(timeout=1)  # and the run left nothing locked behind it
    lock.release()


class _Supply:
    def __init__(self, master_on: bool) -> None:
        self.master_on, self.closed = master_on, 0

    def retrieve_state(self):
        return SimpleNamespace(general_output=self.master_on)

    def close(self) -> None:
        self.closed += 1


def _frequency_like(*steps: Any) -> Sweep:
    return Sweep(kind="frequency", steps=list(steps), config=AnalyzerConfig())


def test_a_run_refuses_to_start_with_the_supply_off_and_always_leaves_the_shutter_closed(
    lab_dir: Path,
):
    off = _Supply(master_on=False)
    outcome = SweepRunner(_frequency_like(_Point("a")), {"mxa": object(), "shutter": off}).run()
    assert "master output is off" in str(outcome.setup_error) and not outcome.collected

    on = _Supply(master_on=True)
    outcome = SweepRunner(_frequency_like(_Point("a")), {"mxa": object(), "shutter": on}).run()
    assert outcome.setup_error is None and len(outcome.collected) == 1
    assert on.closed == 1  # closed at the end of the run, as a last guarantee
