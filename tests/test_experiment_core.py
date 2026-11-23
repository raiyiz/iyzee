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


# -- abort, retry and progress ----------------------------------------------------------------


class _Flaky:
    """Fails with ``error`` the first ``failures`` times, then measures."""

    label = "flaky"

    def __init__(self, error: Exception, failures: int) -> None:
        self.error, self.failures, self.calls = error, failures, 0

    def run(self, ctx):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return StepResult(self.label, 1.0, "Hz", {})


def test_a_step_that_times_out_is_tried_again_and_says_so_but_other_errors_are_not(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.core.time.sleep", lambda s: None)
    step = _Flaky(TimeoutError("lagged"), failures=1)
    (result,) = run_sequence([step], make_ctx(), retries=1)
    assert step.calls == 2 and result.meta["attempts"] == 2

    always = _Flaky(TimeoutError("lagged"), failures=9)
    with pytest.raises(TimeoutError):  # out of attempts: the usual on_error policy applies
        run_sequence([always], make_ctx(), retries=2)
    assert always.calls == 3

    wrong = _Flaky(ValueError("a bug"), failures=9)
    assert run_sequence([wrong], make_ctx(), retries=2, on_error="skip") == []
    assert wrong.calls == 1  # not a transient fault: not repeated

    clean = _Flaky(TimeoutError(), failures=0)
    assert "attempts" not in run_sequence([clean], make_ctx(), retries=1)[0].meta


def test_a_wait_notices_an_abort_and_the_run_reports_what_it_is_doing(monkeypatch):
    from iyzee.experiment.core import RunAborted

    slept: list[float] = []
    monkeypatch.setattr("iyzee.experiment.core.time.sleep", slept.append)
    seen: list[str] = []
    stop = {"now": False}

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        stop["now"] = len(slept) == 2  # the request arrives during the second slice

    monkeypatch.setattr("iyzee.experiment.core.time.sleep", fake_sleep)
    ctx = ExperimentContext(
        mx=object(), run_id="r", progress=seen.append, should_abort=lambda: stop["now"]
    )
    ctx.phase("settling")
    with pytest.raises(RunAborted):
        ctx.sleep(10.0)  # would be 20 slices; the abort cuts it to two
    assert slept == [0.5, 0.5] and seen == ["settling"]
