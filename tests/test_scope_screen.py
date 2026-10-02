"""Tests for the Scope page: apply-with-verification, acquire, and their failure paths.

The page's logic lives in ``scope_workflows`` (tested there); what is exercised
here is what the page adds: reading the form, reporting what the scope
*actually* holds after an apply, and never leaving a button stuck disabled.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from helpers import FakeHandle, async_test, notifications, wait_until
from test_scope_workflows import DetailedFakeScope, StatefulScope
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Input, RichLog, Select

from iyzee.scope import Channel, Coupling, TriggerSlope
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens import scope as scope_screen_mod
from iyzee.tui.screens.scope import ScopeScreen


class ScreenScope(StatefulScope, DetailedFakeScope):
    """Stateful (remembers writes, can round/ignore them) and able to acquire."""


async def _open(pilot: Pilot[Any], scope: object | None = None) -> ScopeScreen:
    app = pilot.app
    assert isinstance(app, IyzeeApp)
    if scope is not None:
        app.handles["scope"] = FakeHandle(scope=scope)
    await pilot.press("o")
    await pilot.pause()
    return app.query_one(ScopeScreen)


def _log(screen: ScopeScreen) -> str:
    return "\n".join(strip.text for strip in screen.query_one("#scope-log", RichLog).lines)


async def _synced(pilot: Pilot[Any], screen: ScopeScreen) -> None:
    """Wait for the retrieve the page runs by itself when shown.

    Do not press "Retrieve" on top of it: a second retrieve finishing later
    would overwrite whatever the test has typed into the form by then.
    """
    await wait_until(
        pilot,
        lambda: (
            screen._last_applied_channel_settings is not None
            and not screen._settings_busy
            and not screen._retrieve_in_flight
        ),
    )


def _set(screen: ScopeScreen, widget_id: str, value: str) -> None:
    screen.query_one(f"#{widget_id}", Input).value = value


# -- retrieve ------------------------------------------------------------------------------


class GatedScope(ScreenScope):
    """Blocks V/div reads while ``gate`` is closed, so a test can act mid-retrieve."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()

    def get_volts_per_div(self, channel: Channel) -> str:
        self.entered.set()
        self.gate.wait(timeout=5)
        return super().get_volts_per_div(channel)


@async_test
async def test_editing_a_field_while_a_retrieve_is_in_flight_does_not_break_the_result() -> None:
    """Regression: this raised "Set changed size during iteration" in the retrieve
    callback (the dirty set was mutated while a generator over it was still being
    consumed), skipping the rest of the callback and leaving the page stuck "busy"."""
    scope = GatedScope()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)
        original = screen.query_one("#C1-vdiv", Input).value

        scope.gate.clear()
        scope.entered.clear()
        screen.query_one("#retrieve-settings", Button).press()
        await wait_until(pilot, scope.entered.is_set)  # the worker is now inside the scope read
        _set(screen, "C1-vdiv", "0.9")  # the user types while it waits
        await wait_until(pilot, lambda: "C1-vdiv" in screen._dirty_fields)
        scope.gate.set()

        await wait_until(
            pilot, lambda: not screen._retrieve_in_flight and not screen._settings_busy
        )
        # The callback must run to completion. (Its exception used to be swallowed by
        # ``_ui``, and later Input.Changed events tidied the form, so only the work
        # the crash skipped, like this confirmation, shows it.)
        assert _log(screen).count("Scope settings synchronized from the instrument.") == 2
        assert screen._settings_synced
        assert screen.query_one("#C1-vdiv", Input).value == original  # form re-synced
        assert "C1-vdiv" not in screen._dirty_fields
        assert not screen.query_one("#C1-vdiv", Input).has_class("scope-dirty")


# -- apply channel changes: the baseline is what the scope reports ------------------------


@async_test
async def test_applying_channel_changes_tracks_verified_state_and_failed_edits() -> None:
    scope = ScreenScope(vdiv_step=0.1, ignore_coupling=frozenset({Channel.C2}))
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)

        _set(screen, "C1-vdiv", "0.123")
        await pilot.pause()
        screen.query_one("#apply-channels", Button).press()
        await wait_until(pilot, lambda: "Applied and verified" in _log(screen))

        baseline = screen._channel_baseline(Channel.C1)
        assert baseline is not None
        assert baseline.volts_per_div == pytest.approx(0.1)
        assert float(screen.query_one("#C1-vdiv", Input).value) == pytest.approx(0.1)
        assert "C1 V/div: requested 0.123, scope set 0.1" in _log(screen)
        assert "C1-vdiv" not in screen._dirty_fields

        before = screen._channel_baseline(Channel.C2)
        assert before is not None
        screen.query_one("#C2-coupling", Select).value = Coupling.DC_50.value
        await pilot.pause()
        screen.query_one("#apply-channels", Button).press()
        await wait_until(pilot, lambda: "coupling is" in _log(screen))

        assert "C2 coupling is D1M but D50 was requested" in _log(screen)
        assert any("Some channel changes failed" in n for n in notifications(app))
        baseline = screen._channel_baseline(Channel.C2)
        assert baseline is not None and baseline.coupling == before.coupling
        assert screen.query_one("#C2-coupling", Select).value == Coupling.DC_50.value
        assert "C2-coupling" in screen._dirty_fields
        assert not screen.query_one("#apply-channels", Button).disabled


@async_test
async def test_apply_after_the_scope_disconnects_releases_the_busy_flag_and_keeps_the_baseline() -> (
    None
):
    scope = ScreenScope()
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)
        baseline = screen._last_applied_channel_settings
        assert baseline is not None
        scope.calls.clear()

        app.handles.pop("scope")  # disconnected between the click and the worker starting
        screen._settings_busy = True
        screen._apply_channels(scope, [baseline[0]], baseline, threading.Lock())

        # Not stuck "busy", and the stale result is discarded: baseline untouched.
        # (The Apply button itself is legitimately disabled again with no scope connected.)
        await wait_until(pilot, lambda: not screen._settings_busy)
        assert screen._last_applied_channel_settings == baseline


# -- apply trigger changes -------------------------------------------------------------------


class TriggerScope(ScreenScope):
    """Remembers trigger writes like the channel fake does; can refuse or stall a write."""

    def __init__(
        self, *, refuse_level: bool = False, gate_reads: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.refuse_level = refuse_level
        self.gate_reads = gate_reads  # stall V/div reads (a retrieve) instead of level writes
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()

    def get_volts_per_div(self, channel: Channel) -> str:
        if self.gate_reads:
            self.entered.set()
            self.gate.wait(timeout=5)
        return super().get_volts_per_div(channel)

    def set_trigger_level(self, source: Channel, level: float) -> None:
        if not self.gate_reads:
            self.entered.set()
            self.gate.wait(timeout=5)
        if self.refuse_level:
            raise RuntimeError("level refused")
        super().set_trigger_level(source, level)
        self.trigger_state = replace(self.trigger_state, level_volts=level)

    def set_trigger_slope(self, source: Channel, slope: TriggerSlope) -> None:
        super().set_trigger_slope(source, slope)
        self.trigger_state = replace(self.trigger_state, slope=slope)


def _trigger_dirty(screen: ScopeScreen) -> set[str]:
    return {field_id for field_id in screen._dirty_fields if field_id.startswith("trig-")}


@async_test
async def test_applying_trigger_changes_shows_what_the_scope_reports_and_cleans_the_form() -> None:
    scope = TriggerScope()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)

        _set(screen, "trig-level", "0.25")
        screen.query_one("#trig-slope", Select).value = TriggerSlope.NEGATIVE.value
        await wait_until(pilot, lambda: _trigger_dirty(screen) == {"trig-level", "trig-slope"})
        screen.query_one("#apply-trigger", Button).press()
        await wait_until(pilot, lambda: "Trigger settings applied and verified." in _log(screen))

        verified = screen._last_applied_trigger_settings
        assert verified is not None
        assert verified.level_volts == 0.25 and verified.slope is TriggerSlope.NEGATIVE
        assert _trigger_dirty(screen) == set()
        assert not screen.query_one("#trig-level", Input).has_class("scope-dirty")
        assert not screen._settings_busy and screen._settings_synced


@async_test
async def test_a_refused_trigger_apply_is_reported_keeps_the_edit_and_releases_the_busy_flag() -> (
    None
):
    scope = TriggerScope(refuse_level=True)
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)

        _set(screen, "trig-level", "0.25")
        await wait_until(pilot, lambda: "trig-level" in screen._dirty_fields)
        screen.query_one("#apply-trigger", Button).press()
        await wait_until(pilot, lambda: "level refused" in _log(screen))

        assert not screen._settings_busy
        assert "trig-level" in screen._dirty_fields  # still editable, so the user can retry
        assert not screen._settings_synced
        assert any("Trigger settings failed" in n for n in notifications(app))


@pytest.mark.parametrize("operation", ["retrieve", "apply-trigger"])
@async_test
async def test_an_operation_finishing_after_a_disconnect_releases_its_flags(
    operation: str,
) -> None:
    scope = TriggerScope(gate_reads=operation == "retrieve")
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)
        scope.entered.clear()
        scope.gate.clear()

        if operation == "retrieve":
            screen.query_one("#retrieve-settings", Button).press()
        else:
            _set(screen, "trig-level", "0.25")
            await wait_until(pilot, lambda: "trig-level" in screen._dirty_fields)
            screen.query_one("#apply-trigger", Button).press()
        await wait_until(pilot, scope.entered.is_set)  # the worker is inside the scope call

        app.handles.pop("scope")  # the link goes away mid-operation
        scope.gate.set()

        await wait_until(pilot, lambda: not screen._settings_busy)
        assert not screen._retrieve_in_flight
        if operation == "retrieve":
            assert not screen.query_one("#retrieve-settings", Button).disabled


# -- acquire ---------------------------------------------------------------------------------


@async_test
async def test_acquire_saves_and_reports_that_a_running_acquisition_was_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("iyzee.experiment.io.DATA_ROOT", tmp_path)
    scope = ScreenScope()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)
        for channel in ("C2", "C3", "C4"):
            screen.query_one(f"#{channel}-enable", Checkbox).value = False

        screen.query_one("#acquire-waveforms", Button).press()
        await wait_until(pilot, lambda: "Saved acquisition" in _log(screen))

        assert "paused for a consistent capture, then resumed" in _log(screen)
        assert list(tmp_path.rglob("*.npz")) and list(tmp_path.rglob("*.json"))
        assert not screen.query_one("#acquire-waveforms", Button).disabled


@async_test
async def test_acquire_when_the_scope_disconnects_first_does_not_leave_the_button_disabled() -> (
    None
):
    scope = ScreenScope()
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)
        settings = tuple(screen._read_channel_settings())
        trigger = screen._read_trigger_settings()

        app.handles.pop("scope")  # gone before the worker runs: this was a KeyError
        screen.query_one("#acquire-waveforms", Button).disabled = True
        screen._acquire(scope, [Channel.C1], settings, trigger, None, None)

        await wait_until(pilot, lambda: not screen.query_one("#acquire-waveforms", Button).disabled)
        assert "disconnected before acquisition started" in _log(screen)
        assert any("Acquisition failed" in n for n in notifications(app))


@async_test
async def test_an_unexpected_acquire_error_re_enables_the_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = ScreenScope()

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(scope_screen_mod, "acquire_scope_recording", boom)
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)

        screen.query_one("#acquire-waveforms", Button).press()
        await wait_until(pilot, lambda: "driver exploded" in _log(screen))

        assert not screen.query_one("#acquire-waveforms", Button).disabled


# -- form plumbing ---------------------------------------------------------------------------


@async_test
async def test_flagging_a_select_as_invalid_marks_and_focuses_it() -> None:
    """``_flag_invalid`` used to assume an ``Input``; a bad ``Select`` would crash it."""
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot)

        screen._flag_invalid("C1-coupling")
        await pilot.pause()

        select = screen.query_one("#C1-coupling", Select)
        assert select.has_class("-invalid")
        screen._flag_invalid("C1-vdiv")  # moving on clears the previous marker
        assert not select.has_class("-invalid")
