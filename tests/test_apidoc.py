from __future__ import annotations

import threading

from iyzee.tui.apidoc import describe_api
from iyzee.tui.instruments import LockedProxy
from iyzee.wavemeter_readout import endpoint


class _Device:
    """A device."""

    colour = "red"

    def set_level(self, volts: float) -> None:
        """Set the level."""

    @endpoint("GET", "{channel}/")
    def read(self, channel: int = 1) -> float:
        """Read a channel."""
        return 1.0

    def _private(self) -> None:
        """Hidden."""


def test_describe_api_lists_public_methods_with_signature_route_and_summary():
    text = describe_api(_Device())
    assert (
        "set_level(volts: 'float') -> 'None'" in text or "set_level(volts: float) -> None" in text
    )
    assert "[GET /api/{channel}/]" in text
    assert "Read a channel." in text and "Set the level." in text
    assert "_private" not in text and "Hidden" not in text
    assert "attributes: colour" in text


def test_describe_api_filters_by_name_or_summary():
    text = describe_api(_Device(), "level")
    assert "set_level" in text and "read(" not in text
    assert "no methods match" in describe_api(_Device(), "zzz")
    assert "read(" in describe_api(_Device(), "channel")  # matched in the summary


def test_a_locked_proxy_keeps_help_working_and_describes_the_device_behind_it():
    proxy = LockedProxy(_Device(), threading.RLock())
    assert proxy.read.__doc__ == "Read a channel."
    assert proxy.read.__name__ == "read"
    assert proxy.read.rest == "GET /api/{channel}/"  # type: ignore[attr-defined]
    assert proxy.read() == 1.0
    assert "set_level" in describe_api(proxy)
