"""The MXA driver and acquisition when the analyzer does not answer in time.

Runs against ``fake_visa.SimulatedMXA``, which keeps a late reply queued the way a real VISA
session does. The first test is the failure seen on the bench: one stalled acquisition must
cost one point, not poison every later point of the run with stale, half-averaged traces.
"""

from __future__ import annotations

import pytest
from fake_visa import SimulatedMXA

from iyzee.devices.mxa import KeysightMXA, MXATimeoutError
from iyzee.experiment.procedures import (
    TRACE_SHOT,
    TRACE_SQZ,
    AnalyzerConfig,
    acquire_pair,
    acquire_trace,
    prepare_analyzer,
)


class _Shutter:
    def __init__(self) -> None:
        self.events: list[str] = []

    def open(self) -> None:
        self.events.append("open")

    def close(self) -> None:
        self.events.append("close")


def _connect(sim: SimulatedMXA) -> KeysightMXA:
    class Manager:
        def open_resource(self, address: str) -> SimulatedMXA:
            return sim

    mx = KeysightMXA("10.0.0.1", resource_manager=Manager())
    mx.connect()
    prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), AnalyzerConfig(avg_count=sim.averages))
    return mx


def _first_sample(trace) -> float:
    return float(trace[0])


def test_a_stalled_acquisition_fails_that_point_only_and_leaves_the_session_in_step():
    sim = SimulatedMXA(stalls={2: 45.0})  # the third sweep (point 2's squeezing) stalls 45 s
    mx = _connect(sim)
    shutter = _Shutter()

    first = acquire_pair(mx, shutter)
    assert [_first_sample(first[k]) for k in ("squeezing", "shot_noise")] == [-71.0, -72.0]

    with pytest.raises(MXATimeoutError):
        acquire_pair(mx, shutter)
    assert shutter.events[-1] == "close"  # the shutter is never left open by a failed point
    assert not sim.queue and sim.cleared == 1  # no late reply left to answer the next query

    # The next points are complete (an integer first sample: not "-0.5" half-averaged),
    # and each is its own sweeps, not a stale reply to an earlier question.
    for sweep in (4, 6):
        again = acquire_pair(mx, shutter)
        assert [_first_sample(again[k]) for k in ("squeezing", "shot_noise")] == [
            -70.0 - sweep,
            -70.0 - sweep - 1,
        ]


def test_the_wait_follows_the_configured_sweep_not_a_fixed_30_seconds():
    sim = SimulatedMXA()
    mx = _connect(sim)
    mx.write("SWE:TIME 500ms")  # 150 averages x 0.5 s = 75 s: far over the old fixed limit
    assert mx.sweep_timeout_s() == pytest.approx(3 * 75)
    trace = acquire_trace(mx, TRACE_SQZ)
    assert len(trace) == 1001 and _first_sample(trace) == -71.0


def test_a_whole_frequency_sweep_finishes_despite_an_analyzer_stall_and_a_flaky_wavemeter(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """The bench scenario end to end: every point is measured, none is half-averaged."""
    import requests

    from iyzee.devices import wavemeter as wavemeter_mod
    from iyzee.experiment import runner as runner_mod
    from iyzee.experiment.procedures import build_frequency_sweep
    from iyzee.experiment.runner import SweepRunner

    monkeypatch.setattr(runner_mod, "create_dirs", lambda: tmp_path)
    for module in ("iyzee.experiment.core", "iyzee.devices.wavemeter"):
        monkeypatch.setattr(f"{module}.time.sleep", lambda s: None)
    monkeypatch.setattr(runner_mod, "RETRY_DELAY_S", 0.0)

    sim = SimulatedMXA(stalls={4: 60.0})  # the third point's squeezing sweep stalls
    mx = _connect(sim)

    reads = {"n": 0}

    def flaky_get(url: str, **kwargs: object) -> requests.Response:
        reads["n"] += 1
        if reads["n"] == 2:
            raise requests.Timeout("busy")  # one failed request: retried inside the client
        reply = requests.Response()
        reply.status_code, reply._content = 200, b"377.1052067"
        return reply

    def post(url: str, **kwargs: object) -> requests.Response:
        reply = requests.Response()
        reply.status_code = 200
        return reply

    monkeypatch.setattr(wavemeter_mod.requests, "get", flaky_get)
    monkeypatch.setattr(wavemeter_mod.requests, "post", post)

    sweep = build_frequency_sweep(offsets_thz=[i * 1e-5 for i in range(5)])
    outcome = SweepRunner(
        sweep,
        {"mxa": mx, "shutter": _Shutter(), "wavemeter": wavemeter_mod.Wavemeter("10.0.0.2")},
    ).run()

    assert outcome.record is not None and outcome.record.status == "completed"
    assert len(outcome.collected) == 5
    assert [r.meta.get("attempts") for r in outcome.collected] == [None, None, 2, None, None]
    for result in outcome.collected:
        for trace in result.traces.values():  # an integer first sample: fully averaged
            assert float(trace[0]) == round(float(trace[0]))
    assert not sim.queue
