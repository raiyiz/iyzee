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


# -- RunRecord: how a run ended, saved beside its data -----------------------------------------


def _record_with(*outcomes: Exception | None):
    from iyzee.experiment.core import RunRecord

    record = RunRecord(config={"center_hz": 1e6})
    for index, error in enumerate(outcomes):
        result = None if error else StepResult(f"p{index}", 0.0, "Hz", {})
        record.on_step(index, len(outcomes), object(), result, error)
    return record


@pytest.mark.parametrize(
    "outcomes,finish,status",
    [
        ((None, None), {}, "completed"),
        ((None, RuntimeError("x")), {}, "completed_with_errors"),
        ((None,), {"aborted": True}, "aborted"),
        ((None,), {"failed": True}, "failed"),
        ((None,), {"aborted": True, "failed": True}, "aborted"),
    ],
)
def test_run_record_status(outcomes, finish, status):
    record = _record_with(*outcomes)
    assert record.status == "running"  # what a run that never finished leaves behind
    assert record.finished_at_utc is None

    record.finish(**finish)

    assert record.status == status
    assert record.finished_at_utc is not None and record.finished_at_utc.endswith("Z")


def test_run_record_names_each_failed_step_and_is_plain_json():
    import json

    record = _record_with(None, ValueError("wavemeter said no"))
    record.finish()

    meta = json.loads(json.dumps(record.as_metadata()))

    assert meta["failed_steps"] == [
        {"index": 1, "label": "step[1]", "error_type": "ValueError", "error": "wavemeter said no"}
    ]
    assert meta["config"] == {"center_hz": 1e6} and meta["run_id"] == record.run_id


def test_run_record_can_be_fed_by_run_sequence():
    from iyzee.experiment.core import RunRecord

    class Boom:
        label = "bad point"

        def run(self, ctx):
            raise RuntimeError("no lock")

    record = RunRecord()
    run_sequence(
        [Boom()], ExperimentContext(mx=None, run_id="r1"), on_error="skip", on_step=record.on_step
    )  # type: ignore[arg-type]

    assert [f.label for f in record.failed_steps] == ["bad point"]
