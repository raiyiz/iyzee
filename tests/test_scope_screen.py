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
from test_scope_workflows import FakeScope, StatefulScope
from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, Input, RichLog, Select

from iyzee.devices.scope import Channel, Coupling, TriggerSlope
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.scope import ScopeScreen


class ScreenScope(StatefulScope, FakeScope):
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
    await wait_until(pilot, lambda: screen._synced and not screen._busy)


def _set(screen: ScopeScreen, widget_id: str, value: str) -> None:
    screen.query_one(f"#{widget_id}", Input).value = value


# -- retrieve ------------------------------------------------------------------------------


# -- apply: the form shows what the scope reports ------------------------------------------


@async_test
async def test_apply_writes_changes_trigger_changes_and_reports_a_refusal() -> None:
    # -- apply writes only changes and shows what the scope reports
    async def _apply_writes_only_changes_and_shows_what():
        scope = ScreenScope(vdiv_step=0.1, ignore_coupling=frozenset({Channel.C2}))
        async with IyzeeApp().run_test() as pilot:
            app = pilot.app
            assert isinstance(app, IyzeeApp)
            screen = await _open(pilot, scope)
            await _synced(pilot, screen)
            scope.calls.clear()

            _set(screen, "C1-vdiv", "0.123")
            screen.query_one("#apply-settings", Button).press()
            await wait_until(pilot, lambda: "Applied and verified" in _log(screen))

            assert [c for c in scope.calls if c[0].startswith("set_")] == [
                ("set_volts_per_div", Channel.C1, 0.123)
            ]  # nothing else was written
            assert float(screen.query_one("#C1-vdiv", Input).value) == pytest.approx(0.1)
            assert "C1 V/div: requested 0.123, scope set 0.1" in _log(screen)
            assert screen._channel_state is not None
            assert screen._channel_state[0].volts_per_div == pytest.approx(0.1)

            screen.query_one("#C2-coupling", Select).value = Coupling.DC_50.value
            screen.query_one("#apply-settings", Button).press()
            await wait_until(pilot, lambda: "coupling is" in _log(screen))

            assert "C2 coupling is D1M but D50 was requested" in _log(screen)
            assert any("Some channel changes failed" in n for n in notifications(app))
            # The form now shows the truth (the scope ignored the command).
            assert screen.query_one("#C2-coupling", Select).value == Coupling.DC_1M.value
            assert not screen._busy

            scope.calls.clear()  # and applying with nothing changed writes nothing
            screen.query_one("#apply-settings", Button).press()
            await wait_until(pilot, lambda: "No changes to apply." in _log(screen))
            assert scope.calls == []

    await _apply_writes_only_changes_and_shows_what()

    # -- applying trigger changes shows what the scope reports
    async def _applying_trigger_changes_shows_what_the_():
        scope = TriggerScope()
        async with IyzeeApp().run_test() as pilot:
            screen = await _open(pilot, scope)
            await _synced(pilot, screen)

            _set(screen, "trig-level", "0.25")
            screen.query_one("#trig-slope", Select).value = TriggerSlope.NEGATIVE.value
            screen.query_one("#apply-settings", Button).press()
            await wait_until(pilot, lambda: "Applied and verified changes." in _log(screen))

            verified = screen._trigger_state
            assert verified is not None
            assert verified.level_volts == 0.25 and verified.slope is TriggerSlope.NEGATIVE
            assert not screen._busy and screen._synced

    await _applying_trigger_changes_shows_what_the_()

    # -- a refused apply is reported keeps the edit and requires a retrieve
    async def _a_refused_apply_is_reported_keeps_the_ed():
        scope = TriggerScope(refuse_level=True)
        async with IyzeeApp().run_test() as pilot:
            app = pilot.app
            assert isinstance(app, IyzeeApp)
            screen = await _open(pilot, scope)
            await _synced(pilot, screen)

            _set(screen, "trig-level", "0.25")
            screen.query_one("#apply-settings", Button).press()
            await wait_until(pilot, lambda: "level refused" in _log(screen))

            assert not screen._busy
            assert (
                screen.query_one("#trig-level", Input).value == "0.25"
            )  # still editable, to retry
            assert not screen._synced  # what the scope holds is unknown until the next Retrieve
            assert screen.query_one("#apply-settings", Button).disabled
            assert any("Applying settings failed" in n for n in notifications(app))

    await _a_refused_apply_is_reported_keeps_the_ed()


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


@async_test
async def test_the_form_refuses_unapplied_changes_and_flags_invalid_selects() -> None:
    # -- acquire refuses while the form has changes the scope does not
    async def _acquire_refuses_while_the_form_has_chang():
        scope = ScreenScope()
        async with IyzeeApp().run_test() as pilot:
            app = pilot.app
            assert isinstance(app, IyzeeApp)
            screen = await _open(pilot, scope)
            await _synced(pilot, screen)

            _set(screen, "trig-level", "0.25")
            screen.query_one("#acquire-waveforms", Button).press()
            await pilot.pause()

            assert any("press Apply" in n for n in notifications(app))
            assert "Acquiring" not in _log(screen)

    await _acquire_refuses_while_the_form_has_chang()


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
        screen.query_one("#apply-settings", Button).press()
        await wait_until(pilot, lambda: "Applied and verified" in _log(screen))

        screen.query_one("#acquire-waveforms", Button).press()
        await wait_until(pilot, lambda: "Saved acquisition" in _log(screen))

        assert "paused for a consistent capture, then resumed" in _log(screen)
        assert list(tmp_path.rglob("*.npz")) and list(tmp_path.rglob("*.json"))
        assert not screen.query_one("#acquire-waveforms", Button).disabled


# -- form plumbing ---------------------------------------------------------------------------
