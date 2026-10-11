"""The LeCroy scope, for real: identity, settings read-back, and a full acquisition."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from iyzee.devices.handles import InstrumentHandle
from iyzee.devices.scope import Channel, Coupling, LeCroy, TriggerMode
from iyzee.experiment import load_recording
from iyzee.scope_workflows import (
    _parse_seconds,
    acquire_scope_recording,
    read_channel_settings,
    read_trigger_settings,
    save_scope_acquisition,
)
from iyzee.waveform_math import traces_from_scope_recording

pytestmark = pytest.mark.scope


def test_identifies_as_a_lecroy(scope: LeCroy) -> None:
    assert "lecroy" in scope.idn().lower()


def test_the_scope_reports_itself_as_connected(scope_handle: InstrumentHandle) -> None:
    assert scope_handle.alive


def test_settings_read_back_through_the_parsers(scope: LeCroy) -> None:
    """The replies of a real scope parse into the workflow's own types.

    If a firmware or model changes the shape of a reply, this is where it shows.
    """
    channels = read_channel_settings(scope, list(Channel))
    assert [setting.channel for setting in channels] == list(Channel)
    for setting in channels:
        assert setting.volts_per_div > 0, setting
        assert isinstance(setting.coupling, Coupling), setting

    trigger = read_trigger_settings(scope)
    assert trigger.source in Channel
    assert trigger.mode in TriggerMode


def test_the_timebase_is_positive(scope: LeCroy) -> None:
    assert _parse_seconds(scope.get_time_per_div()) > 0


@pytest.fixture
def trigger_mode_restored(scope: LeCroy, scope_handle: InstrumentHandle) -> Iterator[TriggerMode]:
    """Hands out the trigger mode the scope was in, and puts it back whatever happens."""
    with scope_handle.lock:
        prior = read_trigger_settings(scope).mode
    yield prior
    with scope_handle.lock:
        scope.set_trigger_mode(prior)


@pytest.mark.actuates
def test_an_acquisition_is_complete_calibrated_and_saved_intact(
    scope: LeCroy,
    scope_handle: InstrumentHandle,
    trigger_mode_restored: TriggerMode,
    tmp_path: Path,
) -> None:
    """Freezes the acquisition (trigger STOP), downloads every enabled channel, restores."""
    with scope_handle.lock:
        enabled = [s.channel for s in read_channel_settings(scope, list(Channel)) if s.enabled]
    if not enabled:
        pytest.skip("no channel is displayed on the scope; nothing to acquire")

    with scope_handle.lock:
        recording = acquire_scope_recording(scope, enabled)

    assert recording.warnings == ()
    assert [waveform.channel for waveform in recording.waveforms] == enabled
    for waveform in recording.waveforms:
        assert waveform.values.size > 1
        assert waveform.values.size == waveform.time.size
        assert np.isfinite(waveform.values).all()
        assert np.all(np.diff(waveform.time) > 0), "time axis must increase"
        assert waveform.raw_codes is not None
        assert waveform.vertical_gain is not None, "scope did not report a vertical gain"
        assert waveform.vertical_offset is not None, "scope did not report a vertical offset"
        expected = waveform.vertical_gain * waveform.raw_codes - waveform.vertical_offset
        assert np.allclose(waveform.values, expected), "values are not gain*codes-offset"

    with scope_handle.lock:
        assert read_trigger_settings(scope).mode == trigger_mode_restored

    path = save_scope_acquisition(recording, tmp_path)
    loaded = load_recording(path)
    assert loaded.metadata["data_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(traces_from_scope_recording(loaded)) == len(enabled)
