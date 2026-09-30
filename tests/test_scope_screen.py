"""Tests for the Scope page: apply-with-verification, acquire, and their failure paths.

The page's logic lives in ``scope_workflows`` (tested there); what is exercised
here is what the page adds: reading the form, reporting what the scope
*actually* holds after an apply, and never leaving a button stuck disabled.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from helpers import FakeHandle, async_test, notifications, wait_until
from test_scope_workflows import DetailedFakeScope, StatefulScope
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Input, RichLog, Select

from iyzee.scope import Channel, Coupling
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
    """Run the page's explicit sync action once, then wait for it to finish."""
    if not screen._settings_synced and not screen._retrieve_in_flight:
        screen.query_one("#retrieve-settings", Button).press()
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


class DisconnectingScope(ScreenScope):
    """Loses the link after channel reads, before trigger settings are read."""

    connected = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.trigger_reads = 0

    def get_trace_display(self, channel: Channel) -> str:
        result = super().get_trace_display(channel)
        if channel == Channel.C4:
            self.connected = False
        return result

    def get_trigger_source(self) -> str:
        self.trigger_reads += 1
        raise AssertionError("trigger read must not run after the link is lost")

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


@async_test
async def test_retrieve_handles_a_connection_loss_between_channel_and_trigger_reads() -> None:
    scope = DisconnectingScope()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, scope)
        await wait_until(
            pilot, lambda: not screen._retrieve_in_flight and not screen._settings_busy
        )

        assert scope.trigger_reads == 0
        assert not screen._settings_synced
        assert "Trigger: scope connection lost while reading trigger settings" in _log(screen)

@async_test
async def test_opening_scope_pane_does_not_start_a_hardware_retrieve() -> None:
    """Scope navigation is UI-only until the user explicitly requests a sync."""
    scope = GatedScope()
    scope.gate.clear()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, scope)
        await pilot.pause()

        assert not screen._retrieve_in_flight
        assert not screen._settings_busy
        assert not screen._settings_synced
        assert not scope.entered.is_set()


# -- apply channel changes: the baseline is what the scope reports ------------------------


@async_test
async def test_applying_channel_changes_tracks_verified_state_and_failed_edits() -> None:
    scope = ScreenScope(vdiv_step=0.1, ignore_coupling=frozenset({Channel.C2}))
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, scope)
        await _synced(pilot, screen)

        screen.query_one("#C1-vdiv", Input).value = "0.123"
        await pilot.pause()
        screen.query_one("#apply-channels", Button).press()
        await wait_until(pilot, lambda: not screen._settings_busy)

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
        await wait_until(pilot, lambda: not screen._settings_busy)

        assert "C2 coupling is D1M but D50 was requested" in _log(screen)
        assert any("Some channel changes failed" in n for n in notifications(app))
        baseline = screen._channel_baseline(Channel.C2)
        assert baseline is not None and baseline.coupling == before.coupling
        assert screen.query_one("#C2-coupling", Select).value == Coupling.DC_50.value
        assert "C2-coupling" in screen._dirty_fields
        assert not screen.query_one("#apply-channels", Button).disabled


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
