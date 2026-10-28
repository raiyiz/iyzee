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


def test_connect_and_disconnect_register_close_and_say_what_is_missing():
    # -- connect registers the handle and returns the probe detail
    handle = FakeHandle()
    lab = make_lab(handle)

    detail = lab.connect("a")

    assert lab.connected == ("a",) and lab["a"] is handle
    assert detail == handle.probe()

    # -- a failed connect closes the half open link and registers nothing
    handle = FakeHandle(connect_error="no route")
    lab = make_lab(handle)

    with pytest.raises(ConnectionError, match="no route"):
        lab.connect("a")

    assert lab.connected == () and handle.disconnected

    # -- disconnect unregisters closes and tolerates unknown keys
    handle = FakeHandle()
    lab = make_lab(handle)
    lab.connect("a")

    lab.disconnect("a")
    lab.disconnect("a")  # second time: nothing to do

    assert lab.connected == () and handle.disconnected
    with pytest.raises(NotConnectedError):
        lab["a"]

    # -- device returns the driver and a missing instrument says so
    handle = FakeHandle(device="the driver")
    lab = make_lab(handle)
    with pytest.raises(NotConnectedError, match="not connected"):
        lab.device("a")
    lab.connect("a")
    assert lab.device("a") == "the driver"


def test_dead_links_are_dropped_and_close_all_never_waits_past_its_timeout():
    # -- drop dead links unregisters only the dead ones and releases them
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

    # -- close all never waits longer than its timeout for a busy instrument
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


# -- instruments that are a part of another (the shutter on the PSU) -------------------


class PartHandle(FakeHandle):
    """A part of a parent instrument: shares its lock, records what it was built on."""

    def __init__(self, parent: FakeHandle, events: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.parent = parent
        self.lock = parent.lock  # one link, one lock
        self.events = events

    def disconnect(self) -> None:
        # Closing a part needs the parent's link: it must still be open.
        self.events.append(f"part closes (parent disconnected: {self.parent.disconnected})")
        super().disconnect()


def make_parent_lab(parent: FakeHandle | None = None, part: PartHandle | None = None):
    events: list[str] = []
    parent = parent or FakeHandle()
    built: list[FakeHandle] = []

    def make_part(handle: FakeHandle) -> PartHandle:
        built.append(handle)
        return part or PartHandle(handle, events)

    lab = Lab(
        [
            InstrumentSpec("psu", "PSU", lambda: parent),
            InstrumentSpec("shutter", "Shutter", make_part, parent="psu"),
        ]
    )
    return lab, parent, built, events


def test_a_part_connects_its_parent_once_and_is_closed_before_it():
    # -- connecting a part connects its parent once and builds it on the parent handle
    lab, parent, built, _ = make_parent_lab()

    lab.connect("shutter")
    lab.connect("psu")  # already connected by the above: opens nothing a second time

    assert lab.connected == ("psu", "shutter") and built == [parent]
    assert parent.connect_calls == 1
    assert lab.details["psu"] == parent.probe()

    failing, _, _, _ = make_parent_lab(parent=FakeHandle(connect_error="no route"))
    with pytest.raises(ConnectionError, match="no route"):
        failing.connect("shutter")
    assert failing.connected == ()  # neither registered

    # -- a part is closed before its parent however they are released
    for release in (lambda lab: lab.disconnect("psu"), lambda lab: lab.close_all()):
        lab, parent, _, events = make_parent_lab()
        lab.connect("shutter")

        release(lab)

        assert events == ["part closes (parent disconnected: False)"]
        assert parent.disconnected and lab.connected == ()

    lab, parent, _, _ = make_parent_lab()
    lab.connect("shutter")
    lab.disconnect("shutter")  # only the part: the parent stays up
    assert lab.connected == ("psu",) and not parent.disconnected
