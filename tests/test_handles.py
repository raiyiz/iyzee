"""Tests for the instrument adapter layer: LockedProxy and each
InstrumentHandle implementation.

None of these touch real hardware. Each handle wraps a driver
(KeysightMXA / ShutterControl / LeCroy / Wavemeter) that either
takes a fake device directly (_VisaHandle) or is constructed inside the
handle itself — for the latter we monkeypatch the class/function
`instruments.py` imports, not the real driver, so these stay fast and
deterministic regardless of what's plugged in on the bench.
"""

from __future__ import annotations

import threading
import time
from typing import Any, cast

import pytest

from iyzee.devices import handles as instruments_mod
from iyzee.devices.handles import (
    LockedProxy,
    ScopeHandle,
    ShutterHandle,
    WavemeterHandle,
    _VisaHandle,
)
from iyzee.devices.scope import LeCroyTimeoutError
from iyzee.devices.wavemeter import DEFAULT_CHANNEL
from iyzee.lab import INSTRUMENTS

# -- LockedProxy --------------------------------------------------------


class _Recorder:
    """A fake device whose calls take just long enough to observe overlap."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def slow_write(self, tag: str) -> str:
        self.calls.append(f"start:{tag}")
        time.sleep(0.05)
        self.calls.append(f"end:{tag}")
        return tag

    @property
    def not_callable(self) -> int:
        return 42


def test_locked_proxy_serializes_calls_across_threads() -> None:
    """Two threads calling through the same proxy must never interleave —
    this is the exact guarantee SweepScreen and the console rely on when
    they hold the same handle's lock (see LockedProxy's docstring)."""
    lock = threading.Lock()
    recorder = _Recorder()
    proxy = LockedProxy(recorder, lock)

    def call(tag: str) -> None:
        proxy.slow_write(tag)

    t1 = threading.Thread(target=call, args=("a",))
    t2 = threading.Thread(target=call, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # If the calls had overlapped, we'd see start:a, start:b, end:a, end:b
    # (or similar interleaving) instead of one call finishing before the
    # next starts.
    assert recorder.calls in (
        ["start:a", "end:a", "start:b", "end:b"],
        ["start:b", "end:b", "start:a", "end:a"],
    )


class _FakeVisaDevice:
    def __init__(self, idn: str = "FAKE,MODEL,0,1.0", ip: str = "10.0.0.1") -> None:
        self._idn = idn
        self.ip = ip
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True

    def idn(self) -> str:
        return self._idn


def test_visa_handle_probe_uses_idn_when_available() -> None:
    handle = _VisaHandle(_FakeVisaDevice(idn="Keysight,MXA,SN1,FW2"))
    handle.connect()
    assert handle.probe() == "Keysight,MXA,SN1,FW2"
    assert handle.device.connected is True


class _FakeVisaDeviceNoIdn:
    """Simulates a driver with no *IDN? support (e.g. a raw PSU)."""

    def __init__(self, ip: str) -> None:
        self.ip = ip


def test_visa_handle_probe_falls_back_when_no_idn() -> None:
    handle = _VisaHandle(_FakeVisaDeviceNoIdn(ip="10.140.1.42"))
    assert handle.probe() == "connected @ 10.140.1.42"


def test_visa_handle_disconnect_closes_device() -> None:
    device = _FakeVisaDevice()
    handle = _VisaHandle(device)
    handle.disconnect()
    assert device.closed is True


# -- ShutterHandle ----------------------------------------------------------


class _FakePsuDevice:
    """A connected PSU that records what the shutter does to its channel."""

    def __init__(self, *, connected: bool = True) -> None:
        self.instrument: object | None = object() if connected else None
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    def set_voltage(self, voltage: float, channel: int) -> None:
        self.commands.append(("volt", (channel, voltage)))

    def set_current(self, current: float, channel: int) -> None:
        self.commands.append(("curr", (channel, current)))

    def enable_output(self, channel: int) -> None:
        self.commands.append(("on", (channel,)))

    def disable_output(self, channel: int) -> None:
        self.commands.append(("off", (channel,)))


class _FakePsuHandle:
    def __init__(self, device: _FakePsuDevice | None) -> None:
        self.device = device
        self.lock = threading.RLock()
        self.alive = True


def test_shutter_handle_is_a_channel_of_the_psu_it_is_built_on() -> None:
    psu = _FakePsuDevice()
    psu_handle = _FakePsuHandle(psu)

    handle = ShutterHandle(cast(Any, psu_handle), chan=3)
    handle.disconnect()  # disconnect before connect is a no-op
    assert psu.commands == []

    handle.connect()
    assert "CH3" in handle.probe()
    assert psu.commands == [("volt", (3, 1.7)), ("curr", (3, 0.01))]
    assert handle.device is not None

    # One link, one lock: the shutter is serialized with everything else on the supply.
    assert handle.lock is psu_handle.lock

    handle.disconnect()  # closes the shutter, leaves the supply connected
    assert psu.commands[-1] == ("off", (3,))
    assert psu.instrument is not None and handle.device is None


def test_shutter_handle_needs_a_connected_psu() -> None:
    for psu_handle in (_FakePsuHandle(None), _FakePsuHandle(_FakePsuDevice(connected=False))):
        with pytest.raises(ConnectionError, match="PSU"):
            ShutterHandle(cast(Any, psu_handle)).connect()


def test_shutter_handle_follows_the_psus_liveness() -> None:
    psu_handle = _FakePsuHandle(_FakePsuDevice())
    handle = ShutterHandle(cast(Any, psu_handle))
    assert handle.alive
    psu_handle.alive = False
    assert not handle.alive


def test_shutter_disconnect_does_not_touch_a_psu_that_is_already_closed() -> None:
    psu = _FakePsuDevice()
    handle = ShutterHandle(cast(Any, _FakePsuHandle(psu)))
    handle.connect()
    psu.instrument = None
    psu.commands.clear()

    handle.disconnect()

    assert psu.commands == []


# -- WavemeterHandle ----------------------------------------------------------


def test_wavemeter_handle_probe_reads_the_default_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    def read(self, channel: int = DEFAULT_CHANNEL):
        seen.append(channel)
        return 377.105

    monkeypatch.setattr(instruments_mod.Wavemeter, "read_frequency", read)

    handle = WavemeterHandle()

    assert handle.probe() == "wavemeter = 377.105000 THz"
    assert isinstance(handle.device, instruments_mod.Wavemeter)
    assert seen == [DEFAULT_CHANNEL]


def test_wavemeter_handle_probe_propagates_read_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError("switch unreachable")

    def fail(self, channel: int = 4) -> float:
        raise failure

    monkeypatch.setattr(instruments_mod.Wavemeter, "read_frequency", fail)

    with pytest.raises(OSError, match="switch unreachable") as caught:
        WavemeterHandle().probe()

    assert caught.value is failure


# -- ScopeHandle ----------------------------------------------------------


class _FakeLeCroy:
    answer: str | Exception = "LECROY,WS452,SN1,9.0"

    def __init__(self, ip: str) -> None:
        self.connected_to: str | None = None
        self.ip = ip
        self.connected = False
        self.disconnected = False
        self.idn_timeouts: list[float | None] = []

    def connect(self) -> None:
        self.connected_to = self.ip
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    def idn(self, *, timeout: float | None = None) -> str:
        self.idn_timeouts.append(timeout)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_scope_handle_connects_then_probe_identifies_the_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instruments_mod, "LeCroy", _FakeLeCroy)
    handle = ScopeHandle()
    handle.connect()
    scope = cast(_FakeLeCroy, handle.device)
    assert scope.connected_to == str(handle._ip)

    assert handle.probe() == "LECROY,WS452,SN1,9.0"
    # the first reply gets the widened bound, not the steady-state one
    assert scope.idn_timeouts == [ScopeHandle.FIRST_RESPONSE_TIMEOUT]


def test_scope_handle_probe_turns_a_silent_scope_into_an_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instruments_mod, "LeCroy", _FakeLeCroy)
    monkeypatch.setattr(_FakeLeCroy, "answer", LeCroyTimeoutError("no response after 15.0s"))
    handle = ScopeHandle()
    handle.connect()

    with pytest.raises(ConnectionError, match=r"did not answer \*IDN\? within 15s"):
        handle.probe()


# -- registry ----------------------------------------------------------


def test_instrument_registry_has_unique_nonempty_specs() -> None:
    keys = [spec.key for spec in INSTRUMENTS]
    assert len(keys) == len(set(keys))
    assert all(spec.label for spec in INSTRUMENTS)


def test_the_shutter_is_registered_as_a_part_of_the_psu() -> None:
    specs = {spec.key: spec for spec in INSTRUMENTS}
    assert specs["shutter"].parent == "psu" and not specs["psu"].parent
    # Every parent exists and comes first, so ``connect all`` and the Connect page list it above.
    keys = [spec.key for spec in INSTRUMENTS]
    for spec in INSTRUMENTS:
        assert not spec.parent or keys.index(spec.parent) < keys.index(spec.key)


def test_scope_handle_alive_follows_the_drivers_connected_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instruments_mod, "LeCroy", _FakeLeCroy)
    handle = ScopeHandle()
    handle.connect()
    assert handle.alive

    cast(_FakeLeCroy, handle.device).connected = False  # e.g. dropped after a timeout

    assert not handle.alive


def test_handles_that_cannot_tell_report_alive() -> None:
    assert instruments_mod._LockedHandle().alive is True
