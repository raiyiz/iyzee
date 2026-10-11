"""The wavemeter server, for real.

Reading is harmless. Moving the laser is not, so that one test is ``actuates`` *and* needs
you to say, in the environment, which frequency is safe.

Environment (all optional except where noted):

``IYZEE_TEST_WAVEMETER_CHANNEL``    channel with the laser on it (default: the driver's default)
``IYZEE_TEST_WAVEMETER_SPREAD_GHZ`` allowed peak-to-peak spread over ten readings (default 1)
``IYZEE_TEST_LASER_SETPOINT_THZ``   REQUIRED for the setpoint test: a frequency the laser is
                                    safe to be locked to, ideally where it already is
``IYZEE_TEST_SETTLE_S``             seconds to wait after setting it (default 2)
``IYZEE_TEST_SETPOINT_TOL_MHZ``     how close the reading must get (default 50)
"""

from __future__ import annotations

import math
import os
import time

import pytest

from iyzee.devices.handles import WavemeterHandle
from iyzee.devices.wavemeter import DEFAULT_CHANNEL, Wavemeter

pytestmark = pytest.mark.wavemeter

CHANNEL = int(os.environ.get("IYZEE_TEST_WAVEMETER_CHANNEL", DEFAULT_CHANNEL))
#: Anything a lab laser could be: 300 nm to 3 um. An error code (-3.0, 0) is far below it.
OPTICAL_THZ = (100.0, 1000.0)


def test_the_handle_probe_reads_a_real_frequency(wavemeter: Wavemeter) -> None:
    # ``wavemeter`` is requested only so an unreachable server fails with the clear message.
    assert WavemeterHandle().probe().startswith("wavemeter = ")


def test_a_reading_is_a_plausible_optical_frequency(wavemeter: Wavemeter) -> None:
    frequency = wavemeter.read_frequency(CHANNEL)
    assert math.isfinite(frequency), frequency
    low, high = OPTICAL_THZ
    assert low < frequency < high, (
        f"channel {CHANNEL} read {frequency!r} THz, which is not an optical frequency "
        "(an error code from the server? no light on that channel?). "
        "Set IYZEE_TEST_WAVEMETER_CHANNEL if the laser is on another channel."
    )


def test_consecutive_readings_agree(wavemeter: Wavemeter) -> None:
    allowed_thz = float(os.environ.get("IYZEE_TEST_WAVEMETER_SPREAD_GHZ", "1")) * 1e-3
    readings = []
    for _ in range(10):
        readings.append(wavemeter.read_frequency(CHANNEL))
        time.sleep(0.05)
    spread = max(readings) - min(readings)
    assert spread < allowed_thz, f"{spread * 1e3:.3f} GHz peak-to-peak over {readings}"


@pytest.mark.actuates
def test_the_setpoint_is_followed(wavemeter: Wavemeter) -> None:
    """Sets the PID setpoint and checks the laser gets there. The laser is NOT put back:
    give it the frequency it is already locked to, so that nothing visibly changes."""
    raw = os.environ.get("IYZEE_TEST_LASER_SETPOINT_THZ")
    if raw is None:
        pytest.skip("set IYZEE_TEST_LASER_SETPOINT_THZ to a frequency that is safe to lock to")
    target = float(raw)
    tolerance_thz = float(os.environ.get("IYZEE_TEST_SETPOINT_TOL_MHZ", "50")) * 1e-6

    wavemeter.set_pid_setpoint(target, CHANNEL)
    time.sleep(float(os.environ.get("IYZEE_TEST_SETTLE_S", "2")))
    measured = wavemeter.read_frequency(CHANNEL)

    assert abs(measured - target) < tolerance_thz, (
        f"asked for {target} THz, measured {measured} THz "
        f"({(measured - target) * 1e6:+.1f} MHz): the laser did not follow"
    )
