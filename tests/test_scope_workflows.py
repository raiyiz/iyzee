from __future__ import annotations

import json
import threading

import numpy as np
import pytest

from iyzee.scope import Channel, Coupling, TriggerCoupling, TriggerMode, TriggerSlope
from iyzee.scope_workflows import (
    ChannelSettings,
    TriggerSettings,
    acquire_scope_recording,
    acquire_waveforms,
    apply_channel_settings,
    apply_trigger_settings,
    read_channel_settings,
    read_trigger_settings,
    save_scope_acquisition,
)


class FakeScope:
    """Records every call ``scope_workflows`` makes, and can be told to
    fail on specific channels — enough to exercise the workflow logic
    (looping, partial-failure collection, shared-timebase reuse) without a
    real socket or VICP framing (see ``test_scope.py`` for that layer)."""

    def __init__(
        self,
        *,
        fail_channels: frozenset = frozenset(),
        fail_hor: bool = False,
        fail_reads: frozenset = frozenset(),
    ):
        self.calls: list[tuple] = []
        self._fail_channels = fail_channels
        self._fail_hor = fail_hor
        self._fail_reads = fail_reads
        self._data = {
            Channel.C1: [1.0, 2.0, 3.0],
            Channel.C2: [4.0, 5.0, 6.0],
            Channel.C3: [7.0, 8.0],
            Channel.C4: [9.0, 10.0],
        }
        # What read_channel_settings()/read_trigger_settings() see on the
        # "instrument" — set_* calls don't update this; tests set it
        # directly to control what a read returns.
        self.channel_state: dict[Channel, ChannelSettings] = {
            channel: _settings(channel) for channel in Channel
        }
        self.trigger_state = TriggerSettings(
            source=Channel.C1,
            mode=TriggerMode.AUTO,
            slope=TriggerSlope.POSITIVE,
            coupling=TriggerCoupling.DC,
            level_volts=0.0,
        )

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

    def _check_readable(self, channel: Channel) -> None:
        if channel in self._fail_reads:
            raise RuntimeError(f"{channel} did not respond to the query")

    def get_volts_per_div(self, channel):
        self.calls.append(("get_volts_per_div", channel))
        self._check_readable(channel)
        return f"{channel}:VOLT_DIV {self.channel_state[channel].volts_per_div:.2E}V"

    def get_offset(self, channel):
        self.calls.append(("get_offset", channel))
        self._check_readable(channel)
        return f"{channel}:OFFSET {self.channel_state[channel].offset:.2E}V"

    def get_coupling(self, channel):
        self.calls.append(("get_coupling", channel))
        self._check_readable(channel)
        return f"{channel}:COUPLING {self.channel_state[channel].coupling.value}"

    def get_trace_display(self, channel):
        self.calls.append(("get_trace_display", channel))
        self._check_readable(channel)
        state = "ON" if self.channel_state[channel].enabled else "OFF"
        return f"{channel}:TRACE {state}"

    def get_trigger_source(self):
        self.calls.append(("get_trigger_source",))
        return f"TRIG_SELECT EDGE,SR,{self.trigger_state.source},HT,OFF"

    def get_trigger_mode(self):
        self.calls.append(("get_trigger_mode",))
        return f"TRIG_MODE {self.trigger_state.mode.value}"

    def get_trigger_slope(self, source):
        self.calls.append(("get_trigger_slope", source))
        return f"{source}:TRIG_SLOPE {self.trigger_state.slope.value}"

    def get_trigger_coupling(self, source):
        self.calls.append(("get_trigger_coupling", source))
        return f"{source}:TRIG_COUPLING {self.trigger_state.coupling.value}"

    def get_trigger_level(self, source):
        self.calls.append(("get_trigger_level", source))
        return f"{source}:TRIG_LEVEL {self.trigger_state.level_volts:.2E}V"

    def getHorProperties(self, channel):
        self.calls.append(("getHorProperties", channel))
        if self._fail_hor:
            raise RuntimeError("scope did not respond")
        return ("S", 0.0, 1e-6)

    def getDataFloats(self, channel, block="DAT1"):
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


class DetailedFakeScope(FakeScope):
    def getDataFloatsDetailed(self, channel, block):
        unit, values = self.getDataFloats(channel)
        raw = np.asarray([10 + i for i in range(len(values))], dtype=np.int16)
        return {
            "unit": unit,
            "values": np.asarray(values, dtype=np.float64),
            "raw_codes": raw,
            "vertical_gain": 0.25,
            "vertical_offset": 0.5,
        }


def test_acquire_scope_recording_retains_calibration_and_statistics():
    scope = DetailedFakeScope()
    settings = (_settings(Channel.C1), _settings(Channel.C2))
    trigger = TriggerSettings(
        source=Channel.C1,
        mode=TriggerMode.SINGLE,
        slope=TriggerSlope.POSITIVE,
        coupling=TriggerCoupling.DC,
        level_volts=0.1,
    )

    recording = acquire_scope_recording(
        scope,
        [Channel.C1, Channel.C2],
        channel_settings=settings,
        trigger_settings=trigger,
        applied_channel_settings=settings,
        applied_trigger_settings=trigger,
    )

    assert recording.errors == ()
    assert recording.requested_channel_settings == settings
    assert recording.requested_trigger_settings == trigger
    assert recording.applied_channel_settings == settings
    assert recording.applied_trigger_settings == trigger
    waveform = recording.waveforms[0]
    np.testing.assert_array_equal(waveform.raw_codes, [10, 11, 12])
    np.testing.assert_allclose(waveform.time, [0.0, 1e-6, 2e-6])
    assert waveform.vertical_gain == 0.25
    assert waveform.vertical_offset == 0.5
    assert waveform.stats["max"] == 3.0
    assert waveform.stats["max_index"] == 2
    assert waveform.stats["max_time"] == pytest.approx(2e-6)
    assert waveform.stats["min"] == 1.0
    assert waveform.stats["peak_to_peak"] == 2.0


def test_save_scope_acquisition_writes_data_manifest_checksum_and_stats(tmp_path):
    scope = DetailedFakeScope()
    recording = acquire_scope_recording(
        scope, [Channel.C1], channel_settings=(_settings(Channel.C1),)
    )

    path = save_scope_acquisition(recording, tmp_path)
    with np.load(path, allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive["raw_C1"], [10, 11, 12])
        np.testing.assert_allclose(archive["value_C1"], [1.0, 2.0, 3.0])
        np.testing.assert_allclose(archive["time_C1"], [0.0, 1e-6, 2e-6])
    manifest = json.loads(path.with_suffix(".json").read_text())
    assert manifest["kind"] == "scope-acquisition"
    assert manifest["schema_version"] == 1
    assert manifest["measurement_id"] == recording.measurement_id
    assert manifest["waveforms"][0]["stats"]["max"] == 3.0
    assert manifest["configuration"]["applied_channel_settings"] is None
    assert len(manifest["data_sha256"]) == 64


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


# -- selective channel apply ---------------------------------------------------------------

def test_apply_channel_settings_only_writes_changed_fields_against_baseline():
    scope = FakeScope()
    baseline = [_settings(Channel.C1)]
    desired = [_settings(Channel.C1, offset=0.2)]

    errors = apply_channel_settings(scope, desired, current_settings=baseline)

    assert errors == []
    assert scope.calls == [("set_offset", Channel.C1, 0.2)]


def test_apply_channel_settings_refuses_unsynchronized_channel():
    scope = FakeScope()
    baseline = [_settings(Channel.C1)]
    desired = [_settings(Channel.C2, offset=0.2)]

    errors = apply_channel_settings(scope, desired, current_settings=baseline)

    assert [error.channel for error in errors] == [Channel.C2]
    assert scope.calls == []


def test_apply_channel_settings_does_nothing_when_form_matches_baseline():
    scope = FakeScope()
    baseline = [_settings(Channel.C1)]

    errors = apply_channel_settings(scope, [_settings(Channel.C1)], current_settings=baseline)

    assert errors == []
    assert scope.calls == []


def test_read_channel_settings_accepts_space_separated_voltage_units():
    scope = FakeScope()
    scope.get_volts_per_div = lambda channel: f"{channel}:VOLT_DIV 200E-3 V"
    scope.get_offset = lambda channel: f"{channel}:OFFSET -500mV"
    settings, errors = read_channel_settings(scope, [Channel.C1])

    assert errors == []
    assert settings == [ChannelSettings(Channel.C1, True, 0.2, -0.5, Coupling.DC_1M)]


# -- read_channel_settings ----------------------------------------------------------------


def test_read_channel_settings_reads_every_field_per_channel():
    scope = FakeScope()
    scope.channel_state[Channel.C1] = _settings(
        Channel.C1, enabled=False, volts_per_div=1.0, offset=-0.25, coupling=Coupling.DC_50
    )

    settings, errors = read_channel_settings(scope, [Channel.C1])

    assert errors == []
    assert settings == [ChannelSettings(Channel.C1, False, 1.0, -0.25, Coupling.DC_50)]


def test_read_channel_settings_continues_past_one_channels_failure():
    scope = FakeScope(fail_reads=frozenset({Channel.C1}))

    settings, errors = read_channel_settings(scope, [Channel.C1, Channel.C2])

    assert [s.channel for s in settings] == [Channel.C2]
    assert [e.channel for e in errors] == [Channel.C1]
    assert isinstance(errors[0].error, RuntimeError)


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


# -- selective trigger apply ----------------------------------------------------------------

def test_apply_trigger_settings_only_writes_changed_field():
    scope = FakeScope()
    baseline = scope.trigger_state
    desired = TriggerSettings(
        source=Channel.C1,
        mode=TriggerMode.AUTO,
        slope=TriggerSlope.POSITIVE,
        coupling=TriggerCoupling.DC,
        level_volts=0.25,
    )

    apply_trigger_settings(scope, desired, current_settings=baseline)

    assert scope.calls == [("set_trigger_level", Channel.C1, 0.25)]


def test_apply_trigger_settings_changes_source_without_copying_old_source_settings():
    scope = FakeScope()
    baseline = TriggerSettings(
        source=Channel.C1,
        mode=TriggerMode.NORMAL,
        slope=TriggerSlope.NEGATIVE,
        coupling=TriggerCoupling.AC,
        level_volts=-0.3,
    )
    desired = TriggerSettings(
        source=Channel.C2,
        mode=TriggerMode.NORMAL,
        slope=TriggerSlope.NEGATIVE,
        coupling=TriggerCoupling.AC,
        level_volts=-0.3,
    )

    apply_trigger_settings(scope, desired, current_settings=baseline)

    assert scope.calls == [("set_trigger_source", Channel.C2)]


def test_apply_trigger_settings_without_baseline_keeps_full_write_behavior():
    scope = FakeScope()
    desired = TriggerSettings(
        source=Channel.C2,
        mode=TriggerMode.SINGLE,
        slope=TriggerSlope.NEGATIVE,
        coupling=TriggerCoupling.AC,
        level_volts=0.1,
    )

    apply_trigger_settings(scope, desired)

    assert scope.calls == [
        ("set_trigger_mode", TriggerMode.SINGLE),
        ("set_trigger_source", Channel.C2),
        ("set_trigger_slope", Channel.C2, TriggerSlope.NEGATIVE),
        ("set_trigger_coupling", Channel.C2, TriggerCoupling.AC),
        ("set_trigger_level", Channel.C2, 0.1),
    ]


# -- read_trigger_settings -----------------------------------------------------------------


def test_read_trigger_settings_reads_every_field_off_the_armed_source():
    scope = FakeScope()
    scope.trigger_state = TriggerSettings(
        source=Channel.C2,
        mode=TriggerMode.NORMAL,
        slope=TriggerSlope.NEGATIVE,
        coupling=TriggerCoupling.AC,
        level_volts=-0.3,
    )

    settings = read_trigger_settings(scope)

    assert settings == scope.trigger_state
    # Slope/coupling/level were all queried off the source TRIG_SELECT?
    # reported, C2 — not the default C1.
    assert ("get_trigger_slope", Channel.C2) in scope.calls
    assert ("get_trigger_level", Channel.C2) in scope.calls


def test_read_trigger_settings_raises_rather_than_collecting_errors():
    """Mirrors apply_trigger_settings: one trigger, nothing to partially
    read."""
    scope = FakeScope()
    scope.get_trigger_mode = lambda: (_ for _ in ()).throw(RuntimeError("nope"))

    with pytest.raises(RuntimeError, match="nope"):
        read_trigger_settings(scope)


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
