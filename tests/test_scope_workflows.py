from __future__ import annotations

import json
import threading
from dataclasses import replace
from typing import Any, ClassVar

import numpy as np
import pytest

from iyzee.devices.scope import Channel, Coupling, TriggerCoupling, TriggerMode, TriggerSlope
from iyzee.scope_workflows import (
    ChannelSettings,
    TriggerSettings,
    acquire_scope_recording,
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
    real VISA resource (see ``test_scope.py`` for that layer)."""

    def __init__(
        self,
        *,
        fail_channels: frozenset = frozenset(),
        fail_hor: bool = False,
        fail_reads: frozenset = frozenset(),
    ):
        self.calls: list[tuple] = []
        self.transaction_lock = threading.RLock()
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

    def query(self, command):
        return "LECROY,FAKE,SN0,0.0"

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


def test_acquiring_and_saving_a_scope_recording(tmp_path):
    # -- acquire scope recording retains calibration and statistics
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

    # -- save scope acquisition writes data manifest checksum and stats
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

    # -- manifest records source
    scope = FakeScope()
    scope.query = lambda command: "LECROY,WS452,LCRY1234,9.0.0"

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2])
    manifest = json.loads(
        save_scope_acquisition(recording, tmp_path).with_suffix(".json").read_text()
    )

    assert manifest["instrument"]["identity"] == "LECROY,WS452,LCRY1234,9.0.0"
    assert manifest["acquisition"] == {"frozen": True, "prior_trigger_mode": "AUTO", "warnings": []}
    assert "errors" not in manifest


# -- apply_channel_settings ---------------------------------------------------------------


# -- selective channel apply ---------------------------------------------------------------


# -- read_channel_settings ----------------------------------------------------------------


# -- apply_trigger_settings ----------------------------------------------------------------


def test_trigger_settings_are_applied_and_read_back():
    # -- apply trigger settings writes every field or only what changed
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

    scope = FakeScope()  # against a baseline: only the level differs
    desired = replace(scope.trigger_state, level_volts=0.25)
    apply_trigger_settings(scope, desired, current_settings=scope.trigger_state)
    assert scope.calls == [("set_trigger_level", scope.trigger_state.source, 0.25)]

    scope = FakeScope()  # a new source does not copy the old source's settings over
    baseline = TriggerSettings(
        Channel.C1, TriggerMode.NORMAL, TriggerSlope.NEGATIVE, TriggerCoupling.AC, -0.3
    )
    apply_trigger_settings(scope, replace(baseline, source=Channel.C2), current_settings=baseline)
    assert scope.calls == [("set_trigger_source", Channel.C2)]

    scope = FakeScope()
    baseline = read_trigger_settings(scope)
    scope.calls.clear()
    apply_trigger_settings(
        scope, replace(baseline, time_per_div=baseline.time_per_div), current_settings=baseline
    )
    assert not any(call[0] == "set_time_per_div" for call in scope.calls)
    apply_trigger_settings(scope, replace(baseline, time_per_div=5e-6), current_settings=baseline)
    assert read_trigger_settings(scope).time_per_div == 5e-6

    # -- read trigger settings reads the armed source and failures raise
    scope = FakeScope()
    scope.trigger_state = TriggerSettings(
        source=Channel.C2,
        mode=TriggerMode.NORMAL,
        slope=TriggerSlope.NEGATIVE,
        coupling=TriggerCoupling.AC,
        level_volts=-0.3,
        time_per_div=2e-6,
    )
    assert read_trigger_settings(scope) == scope.trigger_state
    assert ("get_trigger_slope", Channel.C2) in scope.calls  # off the armed source, not C1

    # One trigger, so nothing can partially succeed: a failure just raises.
    scope.set_trigger_mode = lambda mode: (_ for _ in ()).throw(RuntimeError("nope"))
    with pytest.raises(RuntimeError, match="nope"):
        apply_trigger_settings(scope, scope.trigger_state)
    scope.get_trigger_mode = lambda: (_ for _ in ()).throw(RuntimeError("nope"))
    with pytest.raises(RuntimeError, match="nope"):
        read_trigger_settings(scope)


# -- selective trigger apply ----------------------------------------------------------------


# -- a failed timebase read is reported against every channel that needed it ----------------


# -- link loss: stop instead of reading a late reply as the next channel's answer ---------


# -- locking: one lock, no deadlock through the console proxy ------------------------------


def test_workflow_with_handle_lock_through_the_console_proxy_does_not_deadlock():
    """The console's ``lab.scope`` is a LockedProxy over the handle's lock;
    the workflows take the same (re-entrant) lock again and must not hang."""
    from helpers import FakeResourceManager, FakeVisaResource

    from iyzee.devices.handles import LockedProxy, ScopeHandle
    from iyzee.devices.scope import LeCroy

    handle = ScopeHandle()
    resources = FakeResourceManager(
        FakeVisaResource(["C1:VOLT_DIV 1.0V", "C1:OFFSET 0.0V", "C1:COUPLING D1M", "C1:TRACE ON"])
    )
    handle._scope = LeCroy("10.0.0.1", resource_manager=resources)
    handle.connect()
    assert handle.lock is handle.device.transaction_lock  # one lock, owned by the driver
    proxy = LockedProxy(handle.device, handle.lock)
    outcome: list[object] = []

    def run() -> None:
        outcome.append(apply_channel_settings(proxy, [_settings(Channel.C1, volts_per_div=1.0)]))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=3.0)

    assert not worker.is_alive(), "deadlocked: handle lock re-entered through the proxy"
    assert outcome == [[_settings(Channel.C1, volts_per_div=1.0)]]
    assert "C1:VOLT_DIV 1.0" in resources.resource.written


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


# -- acquisition: consistency and source -----------------------------------------------


class TimebaseScope(FakeScope):
    INTERVALS: ClassVar[dict[Channel, float]] = {Channel.C1: 1e-6, Channel.C2: 5e-6}

    def getHorProperties(self, channel):
        self.calls.append(("getHorProperties", channel))
        return ("S", 0.0, self.INTERVALS[channel])


def test_the_download_gets_each_timebase_freezes_a_running_acquisition_and_fails_safely():
    # -- each channel gets a time axis from its own timebase
    scope = TimebaseScope()

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2])

    c1, c2 = recording.waveforms
    assert c1.time_interval == 1e-6 and c2.time_interval == 5e-6
    np.testing.assert_allclose(c2.time, [0.0, 5e-6, 10e-6])

    # -- a running acquisition is frozen for the download and restored a held one is left alone
    for mode in (TriggerMode.AUTO, TriggerMode.NORMAL):
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
        assert names[::-1].index("set_trigger_mode") < names[::-1].index("getDataFloatsDetailed")
        assert recording.frozen is True and recording.prior_trigger_mode == mode
        assert recording.warnings == ()

    for mode in (TriggerMode.SINGLE, TriggerMode.STOP):
        scope = FakeScope()
        scope.trigger_state = TriggerSettings(
            Channel.C1, mode, TriggerSlope.POSITIVE, TriggerCoupling.DC, 0.0
        )
        recording = acquire_scope_recording(scope, [Channel.C1])
        assert _mode_calls(scope) == []
        assert recording.frozen is False and recording.prior_trigger_mode == mode

    scope = FakeScope()  # and it can be switched off entirely
    recording = acquire_scope_recording(scope, [Channel.C1], freeze=False)
    assert _mode_calls(scope) == [] and not any(c[0] == "get_trigger_mode" for c in scope.calls)
    assert recording.frozen is False

    # -- freeze restore and identity failures are warnings and the download still happens
    scope = FakeScope()

    def boom(mode):
        raise RuntimeError("refused")

    scope.set_trigger_mode = boom
    recording = acquire_scope_recording(scope, [Channel.C1])
    assert recording.frozen is False and [w.channel for w in recording.waveforms] == [Channel.C1]
    assert any("could not freeze" in w and "refused" in w for w in recording.warnings)

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

    scope = FakeScope()
    scope.query = lambda command: "LECROY,WS452,LCRY1234,9.0.0"
    assert (
        acquire_scope_recording(scope, [Channel.C1]).instrument_id == "LECROY,WS452,LCRY1234,9.0.0"
    )

    def dead(command):
        raise TimeoutError("no IDN")

    scope.query = dead
    recording = acquire_scope_recording(scope, [Channel.C1])
    assert recording.instrument_id is None
    assert any("identity unavailable" in w for w in recording.warnings)

    # -- acquisition failures raise and restore the trigger mode
    scope = FakeScope(fail_channels=frozenset({Channel.C1}))
    with pytest.raises(RuntimeError, match="C1 refused"):
        acquire_scope_recording(scope, [Channel.C1, Channel.C2])
    assert _mode_calls(scope)[-1] == ("set_trigger_mode", TriggerMode.AUTO)
    assert ("getDataFloatsDetailed", Channel.C2) not in scope.calls

    with pytest.raises(RuntimeError, match="did not respond"):  # a failed timebase read
        acquire_scope_recording(FakeScope(fail_hor=True), [Channel.C1, Channel.C2])

    class Empty(FakeScope):
        def getDataFloatsDetailed(self, channel, block="DAT1"):
            return {
                "unit": "V",
                "values": [],
                "raw_codes": [],
                "vertical_gain": 1.0,
                "vertical_offset": 0.0,
            }

    with pytest.raises(ValueError, match="empty waveform"):
        acquire_scope_recording(Empty(), [Channel.C1])


def _mode_calls(scope):
    return [c for c in scope.calls if c[0] == "set_trigger_mode"]


def test_parse_seconds_accepts_the_reply_shapes_the_scope_uses_and_rejects_garbage():
    from iyzee.scope_workflows import _parse_seconds

    for raw, seconds in (
        ("TDIV 5.00E-06 S", 5e-6),
        ("TIME_DIV 2.00E-09S", 2e-9),
        ("TDIV 10 ns", 10e-9),
        ("1E-3", 1e-3),
    ):
        assert _parse_seconds(raw) == pytest.approx(seconds)
    with pytest.raises(ValueError, match="could not parse time"):
        _parse_seconds("TDIV ???")


def _set_calls(scope):
    return [c for c in scope.calls if c[0].startswith("set_")]


# -- channel apply / read ---------------------------------------------------------------------


def test_channel_settings_are_applied_and_read_under_the_driver_lock():
    # -- apply channel settings writes every field or only what changed
    scope = FakeScope()
    apply_channel_settings(
        scope, [_settings(Channel.C1, volts_per_div=1.0, offset=0.2, enabled=False)]
    )
    assert _set_calls(scope) == [
        ("set_volts_per_div", Channel.C1, 1.0),
        ("set_offset", Channel.C1, 0.2),
        ("set_coupling", Channel.C1, Coupling.DC_1M),
        ("set_trace_display", Channel.C1, False),
    ]

    scope = FakeScope()
    baseline = [_settings(Channel.C1)]
    apply_channel_settings(scope, [_settings(Channel.C1)], current_settings=baseline)
    assert _set_calls(scope) == []  # nothing changed: nothing written
    apply_channel_settings(scope, [_settings(Channel.C1, offset=0.2)], current_settings=baseline)
    assert _set_calls(scope) == [("set_offset", Channel.C1, 0.2)]

    scope = FakeScope()  # a channel with no baseline is refused before anything is written
    with pytest.raises(RuntimeError, match="C2 has no retrieved baseline"):
        apply_channel_settings(
            scope,
            [_settings(Channel.C1, offset=0.1), _settings(Channel.C2, offset=0.2)],
            current_settings=[_settings(Channel.C1)],
        )
    assert scope.calls == []

    # -- apply channel settings returns what the scope reports and stops at the first failure
    scope = StatefulScope(vdiv_step=0.1, ignore_coupling=frozenset({Channel.C2}))
    c1, c2 = apply_channel_settings(
        scope,
        [
            _settings(Channel.C1, volts_per_div=0.123, offset=0.25),
            _settings(Channel.C2, coupling=Coupling.DC_50),
        ],
    )
    assert c1.volts_per_div == pytest.approx(0.1)  # rounded by the scope
    assert c2.coupling == Coupling.DC_1M  # the ignored command shows up as the old value

    scope = StatefulScope(fail_channels=frozenset({Channel.C1}))
    with pytest.raises(RuntimeError, match="C1 refused"):
        apply_channel_settings(scope, [_settings(Channel.C1), _settings(Channel.C2)])
    assert all(c[1] == Channel.C1 for c in _set_calls(scope))
    assert not any(c[0].startswith("get_") for c in scope.calls)  # no read-back after a failure

    # -- read channel settings reads every field parses units and stops at the first failure
    scope = FakeScope()
    scope.channel_state[Channel.C1] = _settings(
        Channel.C1, enabled=False, volts_per_div=1.0, offset=-0.25, coupling=Coupling.DC_50
    )
    assert read_channel_settings(scope, [Channel.C1]) == [
        ChannelSettings(Channel.C1, False, 1.0, -0.25, Coupling.DC_50)
    ]

    scope = FakeScope()
    scope.get_volts_per_div = lambda channel: f"{channel}:VOLT_DIV 200E-3 V"
    scope.get_offset = lambda channel: f"{channel}:OFFSET -500mV"
    assert read_channel_settings(scope, [Channel.C1]) == [
        ChannelSettings(Channel.C1, True, 0.2, -0.5, Coupling.DC_1M)
    ]

    scope = FakeScope(fail_reads=frozenset({Channel.C1}))
    with pytest.raises(RuntimeError, match="did not respond"):
        read_channel_settings(scope, [Channel.C1, Channel.C2])
    assert {c[1] for c in scope.calls} == {Channel.C1}

    # -- batches hold the driver lock throughout
    scope = StatefulScope()
    held: list[bool] = []
    original = scope.get_volts_per_div
    scope.get_volts_per_div = lambda ch: (
        held.append(scope.transaction_lock._is_owned()),
        original(ch),
    )[1]
    scope.set_offset = lambda ch, v: held.append(scope.transaction_lock._is_owned())

    apply_channel_settings(scope, [_settings(Channel.C1), _settings(Channel.C2)])

    assert held and all(held)
    assert not scope.transaction_lock._is_owned()


# -- acquisition failures ---------------------------------------------------------------------
