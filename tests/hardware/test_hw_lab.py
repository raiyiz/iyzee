"""Session-level behaviour that only real instruments can show."""

from __future__ import annotations

import threading

import pytest

from iyzee.devices.handles import InstrumentHandle, LockedProxy
from iyzee.devices.mxa import KeysightMXA
from iyzee.lab import Lab

pytestmark = pytest.mark.mxa


def test_concurrent_callers_through_the_proxy_never_see_each_others_replies(
    mx: KeysightMXA, mxa_handle: InstrumentHandle
) -> None:
    """The point of ``LockedProxy``: threads sharing one analyzer get their own answers.

    ``*IDN?`` is a fixed string, so a reply that went to the wrong caller (or a reply
    left over from an overlapped exchange) shows up as a different or empty string.
    """
    proxy = LockedProxy(mx, mxa_handle.lock)
    expected = mx.idn()
    replies: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(25):
                replies.append(proxy.idn())
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors, errors
    assert len(replies) == 100
    assert set(replies) == {expected}


def test_the_scripting_pattern_from_the_readme_opens_and_closes_cleanly(mx: KeysightMXA) -> None:
    """``with KeysightMXA() as mx`` (a second session beside the session's own)."""
    with KeysightMXA() as second:
        assert second.idn() == mx.idn()
    assert second.instrument is None


def test_the_lab_knows_which_links_are_alive(lab: Lab, mxa_handle: InstrumentHandle) -> None:
    assert "mxa" in lab.connected
    assert lab.drop_dead_links() == []
    assert "mxa" in lab.connected
