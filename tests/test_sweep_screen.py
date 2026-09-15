"""Tests for SweepScreen's input validation and pre-flight guards.

Deliberately does not drive an actual @work(thread=True) sweep against a
fake MXA — that would mean re-implementing prepare_analyzer/run_sequence
just to exercise this screen, which is what experiment/'s own test suite
already covers. What's untested (and where a typo is easy to ship) is the
form-parsing and the "did you connect the right instruments first"
guards, both of which are plain synchronous methods.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Input, Select

from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.sweep import SweepScreen, _positive_float, _positive_int

# -- standalone validators -------------------------------------------------


@pytest.mark.parametrize("raw,expected", [("20000", 20000.0), ("0.5", 0.5), ("  12  ", 12.0)])
def test_positive_float_accepts_valid_input(raw: str, expected: float) -> None:
    assert _positive_float(raw, "field") == expected


@pytest.mark.parametrize("raw", ["0", "-5", "abc", ""])
def test_positive_float_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(ValueError, match="field"):
        _positive_float(raw, "field")


@pytest.mark.parametrize("raw", ["0", "-1", "3.5", "abc"])
def test_positive_int_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(ValueError, match="field"):
        _positive_int(raw, "field")


def test_positive_int_accepts_valid_input() -> None:
    assert _positive_int("19", "field") == 19


# -- form building, via a mounted screen -------------------------------------------------


class _FakeHandle:
    """A minimal InstrumentHandle stand-in — just enough to make
    `"mxa" in app.handles` true for the pre-flight-guard tests below,
    which return before ever calling connect/disconnect/probe."""

    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def probe(self) -> str:
        raise NotImplementedError


async def _open_sweep_screen(pilot) -> SweepScreen:
    await pilot.press("s")
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, SweepScreen)
    return screen


def test_build_bandwidth_run_uses_form_defaults() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            steps, config = screen._build_bandwidth_run()
            assert len(steps) == 19  # matches the default "Steps" field
            assert config.res_bw_hz == pytest.approx(20000.0)  # first RBW value

    asyncio.run(scenario())


def test_build_bandwidth_run_rejects_bad_input() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            screen.query_one("#rbw-steps", Input).value = "0"
            with pytest.raises(ValueError, match="Steps"):
                screen._build_bandwidth_run()

    asyncio.run(scenario())


def test_build_frequency_run_uses_form_defaults() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            steps, config = screen._build_frequency_run()
            assert len(steps) == 5  # matches the default "Points" field

    asyncio.run(scenario())


# -- pre-flight guards -------------------------------------------------


def test_start_sweep_without_mxa_notifies_and_does_not_launch_worker() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            notifications: list[str] = []
            screen.notify = lambda message, **kwargs: notifications.append(message)  # type: ignore[assignment]

            screen._start_sweep()

            assert notifications, "expected a notification asking to connect the MXA"
            assert "Connect the MXA" in notifications[0]
            # The run button must stay enabled — nothing was launched.
            assert screen.query_one("#run-sweep").disabled is False

    asyncio.run(scenario())


def test_start_frequency_sweep_without_shutter_notifies() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            app.handles["mxa"] = _FakeHandle()  # MXA "connected" for this check only
            screen.query_one("#sweep-type", Select).value = "frequency"
            await pilot.pause()

            notifications: list[str] = []
            screen.notify = lambda message, **kwargs: notifications.append(message)  # type: ignore[assignment]

            screen._start_sweep()

            assert notifications, "expected a notification asking to connect the shutter"
            assert "shutter" in notifications[0].lower()

    asyncio.run(scenario())
