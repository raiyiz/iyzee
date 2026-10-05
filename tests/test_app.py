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
async def test_page_navigation_uses_a_shared_content_switcher() -> None:
    """Pages used to be independent Screens swapped via App.MODES; they're
    now widgets held inside one ContentSwitcher behind a persistent
    NavRail, so "isinstance(app.screen, XScreen)" no longer means
    anything — every page is mounted the whole time. Check which page is
    current instead, that the rail's active-item highlight tracks it, and
    that every page stays reachable from every other (hiding the current
    page's footer entry must not break navigation).
    """
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

        await pilot.press("s")
        assert switcher.current == "sweep"
        assert nav.query_one("#nav-sweep").has_class("-active")
        assert not nav.query_one("#nav-connect").has_class("-active")

        await pilot.press("t")
        assert switcher.current == "results"
        await pilot.press("i")
        assert switcher.current == "console"
        await pilot.pause()  # focus moves to the terminal after the switch settles
        assert pilot.app.screen.focused is not None
        assert pilot.app.screen.focused.id == "console-terminal"

        # From the console only the F-keys navigate (the terminal owns the letters).
        for key, page in [("f1", "connect"), ("f3", "results"), ("f2", "sweep"), ("f4", "console")]:
            await pilot.press(key)
            assert switcher.current == page
        await pilot.pause()
        assert pilot.app.screen.focused is not None
        assert pilot.app.screen.focused.id == "console-terminal"


@async_test
async def test_footer_offers_navigation_to_other_pages_only_and_always_quit() -> None:
    async with IyzeeApp().run_test() as pilot:
        await pilot.pause()

        def offered() -> set[str]:
            return set(pilot.app.screen.active_bindings)

        assert "c" not in offered() and {"s", "t", "i"} <= offered()
        await pilot.press("s")
        await pilot.pause()
        assert "s" not in offered() and {"c", "t", "i"} <= offered()
        assert "f2" not in offered() and "f1" in offered()  # the function keys behave the same

        binding = pilot.app.screen.active_bindings["ctrl+q"].binding
        assert binding.action == "quit" and binding.show is True, "Quit must be advertised"


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
async def test_q_asks_first_and_quits_on_the_second_press() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert app.is_running, "one stray q must not quit"
        assert any("Press q again" in message for message in notifications(app))
        await pilot.press("q")
        await pilot.pause(0.5)
    assert not app.is_running


@async_test
async def test_a_late_second_q_only_asks_again() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        app._quit_armed_until = 0.0  # the confirmation window has passed
        await pilot.press("q")
        await pilot.pause()
        assert app.is_running


@async_test
async def test_the_connect_page_focuses_its_instrument_list_when_shown() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert app.focused is app.query_one("#instrument-table")


@async_test
async def test_exiting_the_app_disconnects_every_connected_instrument() -> None:
    one, two = FakeHandle(), FakeHandle()
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.handles["one"], app.handles["two"] = one, two
        await pilot.press("ctrl+q")
        await pilot.pause(0.5)
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
async def test_a_link_that_dies_is_shown_as_lost_everywhere_and_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Until now nothing noticed: the row, the rail and the readiness banners
    kept saying "connected" and every later command failed with "not connected"."""
    handle = FakeHandle()
    app = connect_app(monkeypatch, handle, key="mxa")
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await wait_until(pilot, lambda: table.get_cell("mxa", STATUS_COL) == "connected")
        await pilot.pause()
        nav = app.query_one("#nav-instruments", Static)
        assert "● Fk" in plain(nav)
        lab = LabProxy(app)
        assert "mx" in lab.connected

        handle.alive = False
        app._check_links()
        await pilot.pause()

        assert "mxa" not in app.handles  # "in handles" keeps meaning "usable"
        assert table.get_cell("mxa", STATUS_COL) == "lost"
        assert "Enter to reconnect" in str(table.get_cell("mxa", DETAIL_COL))
        assert "○ Fk" in plain(nav)
        assert app.query_one("#sweep-status", Static).display
        assert any("connection lost" in m for m in notifications(app))
        await wait_until(pilot, lambda: handle.disconnect_calls > 0)  # the dead link was released
        assert "reconnect Fake" in plain(app.query_one("#connect-hint", Static))
        assert lab.connected == ()  # the console agrees
        with pytest.raises(AttributeError, match="not connected"):
            lab.mx


@async_test
async def test_a_lost_link_is_reported_once_and_a_reconnect_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead, fresh = FakeHandle(), FakeHandle(probe="back")
    app = connect_app(monkeypatch, dead, fresh, key="mxa")
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await wait_until(pilot, lambda: table.get_cell("mxa", STATUS_COL) == "connected")
        dead.alive = False
        app._check_links()
        app._check_links()  # the next poll must not announce it again
        await pilot.pause()
        assert len([m for m in notifications(app) if "connection lost" in m]) == 1
        assert "mxa" in app.lost_links

        await pilot.press("enter")  # Enter on a lost row is "reconnect"
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
