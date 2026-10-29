"""Tests for shared sweep execution, persistence, and abort semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from iyzee.experiment.core import StepResult
from iyzee.experiment.procedures import Sweep, build_bandwidth_sweep
from iyzee.experiment import runner


class FakeMXA:
    def __getattr__(self, name: str) -> Any:
        if name == "get_trace_data":
            return lambda *, trace_num, binary: [float(trace_num), 1.0]
        return lambda *args, **kwargs: None


def _sweep() -> Sweep:
    return build_bandwidth_sweep([20_000.0, 40_000.0])


def test_runner_checkpoints_each_point_and_final_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: list[tuple[int, str, Path | None]] = []

    def save(
        results: list[StepResult],
        savedir: Path,
        metadata: dict[str, Any] | None = None,
        *,
        name: str = "",
        path: Path | None = None,
    ) -> Path:
        saved.append((len(results), str((metadata or {}).get("status")), path))
        return path or savedir / f"{name}.npz"

    monkeypatch.setattr(runner, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(runner, "save_step_results", save)

    outcome = runner.run_recorded_sweep(_sweep(), mx=FakeMXA())

    assert outcome.error is None and outcome.setup_error is None
    assert outcome.record is not None
    assert outcome.record.status == "completed"
    assert len(outcome.results) == 2
    assert [count for count, _, _ in saved] == [1, 2, 2]
    assert saved[-1][1] == "completed"
    assert outcome.save_path == tmp_path / "bandwidth.npz"


def test_save_failure_is_reported_once_without_stopping_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[Exception] = []
    attempts = 0

    def save(
        results: list[StepResult],
        savedir: Path,
        metadata: dict[str, Any] | None = None,
        *,
        name: str = "",
        path: Path | None = None,
    ) -> Path:
        nonlocal attempts
        attempts += 1
        raise OSError("disk full")

    monkeypatch.setattr(runner, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(runner, "save_step_results", save)

    outcome = runner.run_recorded_sweep(
        _sweep(), mx=FakeMXA(), on_save_error=errors.append
    )

    assert attempts == 3
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)
    assert len(outcome.results) == 2
    assert outcome.record is not None and outcome.record.status == "completed"


def test_abort_finishes_and_checkpoints_the_partial_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: list[dict[str, Any]] = []

    def save(
        results: list[StepResult],
        savedir: Path,
        metadata: dict[str, Any] | None = None,
        *,
        name: str = "",
        path: Path | None = None,
    ) -> Path:
        saved.append(metadata or {})
        return path or savedir / "bandwidth.npz"

    monkeypatch.setattr(runner, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(runner, "save_step_results", save)

    outcome = runner.run_recorded_sweep(
        _sweep(), mx=FakeMXA(), should_abort=lambda: True
    )

    assert outcome.aborted and not outcome.failed
    assert outcome.record is not None and outcome.record.status == "aborted"
    assert len(outcome.results) == 1
    assert saved[-1]["status"] == "aborted"


def test_runner_reports_analyzer_setup_failure_without_creating_a_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenMXA(FakeMXA):
        def set_center_freq(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("analyzer unavailable")

    outcome = runner.run_recorded_sweep(_sweep(), mx=BrokenMXA())

    assert isinstance(outcome.setup_error, RuntimeError)
    assert outcome.record is None
    assert outcome.results == []



def test_runner_marks_unexpected_failure_after_partial_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: list[dict[str, Any]] = []

    def save(
        results: list[StepResult],
        savedir: Path,
        metadata: dict[str, Any] | None = None,
        *,
        name: str = "",
        path: Path | None = None,
    ) -> Path:
        saved.append(metadata or {})
        return path or savedir / "bandwidth.npz"

    def run_then_fail(steps, ctx, *, on_error, on_step):
        on_step(0, 2, steps[0], StepResult("first", 20_000.0, "Hz", {"trace": [1.0]}), None)
        raise RuntimeError("measurement callback failed")

    monkeypatch.setattr(runner, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(runner, "save_step_results", save)
    monkeypatch.setattr(runner, "run_sequence", run_then_fail)

    outcome = runner.run_recorded_sweep(_sweep(), mx=FakeMXA())

    assert outcome.failed and isinstance(outcome.error, RuntimeError)
    assert outcome.record is not None and outcome.record.status == "failed"
    assert len(outcome.results) == 1
    assert saved[-1]["status"] == "failed"
