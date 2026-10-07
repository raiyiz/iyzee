from __future__ import annotations

import threading
import time

import pytest
from helpers import FakeHandle

from iyzee.lab import InstrumentSpec, Lab, NotConnectedError


def make_lab(*handles: FakeHandle, keys=("a", "b")) -> Lab:
    queue = list(handles)
    specs = [InstrumentSpec(k, k.upper(), lambda: queue.pop(0)) for k in keys]
    return Lab(specs)


def test_connect_registers_the_handle_and_returns_the_probe_detail():
    handle = FakeHandle()
    lab = make_lab(handle)

    detail = lab.connect("a")

    assert lab.connected == ("a",) and lab["a"] is handle
    assert detail == handle.probe()


def test_a_failed_connect_closes_the_half_open_link_and_registers_nothing():
    handle = FakeHandle(connect_error="no route")
    lab = make_lab(handle)

    with pytest.raises(ConnectionError, match="no route"):
        lab.connect("a")

    assert lab.connected == () and handle.disconnected


def test_disconnect_unregisters_closes_and_tolerates_unknown_keys():
    handle = FakeHandle()
    lab = make_lab(handle)
    lab.connect("a")

    lab.disconnect("a")
    lab.disconnect("a")  # second time: nothing to do

    assert lab.connected == () and handle.disconnected
    with pytest.raises(NotConnectedError):
        lab["a"]


def test_drop_dead_links_unregisters_only_the_dead_ones_and_releases_them():
    alive, dead = FakeHandle(), FakeHandle()
    lab = make_lab(alive, dead)
    lab.connect("a")
    lab.connect("b")
    dead.alive = False

    assert lab.drop_dead_links() == ["b"]
    assert lab.connected == ("a",)
    deadline = time.monotonic() + 2
    while not dead.disconnected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert dead.disconnected and not alive.disconnected


def test_close_all_never_waits_longer_than_its_timeout_for_a_busy_instrument():
    busy, idle = FakeHandle(), FakeHandle()
    lab = make_lab(busy, idle)
    lab.connect("a")
    lab.connect("b")
    release = threading.Event()
    held = threading.Event()

    def hold():
        with busy.lock:
            held.set()
            release.wait(5)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    held.wait(2)

    started = time.monotonic()
    lab.close_all(timeout=0.3)
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 1.5
    assert idle.disconnected and not busy.disconnected
    assert lab.connected == ()


def test_device_returns_the_driver_and_a_missing_instrument_says_so():
    handle = FakeHandle(device="the driver")
    lab = make_lab(handle)
    with pytest.raises(NotConnectedError, match="not connected"):
        lab.device("a")
    lab.connect("a")
    assert lab.device("a") == "the driver"
