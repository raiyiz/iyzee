"""Tests for the app shell: page navigation, the footer, the nav rail, and shutdown."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from helpers import (
    FakeHandle,
    async_test,
    connect_app,
    enter_on_first_row,
    history_manager,
    notifications,
    plain,
    wait_until,
)
from textual.widgets import ContentSwitcher, Static

from iyzee.tui.app import IyzeeApp, NavRail
from iyzee.tui.ipython import LabProxy
from iyzee.tui.screens.connect import DETAIL_COL, STATUS_COL, ConnectScreen
from iyzee.tui.screens.console import ConsoleScreen, IyzeeConsole
from iyzee.tui.screens.results import ResultsScreen
from iyzee.tui.screens.sweep import SweepScreen


@async_test
async def test_page_navigation_and_footer_contracts() -> None:
    async with IyzeeApp().run_test() as pilot:
        switcher = pilot.app.screen.query_one(ContentSwitcher)
        nav = pilot.app.screen.query_one(NavRail)

        assert switcher.current == "connect"
        for page_id, page_type in [
            ("connect", ConnectScreen),
            ("sweep", SweepScreen),
            ("results", ResultsScreen),
            ("console", ConsoleScreen),
        ]:
            assert isinstance(switcher.get_child_by_id(page_id), page_type)

        def offered() -> set[str]:
            return set(pilot.app.screen.active_bindings)

        assert "c" not in offered() and {"s", "t", "i"} <= offered()
        binding = pilot.app.screen.active_bindings["ctrl+q"].binding
        assert binding.action == "quit" and binding.show is True

        await pilot.press("s")
        assert switcher.current == "sweep"
        assert nav.query_one("#nav-sweep").has_class("-active")
        assert not nav.query_one("#nav-connect").has_class("-active")
        assert "s" not in offered() and {"c", "t", "i"} <= offered()
        assert "f2" not in offered() and "f1" in offered()

        await pilot.press("t")
        assert switcher.current == "results"

        await pilot.press("i")
        assert switcher.current == "console"
        await wait_until(
            pilot, lambda: getattr(pilot.app.screen.focused, "id", None) == "console-terminal"
        )

        # From the console only the F-keys navigate; the terminal owns the letters.
        for key, page in [
            ("f1", "connect"),
            ("f3", "results"),
            ("f2", "sweep"),
            ("f4", "console"),
        ]:
            await pilot.press(key)
            assert switcher.current == page

        await pilot.press("f1")
        await pilot.pause()
        assert pilot.app.screen.focused is pilot.app.screen.query_one("#instrument-table")


@async_test
async def test_nav_rail_and_sweep_banner_update_the_moment_a_connect_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rail used to say ○ next to a table saying "connected", until you
    switched pages."""
    app = connect_app(monkeypatch, FakeHandle(), key="mxa")
    async with app.run_test() as pilot:
        nav = app.query_one("#nav-instruments", Static)
        assert "○ Fk" in plain(nav)
        screen, table = await enter_on_first_row(app, pilot)
        await wait_until(pilot, lambda: table.get_cell("mxa", STATUS_COL) == "connected")
        await pilot.pause()
        assert "● Fk" in plain(nav), plain(nav)  # no page switch in between
        assert not app.query_one("#sweep-status", Static).display


@async_test
async def test_console_history_defaults_to_memory_and_accepts_an_explicit_path(
    tmp_path: Path,
) -> None:
    """The app must keep the console history database isolated by default.

    ``IyzeeApp()`` passes ``console_history_file=None`` to the session, which
    must become ``:memory:`` rather than IPython's shared persistent history.
    That protects the whole test suite and each app instance from accidentally
    sharing ``~/.ipython/profile_default/history.sqlite``. The explicit-path
    case stays here because this test is specifically about the app-to-session
    wiring; the shell-level tests cover the history implementation itself.
    """
    default_app = IyzeeApp()
    async with default_app.run_test() as pilot:
        await pilot.press("i")
        console = default_app.screen.query_one(IyzeeConsole)
        assert history_manager(console.session.shell).hist_file == ":memory:"

    history_file = tmp_path / "console_history.sqlite"
    app = IyzeeApp(console_history_file=history_file)
    assert app.console_history_file == history_file
    async with app.run_test() as pilot:
        await pilot.press("i")
        console = app.screen.query_one(IyzeeConsole)
        assert history_manager(console.session.shell).hist_file == str(history_file)


@async_test
async def test_q_requires_a_second_immediate_press() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("q")
        await pilot.pause()
        assert app.is_running
        assert any("Press q again" in message for message in notifications(app))

        # A second press after the confirmation window must only re-arm it.
        app._quit_armed_until = 0.0
        await pilot.press("q")
        assert app.is_running

        # A genuinely immediate second press quits.
        await pilot.press("q")
        await wait_until(pilot, lambda: not app.is_running)

    assert not app.is_running


@async_test
async def test_exiting_the_app_disconnects_every_connected_instrument() -> None:
    one, two = FakeHandle(), FakeHandle()
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.handles["one"], app.handles["two"] = one, two
        await pilot.press("ctrl+q")
        await wait_until(pilot, lambda: not app.is_running)
    assert (one.disconnect_calls, two.disconnect_calls) == (1, 1)
    assert app.handles == {}


def test_a_failing_disconnect_does_not_stop_the_others_from_closing() -> None:
    app = IyzeeApp()
    broken, fine = FakeHandle(disconnect_error="link already gone"), FakeHandle()
    app.handles["broken"], app.handles["fine"] = broken, fine
    app.close_instruments(timeout=2.0)
    assert broken.disconnect_calls == 1 and fine.disconnect_calls == 1
    assert app.handles == {}


@pytest.mark.parametrize(
    "release_lock_after,timeout,busy_is_closed",
    [
        (None, 0.3, False),  # a step that never returns must not be able to block quitting
        (0.2, 3.0, True),  # a step that finishes soon is waited for, not torn down under
    ],
)
def test_close_instruments_waits_for_a_busy_instrument_but_never_hangs(
    release_lock_after: float | None, timeout: float, busy_is_closed: bool
) -> None:
    app = IyzeeApp()
    busy, idle = FakeHandle(), FakeHandle()
    app.handles["busy"], app.handles["idle"] = busy, idle
    lock = busy.lock
    lock.acquire()
    if release_lock_after is not None:
        threading.Timer(release_lock_after, lock.release).start()
    started = time.monotonic()
    try:
        app.close_instruments(timeout=timeout)
    finally:
        if release_lock_after is None:
            # The closer's own acquire() deadline is the same instant close_instruments
            # stops waiting, so releasing right away would race it (and let it win).
            time.sleep(0.2)
            lock.release()
    assert time.monotonic() - started < timeout + 2.0
    assert idle.disconnect_calls == 1, "the other instruments are still closed"
    assert (busy.disconnect_calls == 1) is busy_is_closed


# -- a link that dies while connected ------------------------------------------------------


@async_test
async def test_a_lost_link_is_reported_once_released_and_reconnectable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead, fresh = FakeHandle(), FakeHandle(probe="back")
    app = connect_app(monkeypatch, dead, fresh, key="mxa")
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await wait_until(pilot, lambda: table.get_cell("mxa", STATUS_COL) == "connected")
        nav = app.query_one("#nav-instruments", Static)
        lab = LabProxy(app)

        assert "● Fk" in plain(nav)
        assert "mx" in lab.connected

        dead.alive = False
        app._check_links()
        app._check_links()  # one poll must announce the loss only once
        await wait_until(pilot, lambda: table.get_cell("mxa", STATUS_COL) == "lost")
        await wait_until(pilot, lambda: any("connection lost" in m for m in notifications(app)))

        assert "mxa" not in app.handles
        assert "Enter to reconnect" in str(table.get_cell("mxa", DETAIL_COL))
        assert "○ Fk" in plain(nav)
        assert app.query_one("#sweep-status", Static).display
        assert len([m for m in notifications(app) if "connection lost" in m]) == 1

        await wait_until(pilot, lambda: dead.disconnect_calls > 0)
        assert "mxa" in app.lost_links
        assert "reconnect Fake" in plain(app.query_one("#connect-hint", Static))
        assert lab.connected == ()
        with pytest.raises(AttributeError, match="not connected"):
            lab.mx

        await pilot.press("enter")
        await wait_until(pilot, lambda: table.get_cell("mxa", STATUS_COL) == "connected")
        assert app.handles["mxa"] is fresh
        assert "mxa" not in app.lost_links
        assert table.get_cell("mxa", DETAIL_COL) == "back"


@async_test
async def test_handles_that_cannot_tell_are_never_dropped() -> None:
    class NoAlive:
        lock = threading.Lock()

    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.handles["x"] = NoAlive()  # type: ignore[assignment]

        app._check_links()

        assert "x" in app.handles
        app.handles.clear()  # nothing to disconnect on the way out
