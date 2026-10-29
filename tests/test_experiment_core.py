import logging

import pytest

from iyzee.experiment.core import ExperimentContext, StepResult, run_sequence


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


def test_run_sequence_runs_in_order_and_handles_failures_by_policy(caplog):
    caplog.set_level(logging.INFO, logger="iyzee.experiment")
    results = run_sequence([RecordingStep("a", 1), RecordingStep("b", 2)], make_ctx())
    assert [r.x_value for r in results] == [1, 2]
    assert "starting a" in caplog.text and "finished a" in caplog.text  # the step lifecycle

    with pytest.raises(RuntimeError, match="b failed"):  # raising is the default
        run_sequence([RecordingStep("a", 1), RecordingStep("b", 2, fail=True)], make_ctx())
    skipped = run_sequence(
        [RecordingStep("a", 1, fail=True), RecordingStep("b", 2)], make_ctx(), on_error="skip"
    )
    assert [r.x_value for r in skipped] == [2]
    with pytest.raises(ValueError):
        run_sequence([], make_ctx(), on_error="bogus")


# -- RunRecord: how a run ended, saved beside its data -----------------------------------------


def _record_with(*outcomes: Exception | None):
    from iyzee.experiment.core import RunRecord

    record = RunRecord(config={"center_hz": 1e6})
    for index, error in enumerate(outcomes):
        result = None if error else StepResult(f"p{index}", 0.0, "Hz", {})
        record.on_step(index, len(outcomes), object(), result, error)
    return record


def test_a_run_record_reports_how_the_run_ended_and_can_be_fed_by_run_sequence():
    # -- a run record reports how the run ended and names failed steps as plain json
    import json

    for outcomes, finish, status in (
        ((None, None), {}, "completed"),
        ((None, RuntimeError("x")), {}, "completed_with_errors"),
        ((None,), {"aborted": True}, "aborted"),
        ((None,), {"failed": True}, "failed"),
        ((None,), {"aborted": True, "failed": True}, "aborted"),
    ):
        record = _record_with(*outcomes)
        assert record.status == "running"  # what a run that never finished leaves behind
        assert record.finished_at_utc is None
        record.finish(**finish)
        assert record.status == status
        assert record.finished_at_utc is not None and record.finished_at_utc.endswith("Z")

    record = _record_with(None, ValueError("wavemeter said no"))
    record.finish()
    meta = json.loads(json.dumps(record.as_metadata()))
    assert meta["failed_steps"] == [
        {"index": 1, "label": "step[1]", "error_type": "ValueError", "error": "wavemeter said no"}
    ]
    assert meta["config"] == {"center_hz": 1e6} and meta["run_id"] == record.run_id
