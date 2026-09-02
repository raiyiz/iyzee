import pytest

from iyzee.experiment.config import acquire_trace


class FakeMXA:
    def __init__(self):
        self.update_states = []

    def set_trace_update(self, trace_num, state):
        self.update_states.append((trace_num, state))

    def single_sweep_wait(self):
        raise RuntimeError("sweep failed")


def test_acquire_trace_disables_trace_after_failure():
    mx = FakeMXA()

    with pytest.raises(RuntimeError, match="sweep failed"):
        acquire_trace(mx, 1)

    assert mx.update_states == [(1, True), (1, False)]
