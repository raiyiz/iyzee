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

from iyzee.tui import instruments as instruments_mod
from iyzee.tui.instruments import (
    INSTRUMENTS,
    InstrumentSpec,
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
    they hold the same instrument_locks entry (see LockedProxy's docstring)."""
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


def test_locked_proxy_passes_through_non_callable_attributes() -> None:
    proxy = LockedProxy(_Recorder(), threading.Lock())
    assert proxy.not_callable == 42


def test_locked_proxy_does_not_forward_dunder_methods() -> None:
    """__enter__/__exit__ stay with the real device, not the proxy — see
    LockedProxy's docstring: connection lifecycle belongs to the Connect
    screen, not to console code doing `with lab.mx:`."""
    proxy = LockedProxy(_Recorder(), threading.Lock())
    with pytest.raises(AttributeError):
        proxy.__enter__()


# -- _VisaHandle ----------------------------------------------------------


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


class _FakePsu:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_shutter_handle_connect_builds_shutter_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instruments_mod, "ShutterControl", _FakeShutterControl)
    handle = ShutterHandle()
    handle.connect()
    assert handle.shutter is not None
    assert "CH" in handle.probe()


def test_shutter_handle_disconnect_closes_underlying_psu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instruments_mod, "ShutterControl", _FakeShutterControl)
    handle = ShutterHandle()
    handle.connect()
    # handle.shutter is statically ShutterControl | None (the real type
    # instruments.py declares); monkeypatching ShutterControl only
    # changes what's constructed at runtime, not that annotation, so the
    # fake needs an explicit cast here.
    shutter = cast(_FakeShutterControl, handle.shutter)
    psu = shutter.psu
    handle.disconnect()
    assert psu.closed is True
    assert handle.shutter is None


def test_shutter_handle_disconnect_before_connect_is_a_noop() -> None:
    ShutterHandle().disconnect()  # must not raise


# -- WavemeterHandle ----------------------------------------------------------


def test_wavemeter_handle_probe_reports_frequency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instruments_mod, "single_readout", lambda ch, printing=False: 377.105)
    handle = WavemeterHandle(channel=1)
    assert handle.probe() == "ch1 = 377.105000 THz"


def test_wavemeter_handle_probe_wraps_readout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_ch, printing=False):
        raise instruments_mod.WavemeterReadoutError("switch unreachable")

    monkeypatch.setattr(instruments_mod, "single_readout", _raise)
    handle = WavemeterHandle(channel=0)
    with pytest.raises(ConnectionError, match="switch unreachable"):
        handle.probe()


# -- ScopeHandle ----------------------------------------------------------


class _FakeLeCroy:
    def __init__(self) -> None:
        self.connected_to: str | None = None
        self.disconnected = False

    def connect(self, ip: str) -> None:
        self.connected_to = ip

    def disconnect(self) -> None:
        self.disconnected = True


def test_scope_handle_connect_and_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instruments_mod, "LeCroy", _FakeLeCroy)
    handle = ScopeHandle()
    handle.connect()
    scope = cast(_FakeLeCroy, handle.scope)
    assert scope.connected_to == str(handle._ip)
    assert "socket connected" in handle.probe()


# -- registry ----------------------------------------------------------


def test_instrument_registry_keys_are_unique() -> None:
    keys = [spec.key for spec in INSTRUMENTS]
    assert len(keys) == len(set(keys))


def test_instrument_registry_labels_are_non_empty() -> None:
    assert all(spec.label for spec in INSTRUMENTS)


def test_instrument_spec_build_uses_defaults() -> None:
    built: list[dict] = []

    def make(config):
        built.append(dict(config))
        return object()

    spec = InstrumentSpec("fake", "Fake", make, {"ip": "10.0.0.1", "timeout_ms": 1000})
    assert spec.build() is not None
    assert built == [{"ip": "10.0.0.1", "timeout_ms": 1000}]


def test_instrument_spec_build_overrides_defaults() -> None:
    built: list[dict] = []

    def make(config):
        built.append(dict(config))
        return object()

    spec = InstrumentSpec("fake", "Fake", make, {"ip": "10.0.0.1", "timeout_ms": 1000})
    spec.build({"ip": "10.0.0.2"})
    assert built == [{"ip": "10.0.0.2", "timeout_ms": 1000}]


def test_instrument_spec_build_does_not_mutate_defaults() -> None:
    defaults = {"ip": "10.0.0.1"}
    spec = InstrumentSpec("fake", "Fake", lambda config: object(), defaults)
    spec.build({"ip": "10.0.0.2"})
    assert defaults == {"ip": "10.0.0.1"}
