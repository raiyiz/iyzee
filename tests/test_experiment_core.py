import logging

import pytest

from iyzee.experiment.core import (
    ExperimentContext,
    StepResult,
    TooManyFailures,
    run_sequence,
)


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


# -- giving up on an instrument that is gone ----------------------------------------------------


def _steps(pattern: str) -> list[RecordingStep]:
    """``"ok"``/``"x"`` per step: a step that fails is ``x``."""
    return [RecordingStep(f"s{i}", i, fail=c == "x") for i, c in enumerate(pattern)]


def test_skip_mode_gives_up_after_consecutive_failures_and_keeps_what_was_measured():
    seen: list[tuple[int, bool]] = []
    steps = _steps("oo" + "xxx" + "oo")  # 2 good points, then 3 failures in a row
    with pytest.raises(TooManyFailures, match=r"3 failed steps in a row.*s4 failed") as caught:
        run_sequence(
            steps,
            make_ctx(),
            on_error="skip",
            max_consecutive_failures=3,
            on_step=lambda i, n, step, result, error: seen.append((i, error is not None)),
        )

    assert [r.x_value for r in caught.value.results] == [0, 1]  # the good points are not lost
    assert caught.value.count == 3
    assert seen == [(0, False), (1, False), (2, True), (3, True), (4, True)]  # all reported
    # and the two steps after the failures were never attempted
    assert [i for i, _ in seen][-1] == 4


def test_a_success_resets_the_count_so_scattered_failures_do_not_trip_it():
    results = run_sequence(
        _steps("xxoxxoxx"), make_ctx(), on_error="skip", max_consecutive_failures=3
    )
    assert [r.x_value for r in results] == [2, 5]


def test_the_limit_can_be_switched_off_and_does_not_apply_to_raise_mode():
    results = run_sequence(
        _steps("x" * 20 + "o"), make_ctx(), on_error="skip", max_consecutive_failures=None
    )
    assert [r.x_value for r in results] == [20]
    with pytest.raises(RuntimeError, match="s0 failed") as caught:  # still the first failure
        run_sequence(_steps("xx"), make_ctx(), on_error="raise", max_consecutive_failures=1)
    assert not isinstance(caught.value, TooManyFailures)


def test_the_default_gives_up_after_five_and_a_nonsense_limit_is_refused():
    with pytest.raises(TooManyFailures, match="5 failed steps"):
        run_sequence(_steps("x" * 9), make_ctx(), on_error="skip")
    with pytest.raises(ValueError, match="at least 1"):
        run_sequence(_steps("o"), make_ctx(), on_error="skip", max_consecutive_failures=0)


# -- RunRecord: how a run ended, saved beside its data -----------------------------------------


def _record_with(*outcomes: Exception | None):
    from iyzee.experiment.core import RunRecord

    record = RunRecord(config={"center_hz": 1e6})
    for index, error in enumerate(outcomes):
        result = None if error else StepResult(f"p{index}", 0.0, "Hz", {})
        record.on_step(index, len(outcomes), object(), result, error)
    return record


def test_a_run_record_reports_how_the_run_ended_and_names_failed_steps_as_plain_json():
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
