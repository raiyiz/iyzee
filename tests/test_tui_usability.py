"""Tests for the TUI usability fixes (second round of the audit).

* Sweep: non-finite / absurd inputs are rejected and the offending field is
  marked and focused; a banner says what isn't connected; Abort acknowledges
  itself.
* Nav rail and Sweep banner follow connections live.
* Connect: disconnecting needs a confirming second Enter, is refused during a
  sweep, and the hint says what Enter will do; the Detail column is wide
  enough to show the instrument's identification string.
* Traces: the preview follows the highlight, the selection survives a
  rescan, and an empty folder explains itself.
* Console: Ctrl+J runs a cell (Shift+Enter isn't available in most
  terminals), the vim NORMAL mode is explained, output wraps to the pane.
* Footer: doesn't offer navigation to the page you're already on.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest
from rich.text import Text
from textual.containers import ContentSwitcher
from textual.widgets import Button, DataTable, Input, ListView, RichLog, Select, Static

from iyzee.experiment import StepResult, save_step_results
from iyzee.tui import app as app_mod
from iyzee.tui.instruments import InstrumentSpec
from iyzee.tui.screens import connect as connect_mod
from iyzee.tui.screens import sweep as sweep_mod
from iyzee.tui.screens import traces as traces_mod
from iyzee.tui.screens.connect import DETAIL_COL, STATUS_COL, ConnectScreen
from iyzee.tui.screens.sweep import (
    MAX_POINTS,
    FieldError,
    SweepScreen,
    _positive_float,
    _positive_int,
)

# -- helpers ---------------------------------------------------------------------------


async def _wait_until(pilot, predicate, *, timeout: float = 6.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition not reached within timeout")


class _Handle:
    """Instrument stand-in; ``device``/``shutter`` satisfy the Sweep screen."""

    device = object()
    shutter = object()

    def __init__(self, probe: str = "ready") -> None:
        self._probe = probe
        self.disconnect_calls = 0

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def probe(self) -> str:
        return self._probe


def _connect_app(monkeypatch: pytest.MonkeyPatch, handle: _Handle, key: str = "fake"):
    spec = InstrumentSpec(key=key, label="Fake", make=lambda: handle, short="Fk")
    monkeypatch.setattr(connect_mod, "INSTRUMENTS", [spec])
    monkeypatch.setattr(app_mod, "INSTRUMENTS", [spec])
    return app_mod.IyzeeApp()


async def _enter_on_first_row(app, pilot) -> tuple[ConnectScreen, DataTable]:
    await pilot.press("c")
    await pilot.pause()
    screen = app.query_one(ConnectScreen)
    table = screen.query_one(DataTable)
    table.cursor_coordinate = table.cursor_coordinate._replace(row=0)
    await pilot.press("enter")
    return screen, table


def _text(widget: Static) -> str:
    rendered = widget.render()
    assert isinstance(rendered, Text)
    return rendered.plain


def _messages(app) -> list[str]:
    return [n.message for n in app._notifications]


# -- Sweep: validation ----------------------------------------------------------------


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_non_finite_numbers_are_rejected(raw: str) -> None:
    # float() parses all of these, and nan even slips past a `<= 0` check.
    with pytest.raises(ValueError, match="RBW"):
        _positive_float(raw, "RBW")


def test_step_counts_have_an_upper_bound() -> None:
    assert _positive_int(str(MAX_POINTS), "Steps", maximum=MAX_POINTS) == MAX_POINTS
    with pytest.raises(ValueError, match=f"at most {MAX_POINTS}"):
        _positive_int(str(MAX_POINTS + 1), "Steps", maximum=MAX_POINTS)
    with pytest.raises(ValueError, match="Steps"):
        _positive_int("1000000000", "Steps", maximum=MAX_POINTS)
    # ...but the bound is opt-in: other integer fields (a channel number) keep working.
    assert _positive_int("1000000000", "x") == 1_000_000_000


@pytest.mark.parametrize(
    "field_id,bad,kind",
    [
        ("rbw-start", "nan", "bandwidth"),
        ("rbw-stop", "abc", "bandwidth"),
        ("rbw-steps", "1000000000", "bandwidth"),
        ("rbw-steps", "0", "bandwidth"),
        ("freq-points", "5000", "frequency"),
        ("freq-offset-khz", "inf", "frequency"),
    ],
)
def test_an_invalid_field_is_named_marked_and_focused(field_id: str, bad: str, kind: str) -> None:
    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            screen = app.query_one(SweepScreen)
            app.handles["mxa"] = _Handle()
            app.handles["shutter"] = _Handle()
            screen.query_one("#sweep-type", Select).value = kind
            await pilot.pause()
            field = screen.query_one(f"#{field_id}", Input)
            field.value = bad
            await pilot.pause()

            screen._start_sweep()
            await pilot.pause()

            assert field.has_class("-invalid")
            assert app.focused is field, "cursor should be in the field to fix"
            assert any(m.startswith("Invalid sweep parameters") for m in _messages(app))
            assert not app.sweep_running, "nothing must have started"

            # Editing it clears the marker again.
            field.value = "5"
            await pilot.pause()
            assert not field.has_class("-invalid")

    asyncio.run(scenario())


def test_field_error_is_still_a_value_error() -> None:
    """Callers (and older tests) that catch ValueError keep working."""
    error = FieldError("rbw-steps", "Steps must be positive")
    assert isinstance(error, ValueError)
    assert error.field_id == "rbw-steps"


# -- Sweep: "not ready" banner + live nav rail ----------------------------------------


def test_sweep_banner_tracks_the_connections_it_needs() -> None:
    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            screen = app.query_one(SweepScreen)
            banner = screen.query_one("#sweep-status", Static)

            assert banner.display, "nothing connected -> the reason must be on screen"
            assert "MXA" in _text(banner) and "F1" in _text(banner)

            app.handles["mxa"] = _Handle()
            app.instruments_changed()
            await pilot.pause()
            assert not banner.display, "bandwidth sweep only needs the MXA"

            # A frequency sweep also needs the shutter.
            screen.query_one("#sweep-type", Select).value = "frequency"
            await pilot.pause()
            assert banner.display
            assert "shutter" in _text(banner) and "MXA" not in _text(banner)

            app.handles["shutter"] = _Handle()
            app.instruments_changed()
            await pilot.pause()
            assert not banner.display

            del app.handles["mxa"]
            app.instruments_changed()
            await pilot.pause()
            assert banner.display and "MXA" in _text(banner)

    asyncio.run(scenario())


def test_nav_rail_and_banner_update_the_moment_a_connect_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rail used to say ○ next to a table saying "connected", until you
    switched pages."""
    app = _connect_app(monkeypatch, _Handle(), key="mxa")

    async def scenario() -> None:
        async with app.run_test() as pilot:
            nav = app.query_one("#nav-instruments", Static)
            assert "○ Fk" in _text(nav)

            screen, table = await _enter_on_first_row(app, pilot)
            await _wait_until(pilot, lambda: table.get_cell("mxa", STATUS_COL) == "connected")
            await pilot.pause()
            assert "● Fk" in _text(nav), _text(nav)  # no page switch in between
            assert not app.query_one("#sweep-status", Static).display

    asyncio.run(scenario())


def test_nav_rail_keeps_the_dot_on_the_same_line_as_the_name() -> None:
    """Long labels wrapped and stranded the dot on its own line."""

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            lines = _text(app.query_one("#nav-instruments", Static)).splitlines()
            entries = [line for line in lines if line.startswith(("○", "●"))]
            assert len(entries) == len(app_mod.INSTRUMENTS)
            assert all(len(line) > 2 for line in entries), entries  # dot *and* a name
            assert any("Instruments" in line for line in lines), "block has a heading"

    asyncio.run(scenario())


# -- Sweep: abort feedback --------------------------------------------------------------


def test_abort_is_acknowledged_and_takes_effect_at_the_next_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started, release = threading.Event(), threading.Event()

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        started.set()
        release.wait(5)
        result = StepResult(
            label="pt0",
            x_value=0.0,
            x_unit="Hz",
            traces={"squeezing": [1.0], "shot_noise": [1.0]},
            meta={},
        )
        on_step(0, 3, object(), result, None)  # abort is honoured here

    monkeypatch.setattr(sweep_mod, "create_dirs", lambda name="": tmp_path)
    monkeypatch.setattr(sweep_mod, "prepare_analyzer", lambda *a, **k: None)
    monkeypatch.setattr(sweep_mod, "run_sequence", fake_run_sequence)

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        app.handles["mxa"] = _Handle()
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            await pilot.click("#run-sweep")
            await _wait_until(pilot, started.is_set)
            assert app.sweep_running

            abort = app.query_one("#abort-sweep", Button)
            await pilot.click("#abort-sweep")
            await pilot.pause()
            assert str(abort.label) == "Aborting…"
            assert abort.disabled
            log = app.query_one("#sweep-log", RichLog)
            assert "Abort requested" in " ".join(str(seg) for line in log.lines for seg in line)

            release.set()
            await _wait_until(pilot, lambda: app.last_run is not None)
            await _wait_until(pilot, lambda: not app.sweep_running)
            assert str(abort.label) == "Abort", "label restored for the next run"
            last_run = app.last_run
            assert last_run is not None
            assert len(last_run.results) == 1

    asyncio.run(scenario())


# -- Connect: confirm before disconnecting ------------------------------------------------


def test_first_enter_on_a_connected_row_only_asks_for_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _Handle()
    app = _connect_app(monkeypatch, handle)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            screen, table = await _enter_on_first_row(app, pilot)
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "connected")

            await pilot.press("enter")  # arms
            await pilot.pause(0.2)
            assert "fake" in app.handles and handle.disconnect_calls == 0
            assert any("Press Enter again to disconnect" in m for m in _messages(app))

            await pilot.press("enter")  # confirms
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "disconnected")
            assert handle.disconnect_calls == 1 and "fake" not in app.handles

    asyncio.run(scenario())


def test_an_expired_confirmation_does_not_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = _Handle()
    app = _connect_app(monkeypatch, handle)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            screen, table = await _enter_on_first_row(app, pilot)
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "connected")
            await pilot.press("enter")  # arms
            await pilot.pause(0.1)
            armed = screen._armed
            assert armed is not None
            key, _deadline = armed
            screen._armed = (key, time.monotonic() - 1)  # the window has passed

            await pilot.press("enter")  # must re-arm, not disconnect
            await pilot.pause(0.2)
            assert handle.disconnect_calls == 0 and "fake" in app.handles

    asyncio.run(scenario())


def test_disconnect_is_refused_while_a_sweep_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = _Handle()
    app = _connect_app(monkeypatch, handle)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            screen, table = await _enter_on_first_row(app, pilot)
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "connected")
            app.sweep_running = True

            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert handle.disconnect_calls == 0 and "fake" in app.handles
            assert any("sweep is running" in m for m in _messages(app))

    asyncio.run(scenario())


def test_connect_hint_says_what_enter_will_do(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _connect_app(monkeypatch, _Handle())

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("c")
            await pilot.pause()
            hint = app.query_one("#connect-hint", Static)
            assert "connect Fake" in _text(hint) and "disconnect" not in _text(hint)

            await pilot.press("enter")
            table = app.query_one(DataTable)
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "connected")
            await pilot.pause()
            assert "disconnect Fake" in _text(hint)

    asyncio.run(scenario())


def test_detail_column_is_wide_enough_for_the_identification_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idn = "KEYSIGHT TECHNOLOGIES,N9020B,MY57120001,A.29.05"
    app = _connect_app(monkeypatch, _Handle(probe=idn))

    async def scenario() -> None:
        async with app.run_test(size=(140, 30)) as pilot:
            screen, table = await _enter_on_first_row(app, pilot)
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "connected")
            await pilot.pause()
            column = table.ordered_columns[table.get_column_index(DETAIL_COL)]
            width = column.get_render_width(table)
            assert width >= len(idn), f"Detail column is {width} wide, string is {len(idn)}"

    asyncio.run(scenario())


# -- Traces ----------------------------------------------------------------------------------


def _save_run(root: Path, folder: str, mtime: float, label: str = "pt0") -> Path:
    directory = root / folder
    directory.mkdir()
    path = save_step_results(
        [
            StepResult(
                label=label,
                x_value=1.0,
                x_unit="Hz",
                traces={"squeezing": [1.0, 2.0], "shot_noise": [1.5, 1.5]},
                meta={},
            )
        ],
        directory,
    )
    os.utime(path, (mtime, mtime))
    return path


def test_traces_preview_follows_the_highlight(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    old = _save_run(tmp_path, "2026-09-17_bandwidth", 1_000)
    new = _save_run(tmp_path, "2026-09-18_frequency", 2_000)
    monkeypatch.setattr(traces_mod, "_DATA_ROOT", tmp_path)

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("t")
            await pilot.pause(0.3)
            summary = app.query_one("#traces-summary", Static)

            # On arrival the newest run is highlighted *and* previewed
            # (it used to say "Select a run to preview it.").
            assert new.name in _text(summary)

            app.query_one("#traces-list", ListView).focus()
            await pilot.press("down")  # no Enter needed
            await pilot.pause(0.3)
            assert old.name in _text(summary)

    asyncio.run(scenario())


def test_traces_selection_survives_leaving_and_returning(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _save_run(tmp_path, "2026-09-17_bandwidth", 1_000)
    _save_run(tmp_path, "2026-09-18_frequency", 2_000)
    monkeypatch.setattr(traces_mod, "_DATA_ROOT", tmp_path)

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("t")
            await pilot.pause(0.3)
            listing = app.query_one("#traces-list", ListView)
            listing.focus()
            await pilot.press("down")
            await pilot.pause(0.3)
            assert listing.index == 1
            shown = _text(app.query_one("#traces-summary", Static))

            await pilot.press("escape")
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause(0.5)
            # Used to snap back to row 0 while the preview still showed row 1.
            assert listing.index == 1
            assert _text(app.query_one("#traces-summary", Static)) == shown

    asyncio.run(scenario())


def test_traces_empty_folder_explains_itself_and_names_the_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(traces_mod, "_DATA_ROOT", tmp_path)

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("t")
            await pilot.pause(0.3)
            assert "No runs recorded yet" in _text(app.query_one("#traces-summary", Static))
            hint = _text(app.query_one("#traces-hint", Static))
            assert str(tmp_path) in hint

    asyncio.run(scenario())


def test_traces_list_shows_the_time_of_day_not_the_raw_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    run = tmp_path / "2026-09-18_bandwidth"
    run.mkdir()
    save_step_results([StepResult(label="p", x_value=1.0, x_unit="Hz", traces={}, meta={})], run)
    only = next(run.glob("*.npz"))
    only.rename(run / "20260918T141005.npz")
    monkeypatch.setattr(traces_mod, "_DATA_ROOT", tmp_path)

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("t")
            await pilot.pause(0.3)
            item = app.query_one("#traces-list", ListView).children[0]
            rendered = item.query_one("Label").render()
            assert isinstance(rendered, Text)
            label = rendered.plain
            assert label == "2026-09-18_bandwidth  14:10:05", label

    asyncio.run(scenario())


# -- Footer ----------------------------------------------------------------------------------


def test_footer_does_not_offer_navigation_to_the_current_page() -> None:
    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            def offered() -> set[str]:
                return set(app.screen.active_bindings)

            assert "c" not in offered() and {"s", "t", "i"} <= offered()
            await pilot.press("s")
            await pilot.pause()
            assert "s" not in offered() and {"c", "t", "i"} <= offered()
            # Function keys behave the same, and Ctrl+Q is always there.
            assert "f2" not in offered() and "f1" in offered()
            assert "ctrl+q" in offered()

    asyncio.run(scenario())


def test_every_page_stays_reachable_from_every_other_page() -> None:
    """Hiding the current page's entry must not break navigation."""

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            switcher = app.query_one("#page-switcher", ContentSwitcher)
            for key, page in [("s", "sweep"), ("t", "traces"), ("c", "connect"), ("s", "sweep")]:
                await pilot.press(key)
                await pilot.pause()
                assert switcher.current == page
            for key, page in [("f3", "traces"), ("f1", "connect"), ("f4", "console")]:
                await pilot.press(key)
                await pilot.pause()
                assert switcher.current == page

    asyncio.run(scenario())
