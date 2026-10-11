"""Fixtures for the real-hardware tests.

Nothing here runs unless ``--hardware`` is passed (see the top-level ``tests/conftest.py``).
Instruments are reached exactly as the application reaches them, through
:class:`iyzee.lab.Lab`, at the addresses ``iyzee.config`` resolves (``IYZEE_*_IP`` or
``config.toml``). The first line of a ``--hardware`` run lists them.

An instrument that cannot be reached **fails** its tests; it does not skip them. A hardware
run that quietly skipped half its tests would look like a pass. Deselect what is not on the
bench instead::

    pytest --hardware -m "not scope"
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from iyzee.config import IP, address
from iyzee.devices.base import SHUTTER_CHANNEL_DEFAULT
from iyzee.devices.handles import InstrumentHandle
from iyzee.devices.mxa import KeysightMXA
from iyzee.devices.power import PSU, ShutterControl
from iyzee.devices.scope import LeCroy
from iyzee.devices.wavemeter import Wavemeter
from iyzee.lab import Lab

from .hw_state import psu_output_is_on

_UNREACHABLE: dict[str, str] = {}


def _require(lab: Lab, key: str) -> InstrumentHandle:
    """Connect ``key`` once per session; remember a failure so later tests fail fast."""
    if key in lab.handles:
        return lab[key]
    if key in _UNREACHABLE:
        pytest.fail(_UNREACHABLE[key], pytrace=False)
    problem = None
    try:
        lab.connect(key)
    except Exception as exc:  # noqa: BLE001 - any failure to connect is the same verdict
        problem = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
    if problem is not None:
        # Failed outside the ``except`` so the report is this message, not a chain of causes.
        _UNREACHABLE[key] = (
            f"cannot connect to {key}: {problem}\n"
            f"The address comes from IYZEE_*_IP / config.toml. "
            f'If this instrument is not on the bench, run with -m "not {key}".'
        )
        pytest.fail(_UNREACHABLE[key], pytrace=False)
    return lab[key]


@pytest.fixture(scope="session")
def lab() -> Iterator[Lab]:
    session = Lab()
    yield session
    # Disconnecting the shutter closes it first (ShutterControl.disconnect).
    session.close_all()


# -- Keysight MXA -----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mxa_handle(lab: Lab) -> InstrumentHandle:
    return _require(lab, "mxa")


@pytest.fixture(scope="session")
def mx(mxa_handle: InstrumentHandle) -> KeysightMXA:
    device = mxa_handle.device
    assert isinstance(device, KeysightMXA)
    return device


# -- LeCroy scope -----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def scope_handle(lab: Lab) -> InstrumentHandle:
    return _require(lab, "scope")


@pytest.fixture(scope="session")
def scope(scope_handle: InstrumentHandle) -> LeCroy:
    device = scope_handle.device
    assert isinstance(device, LeCroy)
    return device


# -- wavemeter --------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def wavemeter(lab: Lab) -> Wavemeter:
    device = _require(lab, "wavemeter").device
    assert isinstance(device, Wavemeter)
    return device


# -- PSU / shutter ----------------------------------------------------------------------------


@pytest.fixture(scope="session")
def psu() -> Iterator[PSU]:
    """A raw, read-mostly connection to the PSU.

    Opening it writes nothing (``BaseDevice.connect`` only opens the socket), unlike the
    shutter handle, whose ``connect`` configures the shutter channel's voltage and current.
    Tests that only look use this; tests that drive the shutter use ``shutter``.
    """
    supply = PSU(ip=address(IP.POWER_SUPPLY))
    problem = None
    try:
        supply.connect()
    except Exception as exc:  # noqa: BLE001
        problem = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
    if problem is not None:
        pytest.fail(
            f"cannot connect to the PSU at {address(IP.POWER_SUPPLY)}: {problem}\n"
            'Run with -m "not shutter" if it is not on the bench.',
            pytrace=False,
        )
    yield supply
    supply.close()


@pytest.fixture
def shutter(lab: Lab, psu: PSU) -> Iterator[ShutterControl]:
    """The shutter controller, guaranteed closed before and after the test.

    A shutter that is already open belongs to somebody's running experiment: the test is
    skipped, and nothing is connected or written. Whatever the test does, the shutter is
    closed afterwards and the supply is asked whether it really is.
    """
    if psu_output_is_on(psu, SHUTTER_CHANNEL_DEFAULT):
        pytest.skip("shutter output is already ON (open); not touching it")
    handle = _require(lab, "shutter")
    device = handle.device
    assert isinstance(device, ShutterControl)
    try:
        yield device
    finally:
        with handle.lock:
            device.close()
    if psu_output_is_on(psu, device.chan):
        pytest.fail("SHUTTER IS STILL OPEN after the test closed it", pytrace=False)
