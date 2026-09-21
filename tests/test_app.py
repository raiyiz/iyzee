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
    plain,
    wait_until,
)
from textual.widgets import ContentSwitcher, Static

from iyzee.tui import app as app_mod
from iyzee.tui.app import IyzeeApp, NavRail
from iyzee.tui.screens.connect import STATUS_COL, ConnectScreen
from iyzee.tui.screens.console import ConsoleScreen, IyzeeConsole
from iyzee.tui.screens.sweep import SweepScreen
from iyzee.tui.screens.traces import TracesScreen


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
            ("traces", TracesScreen),
            ("console", ConsoleScreen),
        ]:
            assert isinstance(switcher.get_child_by_id(page_id), page_type)

        await pilot.press("s")
        assert switcher.current == "sweep"
        assert nav.query_one("#nav-sweep").has_class("-active")
        assert not nav.query_one("#nav-connect").has_class("-active")

        await pilot.press("t")
        assert switcher.current == "traces"
        await pilot.press("i")
        assert switcher.current == "console"
        assert pilot.app.screen.focused is not None
        assert pilot.app.screen.focused.id == "console-terminal"

        # From the console only the F-keys navigate (the terminal owns the letters).
        for key, page in [("f1", "connect"), ("f3", "traces"), ("f2", "sweep"), ("f4", "console")]:
            await pilot.press(key)
            assert switcher.current == page
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
async def test_nav_rail_keeps_the_dot_on_the_same_line_as_the_name() -> None:
    """Long labels wrapped and stranded the dot on its own line."""
    async with IyzeeApp().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        lines = plain(pilot.app.query_one("#nav-instruments", Static)).splitlines()
        entries = [line for line in lines if line.startswith(("○", "●"))]
        assert len(entries) == len(app_mod.INSTRUMENTS)
        assert all(len(line) > 2 for line in entries), entries  # a dot *and* a name
        assert any("Instruments" in line for line in lines), "the block has a heading"


@pytest.mark.parametrize("with_file", [False, True])
@async_test
async def test_console_history_file_defaults_to_memory_and_a_real_path_is_passed_through(
    with_file: bool, tmp_path: Path
) -> None:
    """`IyzeeApp()` with no arguments — every test in this codebase — must
    default to no persistent history file at all (`console_history_file=None`,
    which the session turns into `:memory:`). Getting this default wrong
    would silently reintroduce every InteractiveShell in the whole test
    suite writing to a real, shared, ever-growing SQLite file again — see
    `default_history_file`'s docstring in ipython.py for what that caused
    last time. Also checks the opt-in path actually reaches the console's
    shell: a real path passed through the app constructor must end up
    configured on the running IPythonSession's shell, not just stored.
    """
    history_file = tmp_path / "console_history.sqlite" if with_file else None
    app = IyzeeApp(console_history_file=history_file)
    assert app.console_history_file == history_file
    async with app.run_test() as pilot:
        await pilot.press("i")
        console = app.screen.query_one(IyzeeConsole)
        expected = str(history_file) if history_file else ":memory:"
        assert history_manager(console.session.shell).hist_file == expected


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
    lock = app.instrument_locks["busy"]
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
