import logging

import pytest

from iyzee.experiment.runner import run_sequence
from iyzee.experiment.step import ExperimentContext, StepResult


class RecordingStep:
    def __init__(self, label, value, fail=False):
        self.label = label
        self.value = value
        self.fail = fail

    def run(self, ctx):
        if self.fail:
            raise RuntimeError(f"{self.label} failed")
        return StepResult(label=self.label, x_value=self.value, x_unit="a.u.", traces={})


def make_ctx():
    return ExperimentContext(mx=object(), run_id="test-run")


def test_run_sequence_returns_results_in_order():
    steps = [RecordingStep("a", 1), RecordingStep("b", 2)]

    results = run_sequence(steps, make_ctx())

    assert [r.x_value for r in results] == [1, 2]


def test_run_sequence_raises_by_default_on_step_failure():
    steps = [RecordingStep("a", 1), RecordingStep("b", 2, fail=True)]

    with pytest.raises(RuntimeError, match="b failed"):
        run_sequence(steps, make_ctx())


def test_run_sequence_can_skip_failures():
    steps = [RecordingStep("a", 1, fail=True), RecordingStep("b", 2)]

    results = run_sequence(steps, make_ctx(), on_error="skip")

    assert [r.x_value for r in results] == [2]


def test_run_sequence_rejects_unknown_error_policy():
    with pytest.raises(ValueError):
        run_sequence([], make_ctx(), on_error="bogus")


def test_run_sequence_logs_step_lifecycle(caplog):
    caplog.set_level(logging.INFO, logger="iyzee.experiment")

    run_sequence([RecordingStep("a", 1)], make_ctx())

    assert "starting a" in caplog.text
    assert "finished a" in caplog.text
