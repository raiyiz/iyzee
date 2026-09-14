import threading

import pytest

from iyzee.experiment.config import (
    acquire_trace,
    build_instruments,
    select_instruments,
)
from iyzee.tui.instruments import InstrumentSpec


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


def test_select_instruments_preserves_registry_order():
    specs = (
        InstrumentSpec("mxa", "MXA", lambda _: _FakeHandle()),
        InstrumentSpec("scope", "Scope", lambda _: _FakeHandle()),
        InstrumentSpec("wavemeter", "Wavemeter", lambda _: _FakeHandle()),
    )

    selected = select_instruments(specs, ("wavemeter", "mxa"))

    assert [spec.key for spec in selected] == ["mxa", "wavemeter"]


def test_select_instruments_rejects_unknown_key():
    specs = (InstrumentSpec("mxa", "MXA", lambda _: _FakeHandle()),)

    with pytest.raises(KeyError, match="scope"):
        select_instruments(specs, ("mxa", "scope"))


def test_build_instruments_only_builds_required_handles_and_applies_overrides():
    built: list[dict] = []

    def make(settings):
        built.append(dict(settings))
        return _FakeHandle()

    specs = (
        InstrumentSpec("mxa", "MXA", make, {"ip": "default-mxa"}),
        InstrumentSpec("scope", "Scope", make, {"ip": "default-scope"}),
    )

    handles = build_instruments(
        specs,
        ("mxa",),
        {"mxa": {"ip": "local-mxa"}},
    )

    assert list(handles) == ["mxa"]
    assert built == [{"ip": "local-mxa"}]


class _FakeHandle:
    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def probe(self) -> str:
        return "fake"
