"""The PSU and the optical shutter on its channel, for real.

The first two tests only ask questions. The rest open the shutter, so they are ``actuates``,
and the ``shutter`` fixture refuses to run them if the shutter is already open (someone's
experiment) and closes it afterwards no matter what, then asks the supply whether it is.

Opening the shutter lets light through. Make sure that is safe on your bench first.
"""

from __future__ import annotations

import time

import pytest

from iyzee.devices.base import SHUTTER_CHANNEL_DEFAULT
from iyzee.devices.handles import ShutterHandle
from iyzee.devices.power import PSU, ShutterControl
from iyzee.lab import Lab

from .hw_state import psu_output_is_on, psu_query

pytestmark = pytest.mark.shutter


def test_the_supply_answers_idn(psu: PSU) -> None:
    """What ``ShutterHandle.probe`` does not do: prove the supply is really there."""
    identity = psu_query(psu, "*IDN?")
    assert "hmp" in identity.lower(), f"expected an R&S HMP supply, got {identity!r}"


def test_the_shutter_channel_reports_an_output_state(psu: PSU) -> None:
    assert psu_output_is_on(psu, SHUTTER_CHANNEL_DEFAULT) in (True, False)


@pytest.mark.actuates
def test_open_and_close_are_confirmed_by_the_supply(shutter: ShutterControl, psu: PSU) -> None:
    shutter.open()
    assert psu_output_is_on(psu, shutter.chan), "opened, but the supply says the output is off"
    shutter.close()
    assert not psu_output_is_on(psu, shutter.chan), "closed, but the supply says it is still on"


@pytest.mark.actuates
def test_disconnecting_leaves_the_shutter_closed(
    shutter: ShutterControl, psu: PSU, lab: Lab
) -> None:
    """The shutter is closed on the way out, even from the open state.

    Uses its own handle so the session's connection is not disturbed.
    """
    handle = ShutterHandle(lab["psu"])
    handle.connect()
    try:
        device = handle.device
        assert device is not None
        device.open()
        assert psu_output_is_on(psu, device.chan)
    finally:
        handle.disconnect()
    time.sleep(0.2)
    assert not psu_output_is_on(psu, shutter.chan), "disconnect left the shutter OPEN"
