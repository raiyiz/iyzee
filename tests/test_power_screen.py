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
async def test_without_a_psu_connection_the_page_says_so_and_offers_no_controls() -> None:
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot)

        assert screen.query_one("#power-status", Static).display
        assert "connect the PSU" in _text(screen, "power-status")
        for widget_id in ("power-global", "power-shutter", "power-refresh", "power-apply1"):
            assert _button(screen, widget_id).disabled


@async_test
async def test_connecting_reads_the_supply_into_the_page_and_the_form() -> None:
    psu = FakePSU()
    psu.selected[1] = True
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)

        assert not screen.query_one("#power-status", Static).display
        assert "Global output: off" in _text(screen, "power-summary")
        assert "armed" in _text(screen, "power-readout1")  # armed, master still off
        assert "5.000 V" in _text(screen, "power-readout1")
        assert screen.query_one("#power-v1", Input).value == "5"
        assert screen.query_one("#power-i2", Input).value == "0.5"
        assert str(_button(screen, "power-toggle1").label) == "Disable CH1"
        assert str(_button(screen, "power-toggle2").label) == "Enable CH2"
        assert psu.writes() == []  # reading never changes anything


@async_test
async def test_global_output_toggles_and_the_channel_goes_live() -> None:
    psu = FakePSU()
    psu.selected[1] = True
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)

        _button(screen, "power-global").press()
        await wait_until(pilot, lambda: psu.general and not screen._busy)
        assert "Global output: ON" in _text(screen, "power-summary")
        assert "ON" in _text(screen, "power-readout1") and "armed" not in _text(
            screen, "power-readout1"
        )
        assert str(_button(screen, "power-global").label) == "Turn global output OFF"

        _button(screen, "power-global").press()
        await wait_until(pilot, lambda: not psu.general and not screen._busy)
        assert "Global output: off" in _text(screen, "power-summary")
        assert psu.writes() == ["enable_global_output", "disable_global_output"]


@async_test
async def test_channel_switch_enables_and_disables_only_that_channel() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)

        _button(screen, "power-toggle2").press()
        await wait_until(pilot, lambda: psu.selected[2] and not screen._busy)
        assert str(_button(screen, "power-toggle2").label) == "Disable CH2"
        assert psu.selected == {1: False, 2: True, 3: False, 4: False}

        _button(screen, "power-toggle2").press()
        await wait_until(pilot, lambda: not psu.selected[2] and not screen._busy)
        assert psu.calls == [("enable_output", (2,)), ("disable_output", (2,))]


@async_test
async def test_apply_sets_voltage_and_current_and_shows_what_the_supply_reports() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)

        _set(screen, "power-v4", "9.5")
        _set(screen, "power-i4", "0.25")
        _button(screen, "power-apply4").press()
        await wait_until(pilot, lambda: psu.voltage[4] == 9.5 and not screen._busy)

        assert psu.current[4] == 0.25
        assert psu.calls == [("set_voltage", (4, 9.5)), ("set_current", (4, 0.25))]
        assert "9.500 V" in _text(screen, "power-readout4")
        assert "Done: set CH4 to 9.5 V / 0.25 A" in _log(screen)


@async_test
async def test_a_bad_setpoint_is_flagged_and_nothing_is_sent() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, psu)

        for voltage, current in (("abc", "1"), ("40", "1"), ("5", "11"), ("-1", "1"), ("nan", "1")):
            _set(screen, "power-v1", voltage)
            _set(screen, "power-i1", current)
            await pilot.pause()  # let the edit settle: editing clears an invalid marker
            _button(screen, "power-apply1").press()
            await pilot.pause()
            assert psu.writes() == [], (voltage, current)

        assert screen.query_one("#power-i1", Input).has_class("-invalid") or screen.query_one(
            "#power-v1", Input
        ).has_class("-invalid")
        assert any("between 0 and" in n or "number" in n for n in notifications(app))
        assert not screen._busy


@async_test
async def test_shutter_button_opens_and_closes_channel_three() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)
        assert str(_button(screen, "power-shutter").label) == "Open shutter"

        # Opening needs the master output; with it off the channel is only armed.
        _button(screen, "power-shutter").press()
        await wait_until(pilot, lambda: psu.selected[3] and not screen._busy)
        assert "closed until global output is on" in _text(screen, "power-summary")

        _button(screen, "power-global").press()
        await wait_until(pilot, lambda: psu.general and not screen._busy)
        assert "OPEN" in _text(screen, "power-summary")
        assert str(_button(screen, "power-shutter").label) == "Close shutter"

        _button(screen, "power-shutter").press()
        await wait_until(pilot, lambda: not psu.selected[3] and not screen._busy)
        assert "closed" in _text(screen, "power-summary")


@async_test
async def test_a_refused_change_is_reported_and_the_real_state_is_still_shown() -> None:
    psu = FakePSU(fail_on="enable_output")
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, psu)

        _button(screen, "power-toggle1").press()
        await wait_until(pilot, lambda: "Could not enable CH1" in _log(screen))

        assert "refused [by test]" in _log(screen)
        assert any("Could not enable CH1" in n for n in notifications(app))
        assert str(_button(screen, "power-toggle1").label) == "Enable CH1"  # nothing changed
        assert not screen._busy and not _button(screen, "power-toggle1").disabled


@async_test
async def test_controls_are_locked_while_a_sweep_runs() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, psu)

        app.sweep_running = True
        screen._refresh_ui()
        assert _button(screen, "power-global").disabled
        assert _button(screen, "power-toggle1").disabled
        assert "a sweep is running" in _text(screen, "power-summary")

        screen._toggle_global()  # even if something reaches the handler directly
        await pilot.pause()
        assert psu.writes() == []

        app.sweep_running = False  # ends with a read of the supply, then the controls return
        await wait_until(pilot, lambda: not screen._busy)
        assert not _button(screen, "power-global").disabled


@async_test
async def test_losing_the_connection_clears_the_page() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, psu)

        del app.handles["psu"]
        app.instruments_changed()
        await pilot.pause()

        assert screen.query_one("#power-status", Static).display
        assert _text(screen, "power-readout1") == "-"
        assert _button(screen, "power-global").disabled


@async_test
async def test_the_page_works_on_the_psu_alone_and_all_four_channels_are_free() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu, shutter=False)

        assert "Shutter: not connected" in _text(screen, "power-summary")
        assert _button(screen, "power-shutter").disabled
        assert not _button(screen, "power-toggle3").disabled
        assert not screen.query_one("#power-v3", Input).disabled

        _button(screen, "power-toggle3").press()
        await wait_until(pilot, lambda: psu.selected[3] and not screen._busy)


@async_test
async def test_the_shutters_channel_is_claimed_while_the_shutter_is_connected() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, psu)

        assert "use the Shutter button" in _text(screen, "power-title3")
        assert _button(screen, "power-toggle3").disabled
        assert _button(screen, "power-apply3").disabled
        assert screen.query_one("#power-v3", Input).disabled
        assert not _button(screen, "power-toggle1").disabled  # the other channels are untouched

        screen._toggle_channel(3)  # even if something reaches the handler directly
        screen._apply_channel(3)
        await pilot.pause()
        assert psu.writes() == []

        # Disconnecting the shutter frees its channel again; the page stays up on the PSU.
        del app.handles["shutter"]
        app.instruments_changed()
        await pilot.pause()
        assert not _button(screen, "power-toggle3").disabled
        assert "use the Shutter button" not in _text(screen, "power-title3")


@async_test
async def test_the_page_never_polls_the_supply_in_the_background() -> None:
    """Regression: a timer-driven read kept the page 'working' with its controls off."""
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)
        reads = psu.reads
        assert reads >= 1

        await pilot.pause(0.6)  # far longer than any plausible refresh rate in a test

        assert psu.reads == reads
        assert not screen._busy
        assert not _button(screen, "power-global").disabled
        assert not _button(screen, "power-toggle1").disabled


@async_test
async def test_opening_the_page_again_reads_what_the_console_changed_meanwhile() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        screen = await _open(pilot, psu)
        assert "Global output: off" in _text(screen, "power-summary")
        _set(screen, "power-v1", "6")  # a half-typed edit

        await pilot.press("s")  # away ...
        await pilot.pause()
        psu.general = True  # ... the supply is changed from the console
        psu.selected[1] = True
        await pilot.press("p")  # ... and back
        await wait_until(pilot, lambda: "Global output: ON" in _text(screen, "power-summary"))

        assert "ON" in _text(screen, "power-readout1")
        assert screen.query_one("#power-v1", Input).value == "6"  # typing is never overwritten


@async_test
async def test_a_finished_sweep_is_followed_by_a_read_and_the_controls_come_back() -> None:
    psu = FakePSU()
    async with IyzeeApp().run_test() as pilot:
        app = pilot.app
        assert isinstance(app, IyzeeApp)
        screen = await _open(pilot, psu)

        app.sweep_running = True
        await pilot.pause()
        assert _button(screen, "power-global").disabled
        assert "a sweep is running" in _text(screen, "power-summary")
        reads = psu.reads

        psu.selected[3] = True  # the sweep left things moved
        app.sweep_running = False
        await wait_until(pilot, lambda: psu.reads > reads and not screen._busy)

        assert not _button(screen, "power-global").disabled
        assert "a sweep is running" not in _text(screen, "power-summary")
        assert "armed" in _text(screen, "power-readout3")
