from __future__ import annotations

import threading

import pytest

from iyzee.scope import Channel, Coupling, TriggerCoupling, TriggerMode, TriggerSlope
from iyzee.scope_workflows import (
    ChannelSettings,
    TriggerSettings,
    acquire_waveforms,
    apply_channel_settings,
    apply_trigger_settings,
)


class FakeScope:
    """Records every call ``scope_workflows`` makes, and can be told to
    fail on specific channels — enough to exercise the workflow logic
    (looping, partial-failure collection, shared-timebase reuse) without a
    real socket or VICP framing (see ``test_scope.py`` for that layer)."""

    def __init__(self, *, fail_channels: frozenset = frozenset(), fail_hor: bool = False):
        self.calls: list[tuple] = []
        self._fail_channels = fail_channels
        self._fail_hor = fail_hor
        self._data = {
            Channel.C1: [1.0, 2.0, 3.0],
            Channel.C2: [4.0, 5.0, 6.0],
            Channel.C3: [7.0, 8.0],
            Channel.C4: [9.0, 10.0],
        }

    def set_volts_per_div(self, channel, value):
        self.calls.append(("set_volts_per_div", channel, value))
        if channel in self._fail_channels:
            raise RuntimeError(f"{channel} refused volts/div")

    def set_offset(self, channel, value):
        self.calls.append(("set_offset", channel, value))

    def set_coupling(self, channel, value):
        self.calls.append(("set_coupling", channel, value))

    def set_trace_display(self, channel, value):
        self.calls.append(("set_trace_display", channel, value))

    def set_trigger_mode(self, mode):
        self.calls.append(("set_trigger_mode", mode))

    def set_trigger_source(self, source):
        self.calls.append(("set_trigger_source", source))

    def set_trigger_slope(self, source, slope):
        self.calls.append(("set_trigger_slope", source, slope))

    def set_trigger_coupling(self, source, coupling):
        self.calls.append(("set_trigger_coupling", source, coupling))

    def set_trigger_level(self, source, level):
        self.calls.append(("set_trigger_level", source, level))

    def getHorProperties(self, channel):
        self.calls.append(("getHorProperties", channel))
        if self._fail_hor:
            raise RuntimeError("scope did not respond")
        return ("S", 0.0, 1e-6)

    def getDataFloats(self, channel):
        self.calls.append(("getDataFloats", channel))
        if channel in self._fail_channels:
            raise RuntimeError(f"{channel} refused to send data")
        return ("V", self._data[channel])


def _settings(channel: Channel, **overrides) -> ChannelSettings:
    defaults = dict(
        channel=channel, enabled=True, volts_per_div=0.5, offset=0.0, coupling=Coupling.DC_1M
    )
    defaults.update(overrides)
    return ChannelSettings(**defaults)


# -- apply_channel_settings ---------------------------------------------------------------


def test_apply_channel_settings_pushes_every_field_per_channel():
    scope = FakeScope()
    settings = [_settings(Channel.C1, volts_per_div=1.0, offset=0.2, enabled=False)]

    errors = apply_channel_settings(scope, settings)

    assert errors == []
    assert scope.calls == [
        ("set_volts_per_div", Channel.C1, 1.0),
        ("set_offset", Channel.C1, 0.2),
        ("set_coupling", Channel.C1, Coupling.DC_1M),
        ("set_trace_display", Channel.C1, False),
    ]


def test_apply_channel_settings_continues_past_one_channels_failure():
    scope = FakeScope(fail_channels=frozenset({Channel.C1}))
    settings = [_settings(Channel.C1), _settings(Channel.C2)]

    errors = apply_channel_settings(scope, settings)

    assert [e.channel for e in errors] == [Channel.C1]
    assert isinstance(errors[0].error, RuntimeError)
    # C2's settings still went out, even though C1 failed first.
    assert ("set_offset", Channel.C2, 0.0) in scope.calls


def test_apply_channel_settings_holds_one_lock_for_the_whole_batch():
    scope = FakeScope()
    lock = threading.Lock()
    settings = [_settings(Channel.C1), _settings(Channel.C2)]
    lock_state_during_calls = []
    real_set_offset = scope.set_offset

    def spy_set_offset(channel, value):
        lock_state_during_calls.append(lock.locked())
        real_set_offset(channel, value)

    scope.set_offset = spy_set_offset

    apply_channel_settings(scope, settings, lock=lock)

    assert lock_state_during_calls == [True, True], "lock must stay held across both channels"
    assert not lock.locked(), "lock must be released once the batch finishes"


def test_apply_channel_settings_works_with_no_lock_at_all():
    """A script with its own private scope shouldn't need to construct a
    throwaway lock just to call this."""
    errors = apply_channel_settings(FakeScope(), [_settings(Channel.C1)])
    assert errors == []


# -- apply_trigger_settings ----------------------------------------------------------------


def test_apply_trigger_settings_pushes_every_field_in_order():
    scope = FakeScope()
    settings = TriggerSettings(
        source=Channel.C2,
        mode=TriggerMode.AUTO,
        slope=TriggerSlope.POSITIVE,
        coupling=TriggerCoupling.DC,
        level_volts=0.1,
    )

    apply_trigger_settings(scope, settings)

    assert scope.calls == [
        ("set_trigger_mode", TriggerMode.AUTO),
        ("set_trigger_source", Channel.C2),
        ("set_trigger_slope", Channel.C2, TriggerSlope.POSITIVE),
        ("set_trigger_coupling", Channel.C2, TriggerCoupling.DC),
        ("set_trigger_level", Channel.C2, 0.1),
    ]


def test_apply_trigger_settings_raises_rather_than_collecting_errors():
    """Unlike apply_channel_settings, there's only one trigger — nothing
    to partially succeed, so a failure just raises."""
    scope = FakeScope()
    scope.set_trigger_mode = lambda mode: (_ for _ in ()).throw(RuntimeError("nope"))
    settings = TriggerSettings(
        source=Channel.C1,
        mode=TriggerMode.AUTO,
        slope=TriggerSlope.POSITIVE,
        coupling=TriggerCoupling.DC,
        level_volts=0.0,
    )

    with pytest.raises(RuntimeError, match="nope"):
        apply_trigger_settings(scope, settings)


# -- acquire_waveforms ----------------------------------------------------------------------


def test_acquire_waveforms_reads_the_timebase_once_not_per_channel():
    scope = FakeScope()

    series, errors = acquire_waveforms(scope, [Channel.C1, Channel.C2])

    assert errors == []
    assert [c for c in scope.calls if c[0] == "getHorProperties"] == [
        ("getHorProperties", Channel.C1)
    ]
    assert [label for _x, _y, label in series] == ["C1", "C2"]


def test_acquire_waveforms_builds_the_time_axis_from_the_shared_timebase():
    scope = FakeScope()

    series, _errors = acquire_waveforms(scope, [Channel.C1])

    times, values, label = series[0]
    assert label == "C1"
    assert values == [1.0, 2.0, 3.0]
    assert times == [0.0, 1e-6, 2e-6]


def test_acquire_waveforms_reports_every_channel_failed_if_the_timebase_read_fails():
    scope = FakeScope(fail_hor=True)

    series, errors = acquire_waveforms(scope, [Channel.C1, Channel.C2])

    assert series == []
    assert {e.channel for e in errors} == {Channel.C1, Channel.C2}


def test_acquire_waveforms_continues_past_one_channels_failure():
    scope = FakeScope(fail_channels=frozenset({Channel.C3}))

    series, errors = acquire_waveforms(scope, [Channel.C1, Channel.C3, Channel.C4])

    assert [label for _x, _y, label in series] == ["C1", "C4"]
    assert [e.channel for e in errors] == [Channel.C3]
