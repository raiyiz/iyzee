"""Tests for the instrument adapter layer: LockedProxy and each
InstrumentHandle implementation.

None of these touch real hardware. Each handle wraps a driver
(KeysightMXA / ShutterControl / LeCroy / single_readout) that either
takes a fake device directly (_VisaHandle) or is constructed inside the
handle itself — for the latter we monkeypatch the class/function
`instruments.py` imports, not the real driver, so these stay fast and
deterministic regardless of what's plugged in on the bench.
"""

from __future__ import annotations

import threading
import time
from typing import cast

import pytest

from iyzee.scope import LeCroyTimeoutError
from iyzee.tui import instruments as instruments_mod
from iyzee.tui.instruments import (
    INSTRUMENTS,
    LockedProxy,
    ScopeHandle,
    ShutterHandle,
    WavemeterHandle,
    _VisaHandle,
)

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


class _FakeShutterControl:
    def __init__(self, chan, ip) -> None:  # noqa: ANN001 - mirrors real signature
        self.chan = chan
        self.ip = ip
        self.psu = _FakePsu()
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.psu.close()
        self.connected = False


class _FakePsu:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_shutter_handle_connect_and_disconnect_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instruments_mod, "ShutterControl", _FakeShutterControl)

    handle = ShutterHandle()
    handle.disconnect()  # disconnect before connect is a no-op

    handle.connect()
    assert handle.shutter is not None
    assert "CH" in handle.probe()

    # The real attribute is typed as ShutterControl | None; the fake only changes
    # the runtime constructor, so narrow it explicitly for the test-only fake.
    shutter = cast(_FakeShutterControl, handle.shutter)
    psu = shutter.psu
    assert shutter.connected is True
    handle.disconnect()
    assert shutter.connected is False
    assert psu.closed is True
    assert handle.shutter is None


# -- WavemeterHandle ----------------------------------------------------------


def test_wavemeter_handle_probe_reads_the_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_clients = []

    def read(self, channel=None):
        seen_clients.append(self)
        return 377.105

    monkeypatch.setattr(instruments_mod.Wavemeter, "read_frequency", read)

    handle = WavemeterHandle()
    assert handle.probe() == "ch4 = 377.105000 THz"
    assert handle.channel == instruments_mod.DEFAULT_CHANNEL
    assert isinstance(handle.device, instruments_mod.Wavemeter)
    assert seen_clients == [handle.device]


def test_wavemeter_handle_probe_uses_its_configured_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    def read(self, channel=None):
        seen.append(channel)
        return 377.105

    monkeypatch.setattr(instruments_mod.Wavemeter, "read_frequency", read)

    handle = WavemeterHandle(channel=4)

    assert handle.probe() == "ch4 = 377.105000 THz"
    assert seen == [4]


def test_wavemeter_handle_probe_wraps_readout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self, channel=None):
        raise instruments_mod.WavemeterReadoutError("switch unreachable")

    monkeypatch.setattr(instruments_mod.Wavemeter, "read_frequency", fail)

    handle = WavemeterHandle()

    with pytest.raises(ConnectionError, match="switch unreachable"):
        handle.probe()


# -- ScopeHandle ----------------------------------------------------------


class _FakeLeCroy:
    answer: str | Exception = "LECROY,WS452,SN1,9.0"

    def __init__(self) -> None:
        self.connected_to: str | None = None
        self.disconnected = False
        self.idn_timeouts: list[float | None] = []

    def connect(self, ip: str) -> None:
        self.connected_to = ip

    def disconnect(self) -> None:
        self.disconnected = True

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
    scope = cast(_FakeLeCroy, handle.scope)
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


def test_scope_handle_alive_follows_the_drivers_link_check(monkeypatch: pytest.MonkeyPatch) -> None:
    class Link(_FakeLeCroy):
        healthy = True

        def check_link(self) -> bool:
            return self.healthy

    monkeypatch.setattr(instruments_mod, "LeCroy", Link)
    handle = ScopeHandle()
    assert handle.alive

    cast(Link, handle.scope).healthy = False

    assert not handle.alive


def test_handles_that_cannot_tell_report_alive() -> None:
    assert instruments_mod._LockedHandle().alive is True
