"""Rb page: the rubidium D1/D2 transitions the wavemeter is referenced to.

A static reference, not an instrument view: line positions come straight from
:data:`iyzee.devices.wavemeter.Rb_transitions` (D. Steck), so nothing here needs
a connection. Each D line gets a stick plot (one stick per hyperfine transition,
coloured by isotope) and the table below lists every transition's absolute
frequency. Highlighting a table row marks that transition in both plots.

The sticks show *where* the lines are, not how strong they are: relative
strengths, Doppler and pressure broadening are deliberately left out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Static
from textual_plotext import PlotextPlot

from ...devices.wavemeter import Rb_transitions
from .page import Page

_LABEL = re.compile(r"^(?P<line>D[12]) - Rb(?P<isotope>85|87)_F(?P<ground>\d)(?P<excited>\d)$")
_CENTER = re.compile(r"^Rb(?P<isotope>85|87)_(?P<line>D[12])_center$")

# Plot colours: one per isotope, and one for the highlighted transition.
_ISOTOPE_COLOR = {85: "cyan", 87: "orange"}
_HIGHLIGHT_COLOR = "red"


@dataclass(frozen=True)
class RbLine:
    """One hyperfine transition: ground F -> excited F' of an isotope's D line."""

    label: str  # the key used in ``Rb_transitions``
    line: str  # "D1" or "D2"
    isotope: int  # 85 or 87
    f_ground: int
    f_excited: int
    thz: float  # absolute frequency in vacuum
    center_thz: float  # that isotope's fine-structure centre for this D line

    @property
    def name(self) -> str:
        return f"{self.line} Rb{self.isotope} F={self.f_ground}→F'={self.f_excited}"

    @property
    def detuning_ghz(self) -> float:
        """From the isotope's own D-line centre (the convention of Steck's tables)."""
        return (self.thz - self.center_thz) * 1e3


def rb_lines() -> list[RbLine]:
    """Every transition in ``Rb_transitions``, parsed; the ``*_center`` entries are the references.

    Raises ``ValueError`` for an entry it can't make sense of, rather than
    silently dropping it from the display.
    """
    centers: dict[tuple[int, str], float] = {}
    for label, thz in Rb_transitions:
        if match := _CENTER.match(label):
            centers[int(match["isotope"]), match["line"]] = thz

    lines = []
    for label, thz in Rb_transitions:
        if _CENTER.match(label):
            continue
        match = _LABEL.match(label)
        if match is None:
            raise ValueError(f"unrecognised Rb transition label {label!r}")
        isotope, line = int(match["isotope"]), match["line"]
        lines.append(
            RbLine(
                label=label,
                line=line,
                isotope=isotope,
                f_ground=int(match["ground"]),
                f_excited=int(match["excited"]),
                thz=thz,
                center_thz=centers[isotope, line],
            )
        )
    return lines


def _sticks(positions: list[float]) -> tuple[list[float], list[float]]:
    """A series that plots as one unit-height stick per position, joined along the baseline."""
    xs: list[float] = []
    ys: list[float] = []
    for x in sorted(positions):
        xs += [x, x, x]
        ys += [0.0, 1.0, 0.0]
    return xs, ys


class RbScreen(Page):
    """D1 and D2 stick plots over a table of all hyperfine transitions."""

    def compose(self) -> ComposeResult:
        yield Static("Rubidium D1 / D2 transitions", classes="panel-title")
        yield Static(
            "Line positions only (no strengths or broadening). "
            "Highlight a row to mark it in the plots.",
            classes="hint",
        )
        with Horizontal(id="rb-plots"):
            yield PlotextPlot(id="rb-d1", classes="rb-plot")
            yield PlotextPlot(id="rb-d2", classes="rb-plot")
        yield DataTable(id="rb-table", cursor_type="row")

    def on_mount(self) -> None:
        self._lines = rb_lines()
        table = self.query_one(DataTable)
        table.add_column("Transition", key="name")
        table.add_column("Frequency (THz)", key="thz")
        table.add_column("From centre (GHz)", key="detuning")
        for line in self._lines:
            table.add_row(line.name, f"{line.thz:.9f}", f"{line.detuning_ghz:+.4f}", key=line.label)
        self._draw(None)

    def on_show(self) -> None:
        self.query_one(DataTable).focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._draw(event.row_key.value)

    def _draw(self, highlighted: str | None) -> None:
        for line in ("D1", "D2"):
            self._draw_line(line, highlighted)

    def _draw_line(self, line: str, highlighted: str | None) -> None:
        lines = [candidate for candidate in self._lines if candidate.line == line]
        # One shared axis for both isotopes: GHz from the Rb85 centre of this D line.
        reference = next(candidate.center_thz for candidate in lines if candidate.isotope == 85)

        def offset(candidate: RbLine) -> float:
            return (candidate.thz - reference) * 1e3

        plot = self.query_one(f"#rb-{line.lower()}", PlotextPlot)
        plt = plot.plt
        plt.clear_data()
        for isotope, color in _ISOTOPE_COLOR.items():
            positions = [offset(c) for c in lines if c.isotope == isotope]
            plt.plot(*_sticks(positions), label=f"Rb{isotope}", color=color, marker="braille")
        marked = next((c for c in lines if c.label == highlighted), None)
        if marked is not None:
            plt.plot(
                *_sticks([offset(marked)]),
                label=f"F={marked.f_ground}→F'={marked.f_excited} Rb{marked.isotope}",
                color=_HIGHLIGHT_COLOR,
                marker="braille",
            )
        span = [offset(c) for c in lines]
        margin = 0.06 * (max(span) - min(span))
        plt.xlim(min(span) - margin, max(span) + margin)
        plt.ylim(0, 1.6)  # headroom: the legend sits above the sticks
        plt.yfrequency(0)  # the stick height means nothing; don't label it
        plt.title(f"Rb {line}")
        plt.xlabel(f"GHz from Rb85 {line} centre")
        plot.refresh()
