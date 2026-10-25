"""The Power page: what the supply reports, and the switches and setpoints that change it.

The fake supply is stateful (it remembers writes, like the real one) so the tests can check
what the page *shows afterwards*, which is what the page promises: the read-back, not the
request.
"""

from __future__ import annotations

import threading
from typing import Any

from helpers import FakeHandle, async_test, notifications, wait_until
from textual.pilot import Pilot
from textual.widgets import Button, Input, RichLog, Static

from iyzee.devices.power import ChannelState, PSUState, ShutterControl
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.power import PowerScreen


class FakePSU:
    """Remembers what it was told and reports it back, like the real supply."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.general = False
        self.voltage = {1: 5.0, 2: 3.3, 3: 1.7, 4: 12.0}
        self.current = {1: 1.0, 2: 0.5, 3: 0.01, 4: 2.0}
        self.selected = {1: False, 2: False, 3: False, 4: False}
        self.calls: list[tuple[str, Any]] = []
        self.fail_on = fail_on
        self.lock_probe: threading.Lock | None = None
        self.reads = 0
        self.reads = 0  # how many times the state has been read
        self.read_gate: threading.Event | None = None  # when set (an Event), reads wait for it
        self.fail_reads = False

    def _do(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if self.fail_on == name:
            raise ConnectionError(f"{name} refused [by test]")

    def retrieve_state(self) -> PSUState:
        self.reads += 1
        if self.read_gate is not None:  # a slow supply: a full read is ~22 round-trips
            self.read_gate.wait(10)
        if self.fail_reads:
            raise TimeoutError("the supply did not answer [by test]")
        return PSUState(
            general_output=self.general,
            channels=tuple(
                ChannelState(
                    channel=n,
                    voltage_set=self.voltage[n],
                    current_set=self.current[n],
                    voltage_measured=self.voltage[n] if self.selected[n] and self.general else 0.0,
                    current_measured=0.0,
                    selected=self.selected[n],
                )
                for n in (1, 2, 3, 4)
            ),
        )

    def set_voltage(self, voltage: float, channel: int) -> None:
        self._do("set_voltage", channel, voltage)
        self.voltage[channel] = voltage

    def set_current(self, current: float, channel: int) -> None:
        self._do("set_current", channel, current)
        self.current[channel] = current

    def enable_output(self, channel: int) -> None:
        self._do("enable_output", channel)
        self.selected[channel] = True

    def disable_output(self, channel: int) -> None:
        self._do("disable_output", channel)
        self.selected[channel] = False

    def enable_global_output(self) -> None:
        self._do("enable_global_output")
        self.general = True

    def disable_global_output(self) -> None:
        self._do("disable_global_output")
        self.general = False

    def writes(self) -> list[str]:
        return [name for name, _ in self.calls]


def _shutter(psu: FakePSU) -> ShutterControl:
    """A real ``ShutterControl`` (so open()/close() are the real ones) on the fake supply."""
    return ShutterControl(psu, chan=3)  # type: ignore[arg-type]


def _text(screen: PowerScreen, widget_id: str) -> str:
    return str(screen.query_one(f"#{widget_id}", Static).render())


def _log(screen: PowerScreen) -> str:
    return "\n".join(strip.text for strip in screen.query_one("#power-log", RichLog).lines)


def _button(screen: PowerScreen, widget_id: str) -> Button:
    return screen.query_one(f"#{widget_id}", Button)


def _set(screen: PowerScreen, widget_id: str, value: str) -> None:
    screen.query_one(f"#{widget_id}", Input).value = value


async def _open(
    pilot: Pilot[Any], psu: FakePSU | None = None, *, shutter: bool = True
) -> PowerScreen:
    """Open the page on a connected PSU; the shutter (a part of it) is connected too by default."""
    app = pilot.app
    assert isinstance(app, IyzeeApp)
    if psu is not None:
        app.handles["psu"] = FakeHandle(device=psu)
        if shutter:
            app.handles["shutter"] = FakeHandle(device=_shutter(psu))
    await pilot.press("p")
    await pilot.pause()
    screen = app.query_one(PowerScreen)
    if psu is not None:
        await wait_until(pilot, lambda: screen._synced and not screen._busy)
    return screen


@async_test
async def test_controls_and_invalid_setpoints_are_safe() -> None:
    psu = FakePSU()
    psu.selected[1] = True
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)
        app = pilot.app
        assert isinstance(app, IyzeeApp)

        # Connecting reads the supply into the page and the form without changing anything.
        assert "Global output: off" in _text(screen, "power-summary")
        assert "armed" in _text(screen, "power-readout1")
        assert screen.query_one("#power-v1", Input).value == "5"
        assert psu.writes() == []

        async def press(widget_id: str, done: Any) -> None:
            _button(screen, widget_id).press()
            await wait_until(pilot, lambda: done() and not screen._busy)

        await press("power-global", lambda: psu.general)
        assert "Global output: ON" in _text(screen, "power-summary")
        await press("power-toggle2", lambda: psu.selected[2])
        assert str(_button(screen, "power-toggle2").label) == "Disable CH2"
        await press("power-shutter", lambda: psu.selected[3])  # master is on: it opens
        assert "OPEN" in _text(screen, "power-summary")

        _set(screen, "power-v4", "9.5")
        _set(screen, "power-i4", "0.25")
        await press("power-apply4", lambda: psu.voltage[4] == 9.5)
        assert psu.current[4] == 0.25 and "9.500 V" in _text(screen, "power-readout4")

        await press("power-shutter", lambda: not psu.selected[3])
        await press("power-global", lambda: not psu.general)
        assert psu.selected == {1: True, 2: True, 3: False, 4: False}

        # Reopen after the simulated supply changed out-of-band, then verify validation
        # and a refused hardware command in the same app session.
        psu.selected[1] = False
        psu.fail_on = "enable_output"
        reads = psu.reads
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("p")
        await wait_until(pilot, lambda: psu.reads > reads and not screen._busy)

        writes_before = psu.writes()
        for voltage, current in (
            ("abc", "1"),
            ("40", "1"),
            ("5", "11"),
            ("-1", "1"),
            ("nan", "1"),
        ):
            _set(screen, "power-v1", voltage)
            _set(screen, "power-i1", current)
            await pilot.pause()  # let the edit settle: editing clears an invalid marker
            _button(screen, "power-apply1").press()
            await pilot.pause()
            assert psu.writes() == writes_before, (voltage, current)

        _button(screen, "power-toggle1").press()  # the supply refuses this one
        await wait_until(pilot, lambda: "Could not enable CH1" in _log(screen))
        assert "refused [by test]" in _log(screen)
        assert any("Could not enable CH1" in n for n in notifications(app))
        assert str(_button(screen, "power-toggle1").label) == "Enable CH1"
        assert not screen._busy and not _button(screen, "power-toggle1").disabled


@async_test
async def test_the_page_follows_connections_and_sweeps() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)

        screen = await _open(pilot)  # no PSU yet
        assert "connect the PSU" in _text(screen, "power-status")
        assert _button(screen, "power-global").disabled

        app.handles["psu"] = FakeHandle(device=psu)  # the PSU alone: every channel is free
        app.instruments_changed()
        await wait_until(pilot, lambda: screen._synced and not screen._busy)
        assert "Shutter: not connected" in _text(screen, "power-summary")
        assert not _button(screen, "power-toggle3").disabled

        app.handles["shutter"] = FakeHandle(device=_shutter(psu))  # the shutter claims CH3
        app.instruments_changed()
        await pilot.pause()
        assert (
            _button(screen, "power-toggle3").disabled and _button(screen, "power-apply3").disabled
        )
        screen._toggle_channel(3)  # even if something reaches the handler directly
        await pilot.pause()
        assert psu.writes() == []

        app.sweep_running = True  # a sweep locks the controls...
        await pilot.pause()
        assert _button(screen, "power-global").disabled
        reads = psu.reads
        psu.selected[3] = True  # ... moves the shutter ...
        app.sweep_running = False  # ... and when it ends the page reads the supply again
        await wait_until(pilot, lambda: psu.reads > reads and not screen._busy)
        assert "armed" in _text(screen, "power-readout3")
        assert not _button(screen, "power-global").disabled

        del app.handles["psu"]
        app.instruments_changed()
        await pilot.pause()
        assert _text(screen, "power-readout1") == "-" and _button(screen, "power-global").disabled


@async_test
async def test_the_page_never_polls_but_reading_again_when_reopened() -> None:
    """Regression: a timer-driven read kept the page 'working' with its controls off."""
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)
        reads = psu.reads
        await pilot.pause(0.6)
        assert psu.reads == reads and not _button(screen, "power-global").disabled

        _set(screen, "power-v1", "6")  # a half-typed edit
        await pilot.press("s")
        await pilot.pause()
        psu.general = True  # changed from the console meanwhile
        await pilot.press("p")
        await wait_until(pilot, lambda: "Global output: ON" in _text(screen, "power-summary"))
        assert screen.query_one("#power-v1", Input).value == "6"  # typing is never overwritten
