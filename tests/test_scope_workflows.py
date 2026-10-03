from __future__ import annotations

import json
import threading
from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from helpers import FakeSocket

from iyzee.scope import Channel, Coupling, TriggerCoupling, TriggerMode, TriggerSlope
from iyzee.scope_workflows import (
    ChannelSettings,
    SettingAdjustment,
    TriggerSettings,
    acquire_scope_recording,
    apply_and_verify_channel_settings,
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

    def set_time_per_div(self, seconds):
        self.calls.append(("set_time_per_div", seconds))
        self.trigger_state = replace(self.trigger_state, time_per_div=seconds)

    def get_time_per_div(self):
        self.calls.append(("get_time_per_div",))
        return f"TDIV {self.trigger_state.time_per_div or 1e-6:.2E} S"

    def get_trigger_level(self, source):
        self.calls.append(("get_trigger_level", source))
        return f"{source}:TRIG_LEVEL {self.trigger_state.level_volts:.2E}V"

    def getHorProperties(self, channel):
        self.calls.append(("getHorProperties", channel))
        if self._fail_hor:
            raise RuntimeError("scope did not respond")
        return ("S", 0.0, 1e-6)

    def getDataFloatsDetailed(self, channel, block="DAT1"):
        self.calls.append(("getDataFloatsDetailed", channel))
        if channel in self._fail_channels:
            raise RuntimeError(f"{channel} refused to send data")
        values = self._data[channel]
        return {
            "unit": "V",
            "values": np.asarray(values, dtype=np.float64),
            "raw_codes": np.asarray([10 + i for i in range(len(values))], dtype=np.int16),
            "vertical_gain": 0.25,
            "vertical_offset": 0.5,
        }


def _settings(channel: Channel, **overrides) -> ChannelSettings:
    defaults: dict[str, Any] = dict(
        channel=channel, enabled=True, volts_per_div=0.5, offset=0.0, coupling=Coupling.DC_1M
    )
    defaults.update(overrides)
    return ChannelSettings(**defaults)


def test_acquire_scope_recording_retains_calibration_and_statistics():
    scope = FakeScope()
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
    scope = FakeScope()
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
    assert manifest["schema_version"] == 2
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


# -- selective channel apply ---------------------------------------------------------------


def test_apply_channel_settings_only_writes_changed_fields_against_baseline():
    scope = FakeScope()
    baseline = [_settings(Channel.C1)]

    errors = apply_channel_settings(scope, [_settings(Channel.C1)], current_settings=baseline)
    assert errors == [] and scope.calls == []  # nothing changed: nothing written

    errors = apply_channel_settings(
        scope, [_settings(Channel.C1, offset=0.2)], current_settings=baseline
    )
    assert errors == []
    assert scope.calls == [("set_offset", Channel.C1, 0.2)]


def test_apply_channel_settings_refuses_unsynchronized_channel():
    scope = FakeScope()
    baseline = [_settings(Channel.C1)]
    desired = [_settings(Channel.C2, offset=0.2)]

    errors = apply_channel_settings(scope, desired, current_settings=baseline)

    assert [error.channel for error in errors] == [Channel.C2]
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
        ("set_trigger_source", Channel.C2),
        ("set_trigger_slope", Channel.C2, TriggerSlope.POSITIVE),
        ("set_trigger_coupling", Channel.C2, TriggerCoupling.DC),
        ("set_trigger_level", Channel.C2, 0.1),
        ("set_trigger_mode", TriggerMode.AUTO),  # mode last
    ]


def test_trigger_settings_raise_rather_than_collecting_errors():
    """Unlike the channel batch, there is one trigger, so nothing can partially
    succeed: a failure on either side just raises."""
    settings = TriggerSettings(
        source=Channel.C1,
        mode=TriggerMode.AUTO,
        slope=TriggerSlope.POSITIVE,
        coupling=TriggerCoupling.DC,
        level_volts=0.0,
    )
    scope = FakeScope()
    scope.set_trigger_mode = lambda mode: (_ for _ in ()).throw(RuntimeError("nope"))
    with pytest.raises(RuntimeError, match="nope"):
        apply_trigger_settings(scope, settings)

    scope = FakeScope()
    scope.get_trigger_mode = lambda: (_ for _ in ()).throw(RuntimeError("nope"))
    with pytest.raises(RuntimeError, match="nope"):
        read_trigger_settings(scope)


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


def test_read_trigger_settings_reads_every_field_off_the_armed_source():
    scope = FakeScope()
    scope.trigger_state = TriggerSettings(
        source=Channel.C2,
        mode=TriggerMode.NORMAL,
        slope=TriggerSlope.NEGATIVE,
        coupling=TriggerCoupling.AC,
        level_volts=-0.3,
        time_per_div=2e-6,
    )

    settings = read_trigger_settings(scope)

    assert settings == scope.trigger_state
    # Slope/coupling/level were all queried off the source TRIG_SELECT?
    # reported, C2 — not the default C1.
    assert ("get_trigger_slope", Channel.C2) in scope.calls
    assert ("get_trigger_level", Channel.C2) in scope.calls


# -- a failed timebase read is reported against every channel that needed it ----------------


def test_a_failed_timebase_read_fails_every_channel_that_needs_it():
    recording = acquire_scope_recording(FakeScope(fail_hor=True), [Channel.C1, Channel.C2])

    assert recording.waveforms == ()
    assert {e.channel for e in recording.errors} == {Channel.C1, Channel.C2}


# -- link loss: stop instead of reading a late reply as the next channel's answer ---------


class DroppingScope(FakeScope):
    """Behaves like the real driver: a failure on ``fail_on`` invalidates the
    connection, after which ``connected`` is False."""

    def __init__(self, fail_on: Channel, **kwargs):
        super().__init__(
            fail_channels=frozenset({fail_on}), fail_reads=frozenset({fail_on}), **kwargs
        )
        self.connected = True

    def _drop(self, message: str):
        self.connected = False
        raise TimeoutError(message)

    def _check_readable(self, channel):
        if channel in self._fail_reads:
            self._drop(f"{channel} timed out")

    def set_volts_per_div(self, channel, value):
        self.calls.append(("set_volts_per_div", channel, value))
        if channel in self._fail_channels:
            self._drop(f"{channel} timed out")

    def getDataFloatsDetailed(self, channel, block="DAT1"):
        if channel in self._fail_channels:
            self.calls.append(("getDataFloatsDetailed", channel))
            self._drop(f"{channel} timed out")
        return super().getDataFloatsDetailed(channel, block)


def test_read_channel_settings_stops_once_the_connection_is_lost():
    scope = DroppingScope(Channel.C2)

    settings, errors = read_channel_settings(
        scope, [Channel.C1, Channel.C2, Channel.C3, Channel.C4]
    )

    assert [s.channel for s in settings] == [Channel.C1]
    assert [e.channel for e in errors] == [Channel.C2, Channel.C3, Channel.C4]
    assert isinstance(errors[0].error, TimeoutError)
    assert all(isinstance(e.error, ConnectionError) for e in errors[1:])
    queried = {call[1] for call in scope.calls if len(call) > 1}
    assert queried == {Channel.C1, Channel.C2}, "C3/C4 must never be queried on a dead link"


def test_apply_channel_settings_stops_once_the_connection_is_lost():
    scope = DroppingScope(Channel.C1)
    desired = [_settings(c, volts_per_div=1.0) for c in (Channel.C1, Channel.C2, Channel.C3)]

    errors = apply_channel_settings(scope, desired)

    assert [e.channel for e in errors] == [Channel.C1, Channel.C2, Channel.C3]
    assert [c for c in scope.calls if c[0].startswith("set_")] == [
        ("set_volts_per_div", Channel.C1, 1.0)
    ]


def test_acquire_scope_recording_stops_once_the_connection_is_lost():
    scope = DroppingScope(Channel.C2)

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2, Channel.C3])

    assert [w.channel for w in recording.waveforms] == [Channel.C1]
    assert [e.channel for e in recording.errors] == [Channel.C2, Channel.C3]
    assert not any(c == ("getDataFloatsDetailed", Channel.C3) for c in scope.calls)


def test_a_parse_error_on_a_healthy_link_does_not_stop_the_batch():
    """Only a lost connection aborts; a bad reply from one channel doesn't."""
    scope = FakeScope(fail_reads=frozenset({Channel.C1}))

    settings, errors = read_channel_settings(scope, [Channel.C1, Channel.C2])

    assert [s.channel for s in settings] == [Channel.C2]
    assert [e.channel for e in errors] == [Channel.C1]


# -- locking: one lock, no deadlock through the console proxy ------------------------------


def test_workflow_with_handle_lock_through_the_console_proxy_does_not_deadlock():
    """The console's ``lab.scope`` is a LockedProxy over the handle's lock;
    passing that same lock to a workflow used to hang forever."""
    from iyzee.tui.instruments import LockedProxy, ScopeHandle

    handle = ScopeHandle()
    assert handle.lock is handle.scope.transaction_lock  # one lock, owned by the driver
    sock = FakeSocket()
    handle.scope._transport.attach_socket(sock)
    proxy = LockedProxy(handle.scope, handle.lock)
    outcome: list[object] = []

    def run() -> None:
        outcome.append(
            apply_channel_settings(
                proxy, [_settings(Channel.C1, volts_per_div=1.0)], lock=handle.lock
            )
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=3.0)

    assert not worker.is_alive(), "deadlocked: handle lock re-entered through the proxy"
    assert outcome == [[]]
    assert b"C1:VOLT_DIV 1.0" in bytes(sock.sent)


def test_batch_holds_the_driver_lock_without_an_explicit_lock():
    events: list[bool] = []
    lock = threading.Lock()

    class LockedScope(FakeScope):
        transaction_lock = lock

        def set_offset(self, channel, value):
            events.append(lock.locked())

    scope = LockedScope()
    apply_channel_settings(scope, [_settings(Channel.C1)])

    assert events == [True] and not lock.locked()


# -- trigger: mode is written last on the selective path too --------------------------------


# -- apply + read-back verification ---------------------------------------------------------


class StatefulScope(FakeScope):
    """A scope that remembers what it was told, like a real one: values are
    quantized to ``vdiv_step`` and (optionally) some writes are ignored."""

    def __init__(self, *, vdiv_step: float = 0.0, ignore_coupling=frozenset(), **kwargs):
        super().__init__(**kwargs)
        self.vdiv_step = vdiv_step
        self.ignore_coupling = ignore_coupling

    def _update(self, channel, **changes):
        from dataclasses import replace

        self.channel_state[channel] = replace(self.channel_state[channel], **changes)

    def set_volts_per_div(self, channel, value):
        super().set_volts_per_div(channel, value)
        if self.vdiv_step:
            value = round(value / self.vdiv_step) * self.vdiv_step
        self._update(channel, volts_per_div=value)

    def set_offset(self, channel, value):
        super().set_offset(channel, value)
        self._update(channel, offset=value)

    def set_coupling(self, channel, value):
        super().set_coupling(channel, value)
        if channel not in self.ignore_coupling:
            self._update(channel, coupling=value)

    def set_trace_display(self, channel, value):
        super().set_trace_display(channel, value)
        self._update(channel, enabled=value)


def test_apply_and_verify_returns_what_the_scope_reports_not_what_was_requested():
    scope = StatefulScope(vdiv_step=0.1)

    result = apply_and_verify_channel_settings(
        scope, [_settings(Channel.C1, volts_per_div=0.123, offset=0.25)]
    )

    assert result.errors == ()
    (verified,) = result.verified
    assert verified.volts_per_div == pytest.approx(0.1)
    assert verified.offset == pytest.approx(0.25)
    assert result.adjustments == (
        SettingAdjustment(
            Channel.C1, "volts_per_div", requested=0.123, actual=verified.volts_per_div
        ),
    )


def test_apply_and_verify_exact_match_has_no_adjustments():
    scope = StatefulScope()

    result = apply_and_verify_channel_settings(scope, [_settings(Channel.C2, volts_per_div=0.2)])

    assert result.errors == () and result.adjustments == ()
    assert result.verified[0].volts_per_div == pytest.approx(0.2)


def test_apply_and_verify_reports_an_ignored_discrete_setting_as_an_error():
    scope = StatefulScope(ignore_coupling=frozenset({Channel.C1}))

    result = apply_and_verify_channel_settings(
        scope, [_settings(Channel.C1, coupling=Coupling.DC_50)]
    )

    assert [e.channel for e in result.errors] == [Channel.C1]
    assert "coupling is D1M but D50 was requested" in str(result.errors[0].error)
    assert result.verified[0].coupling == Coupling.DC_1M, "baseline must be the truth"


def test_apply_and_verify_reports_a_channel_that_cannot_be_read_back():
    scope = StatefulScope(fail_reads=frozenset({Channel.C2}))

    result = apply_and_verify_channel_settings(
        scope, [_settings(Channel.C1), _settings(Channel.C2, volts_per_div=1.0)]
    )

    assert [s.channel for s in result.verified] == [Channel.C1]
    assert [e.channel for e in result.errors] == [Channel.C2]
    assert "could not verify C2" in str(result.errors[0].error)


def test_apply_and_verify_still_reads_back_a_channel_whose_write_failed():
    """After a failed write the scope's real state is unknown, so it is read,
    and the baseline becomes the truth (the old value), not the request."""
    scope = StatefulScope(fail_channels=frozenset({Channel.C1}))
    before = scope.channel_state[Channel.C1].volts_per_div

    result = apply_and_verify_channel_settings(scope, [_settings(Channel.C1, volts_per_div=2.0)])

    assert Channel.C1 in [e.channel for e in result.errors]
    assert result.verified[0].volts_per_div == pytest.approx(before)
    assert before != 2.0


def test_apply_and_verify_skips_the_read_back_when_the_link_is_lost():
    scope = DroppingScope(Channel.C1)

    result = apply_and_verify_channel_settings(
        scope, [_settings(Channel.C1, volts_per_div=1.0), _settings(Channel.C2, volts_per_div=1.0)]
    )

    assert result.verified == ()
    assert [e.channel for e in result.errors] == [Channel.C1, Channel.C2]
    assert not any(c[0].startswith("get_") for c in scope.calls)


def test_apply_and_verify_holds_the_lock_across_write_and_read_back():
    scope = StatefulScope()
    lock = threading.Lock()
    held: list[bool] = []
    original = scope.get_volts_per_div
    scope.get_volts_per_div = lambda ch: (held.append(lock.locked()), original(ch))[1]

    apply_and_verify_channel_settings(scope, [_settings(Channel.C1)], lock=lock)

    assert held == [True] and not lock.locked()


# -- acquisition: consistency and provenance -----------------------------------------------


class TimebaseScope(FakeScope):
    INTERVALS = {Channel.C1: 1e-6, Channel.C2: 5e-6}

    def getHorProperties(self, channel):
        self.calls.append(("getHorProperties", channel))
        return ("S", 0.0, self.INTERVALS[channel])


def test_each_channel_gets_a_time_axis_from_its_own_timebase():
    scope = TimebaseScope()

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2])

    c1, c2 = recording.waveforms
    assert c1.time_interval == 1e-6 and c2.time_interval == 5e-6
    np.testing.assert_allclose(c2.time, [0.0, 5e-6, 10e-6])


def test_one_channels_timebase_failure_no_longer_discards_the_others():
    class FlakyTimebase(FakeScope):
        def getHorProperties(self, channel):
            if channel == Channel.C2:
                raise RuntimeError("no timebase for C2")
            return super().getHorProperties(channel)

    recording = acquire_scope_recording(FlakyTimebase(), [Channel.C1, Channel.C2, Channel.C3])

    assert [w.channel for w in recording.waveforms] == [Channel.C1, Channel.C3]
    assert [e.channel for e in recording.errors] == [Channel.C2]


def test_empty_waveform_is_an_error_not_a_silent_success():
    class Empty(FakeScope):
        def getDataFloatsDetailed(self, channel, block="DAT1"):
            self.calls.append(("getDataFloatsDetailed", channel))
            return {
                "unit": "V",
                "values": [],
                "raw_codes": [],
                "vertical_gain": 1.0,
                "vertical_offset": 0.0,
            }

    recording = acquire_scope_recording(Empty(), [Channel.C1])

    assert recording.waveforms == ()
    assert "empty waveform" in str(recording.errors[0].error)


def _mode_calls(scope):
    return [c for c in scope.calls if c[0] == "set_trigger_mode"]


@pytest.mark.parametrize("mode", [TriggerMode.AUTO, TriggerMode.NORMAL])
def test_a_running_acquisition_is_stopped_for_the_download_and_restored(mode):
    scope = FakeScope()
    scope.trigger_state = TriggerSettings(
        Channel.C1, mode, TriggerSlope.POSITIVE, TriggerCoupling.DC, 0.0
    )

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2])

    assert _mode_calls(scope) == [
        ("set_trigger_mode", TriggerMode.STOP),
        ("set_trigger_mode", mode),
    ]
    names = [c[0] for c in scope.calls]
    assert names.index("set_trigger_mode") < names.index("getDataFloatsDetailed")
    assert names[::-1].index("set_trigger_mode") < names[::-1].index(
        "getDataFloatsDetailed"
    )  # restore is last
    assert recording.frozen is True and recording.prior_trigger_mode == mode
    assert recording.warnings == ()


@pytest.mark.parametrize("mode", [TriggerMode.SINGLE, TriggerMode.STOP])
def test_a_held_acquisition_is_left_alone(mode):
    scope = FakeScope()
    scope.trigger_state = TriggerSettings(
        Channel.C1, mode, TriggerSlope.POSITIVE, TriggerCoupling.DC, 0.0
    )

    recording = acquire_scope_recording(scope, [Channel.C1])

    assert _mode_calls(scope) == []
    assert recording.frozen is False and recording.prior_trigger_mode == mode


def test_freeze_can_be_disabled():
    scope = FakeScope()

    recording = acquire_scope_recording(scope, [Channel.C1], freeze=False)

    assert _mode_calls(scope) == [] and not any(c[0] == "get_trigger_mode" for c in scope.calls)
    assert recording.frozen is False


def test_trigger_mode_is_restored_even_if_a_channel_fails():
    scope = FakeScope(fail_channels=frozenset({Channel.C1}))

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2])

    assert _mode_calls(scope)[-1] == ("set_trigger_mode", TriggerMode.AUTO)
    assert [e.channel for e in recording.errors] == [Channel.C1]
    assert [w.channel for w in recording.waveforms] == [Channel.C2]


def test_failure_to_freeze_is_a_warning_and_the_download_still_happens():
    scope = FakeScope()

    def boom(mode):
        raise RuntimeError("refused")

    scope.set_trigger_mode = boom

    recording = acquire_scope_recording(scope, [Channel.C1])

    assert recording.frozen is False
    assert [w.channel for w in recording.waveforms] == [Channel.C1]
    assert any("could not freeze" in w and "refused" in w for w in recording.warnings)


def test_failure_to_restore_is_reported_as_a_warning():
    scope = FakeScope()
    calls = []

    def flaky(mode):
        calls.append(mode)
        if len(calls) == 2:
            raise RuntimeError("restore refused")

    scope.set_trigger_mode = flaky

    recording = acquire_scope_recording(scope, [Channel.C1])

    assert recording.frozen is True
    assert any("could not restore trigger mode AUTO" in w for w in recording.warnings)


def test_a_lost_link_while_frozen_is_reported_not_papered_over():
    scope = DroppingScope(Channel.C1)

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2])

    assert recording.frozen is True
    assert _mode_calls(scope) == [("set_trigger_mode", TriggerMode.STOP)], (
        "no restore on a dead link"
    )
    assert any("connection lost while frozen" in w for w in recording.warnings)
    assert [e.channel for e in recording.errors] == [Channel.C1, Channel.C2]


def test_identity_is_recorded_and_failure_to_read_it_is_a_warning():
    scope = FakeScope()
    scope.query = lambda command: "LECROY,WS452,LCRY1234,9.0.0"

    recording = acquire_scope_recording(scope, [Channel.C1])

    assert recording.instrument_id == "LECROY,WS452,LCRY1234,9.0.0"

    def dead(command):
        raise TimeoutError("no IDN")

    scope.query = dead
    recording = acquire_scope_recording(scope, [Channel.C1])
    assert recording.instrument_id is None
    assert any("identity unavailable" in w for w in recording.warnings)


def test_manifest_records_provenance_and_the_error_type(tmp_path):
    scope = FakeScope(fail_channels=frozenset({Channel.C2}))
    scope.query = lambda command: "LECROY,WS452,LCRY1234,9.0.0"

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2])
    manifest = json.loads(
        save_scope_acquisition(recording, tmp_path).with_suffix(".json").read_text()
    )

    assert manifest["instrument"]["identity"] == "LECROY,WS452,LCRY1234,9.0.0"
    assert manifest["acquisition"] == {"frozen": True, "prior_trigger_mode": "AUTO", "warnings": []}
    assert manifest["errors"] == [
        {"channel": "C2", "type": "RuntimeError", "error": "C2 refused to send data"}
    ]


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        ("TDIV 5.00E-06 S", 5e-6),
        ("TIME_DIV 2.00E-09S", 2e-9),
        ("TDIV 10 NS".replace("NS", "ns"), 10e-9),
        ("1E-3", 1e-3),
    ],
)
def test_parse_seconds_accepts_the_reply_shapes_the_scope_uses(raw, seconds):
    from iyzee.scope_workflows import _parse_seconds

    assert _parse_seconds(raw) == pytest.approx(seconds)


def test_parse_seconds_rejects_garbage():
    from iyzee.scope_workflows import _parse_seconds

    with pytest.raises(ValueError, match="could not parse time"):
        _parse_seconds("TDIV ???")


def test_apply_trigger_settings_writes_time_per_div_only_when_it_changed():
    scope = FakeScope()
    baseline = read_trigger_settings(scope)
    scope.calls.clear()

    apply_trigger_settings(
        scope, replace(baseline, time_per_div=baseline.time_per_div), current_settings=baseline
    )
    assert not any(call[0] == "set_time_per_div" for call in scope.calls)

    apply_trigger_settings(scope, replace(baseline, time_per_div=5e-6), current_settings=baseline)
    assert ("set_time_per_div", 5e-6) in scope.calls
    assert read_trigger_settings(scope).time_per_div == 5e-6
