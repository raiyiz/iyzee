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
    return pilot.app.query_one(SweepScreen)


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


# -- "Capture trace" ---------------------------------------------------------


class _FakeMxaForCapture:
    """A fake MXA supporting exactly what acquire_trace()/get_frequency_axis()
    call — enough to drive a real _capture() worker end-to-end without a
    live instrument."""

    def __init__(self, power: list[float], freq: list[float]) -> None:
        self._power = power
        self._freq = freq
        self.trace_updates: list[tuple[int, bool]] = []

    def set_trace_update(self, trace_num: int, state: bool) -> None:
        self.trace_updates.append((trace_num, state))

    def single_sweep_wait(self) -> bool:
        return True

    def get_trace_data(self, trace_num: int = 1, binary: bool = True) -> list[float]:
        return self._power

    def get_frequency_axis(self) -> list[float]:
        return self._freq


def test_capture_trace_without_mxa_notifies_and_does_not_launch_worker() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            notifications: list[str] = []
            screen.notify = lambda message, **kwargs: notifications.append(message)  # type: ignore[assignment]

            screen._start_capture()

            assert notifications, "expected a notification asking to connect the MXA"
            assert "Connect the MXA" in notifications[0]
            assert screen.query_one("#capture-trace").disabled is False

    asyncio.run(scenario())


def test_capture_trace_plots_power_vs_frequency(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        fake_mxa = _FakeMxaForCapture(power=[-70.0, -68.0, -71.0], freq=[1e6, 1.1e6, 1.2e6])
        app.handles["mxa"] = _FakeVisaHandle(fake_mxa)

        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)

            screen._start_capture()
            # _capture runs in a background worker thread; give it a moment
            # to finish and call back into the UI thread.
            for _ in range(50):
                await pilot.pause(0.05)
                if not screen.query_one("#capture-trace").disabled:
                    break

            assert fake_mxa.trace_updates == [(1, True), (1, False)]
            log_text = " ".join(
                str(seg) for line in screen.query_one("#sweep-log").lines for seg in line
            )
            assert "Captured 3 point(s)" in log_text

    asyncio.run(scenario())


class _FakeVisaHandle:
    """Minimal stand-in for instruments._VisaHandle: just needs `.device`."""

    def __init__(self, device) -> None:
        self.device = device
