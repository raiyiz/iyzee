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

from helpers import async_test, wait_until
from textual.geometry import Region
from textual.widgets import ContentSwitcher, Select

from iyzee.tui.app import IyzeeApp

# Representative wide and narrow terminals: breakpoint-specific reflow is covered
# separately below, so reachability only needs the two layout extremes.
SIZES = [(140, 50), (60, 14)]

CONTROLS = {
    "bandwidth": ["#sweep-type", "#sweep-duration", "#rbw-start", "#rbw-stop", "#rbw-steps"],
    "frequency": [
        "#sweep-type",
        "#sweep-duration",
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
    # Preserve both UI turns (scroll, then updated region) without waiting for idle timers.
    await pilot.pause(0)
    await pilot.pause(0)
    return _visible_area(pilot.app)


async def _show(pilot: Any, page_id: str) -> None:
    """Switch page without keystrokes: the console page keeps the keyboard for itself."""
    await pilot.app.run_action(f"show_page('{page_id}')")
    await pilot.pause(0)


async def _resize(pilot: Any, size: tuple[int, int]) -> None:
    # Pilot.resize_terminal already waits for Textual to process the Resize message.
    await pilot.resize_terminal(*size)


@async_test
async def test_every_control_stays_reachable_at_the_extreme_sizes() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.query_one("#sweep")
        for size in SIZES:
            await _resize(pilot, size)
            for kind in ("bandwidth", "frequency"):
                screen.query_one("#sweep-type", Select).value = kind
                await pilot.pause()
                for selector in [*CONTROLS[kind], *ALWAYS]:
                    widget = screen.query_one(selector)
                    area = await _scroll_to(pilot, widget)
                    assert area.contains_region(widget.region), f"{selector} not visible at {size}"
                for selector in DISPLAYS:
                    widget = screen.query_one(selector)
                    assert (await _scroll_to(pilot, widget)).overlaps(widget.region), (
                        f"{selector} at {size}"
                    )

        await _resize(pilot, (80, 16))  # the cell editor must remain reachable
        await _show(pilot, "console")
        terminal = app.query_one("#console-editor")
        await wait_until(pilot, lambda: app.focused is terminal)
        assert (await _scroll_to(pilot, terminal)).overlaps(terminal.region)


@async_test
async def test_pages_scroll_instead_of_clipping_and_the_layout_reflows_with_the_width() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        for width in (60, 140):
            await _resize(pilot, (width, 30))
            for page_id in ("connect", "sweep", "results", "console"):
                await _show(pilot, page_id)
                page = app.query_one(f"#{page_id}")
                assert page.styles.overflow_x != "hidden" and page.styles.overflow_y != "hidden"
                assert page.max_scroll_x == 0, f"{page_id} needs sideways scrolling at {width}"

        for size, fields_per_row, buttons_share_a_row in (
            ((140, 50), 3, True),
            ((100, 30), 2, True),
            ((60, 24), 1, False),
        ):
            await _resize(pilot, size)
            await _show(pilot, "sweep")
            await pilot.pause()
            fields = [app.query_one(f"#{i}").region for i in ("rbw-start", "rbw-stop", "rbw-steps")]
            assert sum(1 for f in fields if f.y == fields[0].y) == fields_per_row
            buttons = [
                app.query_one(f"#{i}").region for i in ("run-sweep", "abort-sweep", "capture-trace")
            ]
            assert (len({b.y for b in buttons}) == 1) is buttons_share_a_row
            assert app.query_one("#sweep-plot").region.height >= 14, "the plot must not be squeezed"

        for size, side_by_side in (((120, 30), True), ((60, 24), False)):
            await _resize(pilot, size)
            await _show(pilot, "results")
            await pilot.pause()
            listing = app.query_one("#results-list").region
            detail = app.query_one("#results-detail").region
            if side_by_side:
                assert detail.x >= listing.x + listing.width
            else:
                assert detail.y >= listing.y + listing.height
