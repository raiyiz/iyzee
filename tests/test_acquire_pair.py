"""Behavioral tests for the shared squeezing/shot-noise acquisition sequence."""

import pytest

import iyzee.experiment.procedures as procedures


class FakeShutter:
    def __init__(self, events):
        self.events = events

    def open(self):
        self.events.append("open")

    def close(self):
        self.events.append("close")


def test_acquire_pair_closes_shutter_before_shot_noise(monkeypatch):
    events = []

    def acquire_trace(_mx, trace):
        events.append(f"trace {trace}")
        return [trace]

    monkeypatch.setattr(procedures, "acquire_trace", acquire_trace)

    result = procedures.acquire_pair(object(), FakeShutter(events))

    assert events == ["open", "trace 1", "close", "trace 2"]
    assert result == {"squeezing": [1], "shot_noise": [2]}


def test_acquire_pair_does_not_touch_shutter_when_omitted(monkeypatch):
    events = []

    def acquire_trace(_mx, trace):
        events.append(trace)
        return [trace]

    monkeypatch.setattr(procedures, "acquire_trace", acquire_trace)

    result = procedures.acquire_pair(object())

    assert events == [1, 2]
    assert result == {"squeezing": [1], "shot_noise": [2]}


def test_acquire_pair_closes_shutter_if_squeezing_acquisition_fails(monkeypatch):
    events = []

    def acquire_trace(_mx, trace):
        events.append(f"trace {trace}")
        raise RuntimeError("MXA failed")

    monkeypatch.setattr(procedures, "acquire_trace", acquire_trace)

    with pytest.raises(RuntimeError, match="MXA failed"):
        procedures.acquire_pair(object(), FakeShutter(events))

    assert events == ["open", "trace 1", "close"]
