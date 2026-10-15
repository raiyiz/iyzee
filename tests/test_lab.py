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


def test_connecting_a_part_connects_its_parent_first_and_builds_it_on_the_parent_handle():
    lab, parent, built, _ = make_parent_lab()

    lab.connect("shutter")

    assert lab.connected == ("psu", "shutter") and built == [parent]
    assert parent.connect_calls == 1
    assert lab.details["psu"] == parent.probe()  # the auto-connected parent has its detail too


def test_an_already_connected_parent_is_reused_not_connected_twice():
    lab, parent, _, _ = make_parent_lab()
    lab.connect("psu")

    lab.connect("shutter")

    assert parent.connect_calls == 1 and lab.connected == ("psu", "shutter")


def test_a_failing_parent_fails_the_part_and_registers_neither():
    lab, parent, _, _ = make_parent_lab(parent=FakeHandle(connect_error="no route"))

    with pytest.raises(ConnectionError, match="no route"):
        lab.connect("shutter")

    assert lab.connected == ()


def test_connecting_the_part_and_its_parent_at_once_opens_the_parent_once():
    lab, parent, _, _ = make_parent_lab(parent=FakeHandle(connect_delay=0.2))
    errors: list[BaseException] = []

    def connect(key: str) -> None:
        try:
            lab.connect(key)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=connect, args=(key,)) for key in ("shutter", "psu")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert errors == []
    assert parent.connect_calls == 1
    assert set(lab.connected) == {"psu", "shutter"}


def test_disconnecting_the_parent_closes_the_part_first():
    lab, parent, _, events = make_parent_lab()
    lab.connect("shutter")

    lab.disconnect("psu")

    assert events == ["part closes (parent disconnected: False)"]
    assert parent.disconnected and lab.connected == ()


def test_disconnecting_only_the_part_leaves_the_parent_connected():
    lab, parent, _, _ = make_parent_lab()
    lab.connect("shutter")

    lab.disconnect("shutter")

    assert lab.connected == ("psu",) and not parent.disconnected


def test_closing_everything_closes_the_part_before_the_parent():
    lab, parent, _, events = make_parent_lab()
    lab.connect("shutter")

    lab.close_all()

    assert events == ["part closes (parent disconnected: False)"]
    assert parent.disconnected and lab.connected == ()


def test_a_dead_parent_link_drops_its_parts_too():
    lab, parent, _, _ = make_parent_lab()
    lab.connect("shutter")
    parent.alive = False

    class Following(PartHandle):
        @property
        def alive(self) -> bool:  # type: ignore[override]
            return self.parent.alive

        @alive.setter
        def alive(self, _value: bool) -> None:
            pass

    lab.handles["shutter"] = Following(parent, [])

    assert sorted(lab.drop_dead_links()) == ["psu", "shutter"]
    assert lab.connected == ()


def test_connecting_something_already_connected_opens_nothing_again():
    handle = FakeHandle()
    lab = make_lab(handle)
    first = lab.connect("a")

    assert lab.connect("a") == first
    assert handle.connect_calls == 1 and not handle.disconnected
