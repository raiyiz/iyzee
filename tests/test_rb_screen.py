"""The Rb page: the rubidium D1/D2 transitions as a plot pair plus a table."""

from __future__ import annotations

import re

from helpers import async_test
from textual.widgets import ContentSwitcher, DataTable
from textual_plotext import PlotextPlot

from iyzee.devices.wavemeter import Rb_transitions
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.rb import rb_lines

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _drawn(plot: PlotextPlot) -> str:
    return _ANSI.sub("", plot.plt.build())


def test_every_transition_is_parsed_with_its_own_isotope_centre() -> None:
    lines = rb_lines()
    expected = [label for label, _ in Rb_transitions if not label.endswith("_center")]
    assert [line.label for line in lines] == expected
    assert {(line.line, line.isotope) for line in lines} == {
        ("D1", 85),
        ("D1", 87),
        ("D2", 85),
        ("D2", 87),
    }


def test_labels_are_decoded_into_f_numbers_and_detuning() -> None:
    by_label = {line.label: line for line in rb_lines()}
    # Rb85 D2, F=3 -> F'=4: the cycling transition.
    cycling = by_label["D2 - Rb85_F34"]
    assert (cycling.f_ground, cycling.f_excited) == (3, 4)
    assert cycling.name == "D2 Rb85 F=3→F'=4"
    # Steck: ground-state offset -1.264888516 GHz, excited-state offset +0.100357 GHz.
    assert abs(cycling.detuning_ghz - (-1.264888516 + 0.100357)) < 1e-9


@async_test
async def test_rb_page_lists_every_transition_and_draws_both_lines() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        assert app.query_one(ContentSwitcher).current == "rb"
        assert app.query_one("#rb-table", DataTable).row_count == len(rb_lines())
        for line in ("d1", "d2"):
            text = _drawn(app.query_one(f"#rb-{line}", PlotextPlot))
            assert f"Rb {line.upper()}" in text
            assert "Rb85" in text and "Rb87" in text


@async_test
async def test_highlighting_a_row_marks_that_transition_in_its_plot() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        table = app.query_one("#rb-table", DataTable)
        # The table has focus when the page opens; move to the last D2 row.
        target = rb_lines()[-1]
        table.move_cursor(row=table.get_row_index(target.label))
        await pilot.pause()
        marker = f"F={target.f_ground}→F'={target.f_excited} Rb{target.isotope}"
        assert marker in _drawn(app.query_one("#rb-d2", PlotextPlot))
        assert marker not in _drawn(app.query_one("#rb-d1", PlotextPlot))
