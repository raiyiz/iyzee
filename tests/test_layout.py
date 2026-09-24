"""Tests for the responsive layout: on any terminal size every control is
reachable (on screen, or brought on screen by scrolling), nothing is clipped
without a scrollbar, and the layout reflows with the width.

Reachability is checked the way a user gets there: ask Textual to scroll the
widget into view (which is also what focusing it does), then assert its region
really lies inside the page's visible area. "The page has a scrollbar" alone
would pass even if a widget were unreachable.
"""

from __future__ import annotations

from typing import Any

import pytest
from helpers import async_test
from textual.geometry import Region
from textual.widgets import ContentSwitcher, Select

from iyzee.tui.app import IyzeeApp

# Representative wide and narrow terminals: breakpoint-specific reflow is covered
# separately below, so reachability only needs the two layout extremes.
SIZES = [(140, 50), (60, 14)]

CONTROLS = {
    "bandwidth": ["#sweep-type", "#rbw-start", "#rbw-stop", "#rbw-steps"],
    "frequency": [
        "#sweep-type",
        "#freq-center",
        "#freq-channel",
        "#freq-points",
        "#freq-offset-khz",
    ],
}
ALWAYS = ["#run-sweep", "#abort-sweep", "#capture-trace", "#sweep-progress"]
DISPLAYS = ["#sweep-plot", "#sweep-log"]  # big areas: only their start must be reachable


def _visible_area(app: IyzeeApp) -> Region:
    switcher = app.query_one(ContentSwitcher)
    return app.query_one(f"#{switcher.current}").scrollable_content_region


async def _scroll_to(pilot: Any, widget: Any) -> Region:
    widget.scroll_visible(animate=False)
    await pilot.pause()
    await pilot.pause()  # the scroll applies after a refresh; the region updates after the next
    return _visible_area(pilot.app)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("kind", ["bandwidth", "frequency"])
@async_test
async def test_every_sweep_control_can_be_scrolled_into_view(
    size: tuple[int, int], kind: str
) -> None:
    app = IyzeeApp()
    async with app.run_test(size=size) as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.query_one("#sweep")
        screen.query_one("#sweep-type", Select).value = kind
        await pilot.pause()

        for selector in [*CONTROLS[kind], *ALWAYS]:
            widget = screen.query_one(selector)
            area = await _scroll_to(pilot, widget)
            assert area.contains_region(widget.region), f"{selector} not fully visible at {size}"
        for selector in DISPLAYS:
            widget = screen.query_one(selector)
            assert (await _scroll_to(pilot, widget)).overlaps(widget.region), (
                f"{selector} at {size}"
            )


@pytest.mark.parametrize("size", [(80, 16)])
@async_test
async def test_the_console_terminal_stays_reachable_on_short_terminals(
    size: tuple[int, int],
) -> None:
    app = IyzeeApp()
    async with app.run_test(size=size) as pilot:
        await pilot.press("i")
        await pilot.pause(0.3)
        terminal = app.query_one("#console-terminal")
        assert (await _scroll_to(pilot, terminal)).overlaps(terminal.region)


@pytest.mark.parametrize("width", [60, 140])
@async_test
async def test_pages_scroll_instead_of_clipping_and_fit_horizontally(width: int) -> None:
    app = IyzeeApp()
    async with app.run_test(size=(width, 30)) as pilot:
        for key, page_id in [("c", "connect"), ("s", "sweep"), ("t", "traces"), ("i", "console")]:
            await pilot.press(key)
            await pilot.pause()
            page = app.query_one(f"#{page_id}")
            assert page.styles.overflow_x != "hidden" and page.styles.overflow_y != "hidden"
            assert page.max_scroll_x == 0, f"{page_id} needs sideways scrolling at width {width}"


@pytest.mark.parametrize(
    "size,fields_per_row,buttons_share_a_row",
    [((140, 50), 3, True), ((100, 30), 2, True), ((60, 24), 1, False)],
)
@async_test
async def test_the_sweep_form_reflows_with_the_width(
    size: tuple[int, int], fields_per_row: int, buttons_share_a_row: bool
) -> None:
    app = IyzeeApp()
    async with app.run_test(size=size) as pilot:
        await pilot.press("s")
        await pilot.pause()
        fields = [app.query_one(f"#{i}").region for i in ("rbw-start", "rbw-stop", "rbw-steps")]
        assert sum(1 for f in fields if f.y == fields[0].y) == fields_per_row
        buttons = [
            app.query_one(f"#{i}").region for i in ("run-sweep", "abort-sweep", "capture-trace")
        ]
        assert (len({b.y for b in buttons}) == 1) is buttons_share_a_row
        assert app.query_one("#sweep-plot").region.height >= 14, (
            "the plot must not be squeezed away"
        )


@pytest.mark.parametrize("size,side_by_side", [((120, 30), True), ((60, 24), False)])
@async_test
async def test_the_traces_list_and_preview_stack_when_narrow(
    size: tuple[int, int], side_by_side: bool
) -> None:
    app = IyzeeApp()
    async with app.run_test(size=size) as pilot:
        await pilot.press("t")
        await pilot.pause()
        listing, detail = (
            app.query_one("#traces-list").region,
            app.query_one("#traces-detail").region,
        )
        if side_by_side:
            assert detail.x >= listing.x + listing.width
        else:
            assert detail.y >= listing.y + listing.height
