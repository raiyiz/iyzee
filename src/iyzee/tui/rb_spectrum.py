"""Reference rubidium spectrum for the Connect page.

The line positions come directly from :data:`iyzee.devices.wavemeter.Rb_transitions`.
The displayed line shape is deliberately synthetic: it is a visual guide to the
D1/D2 and hyperfine structure, not a calibrated absorption model.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual_plotext import PlotextPlot

from ...devices.wavemeter import Rb_transitions


class RbSpectrum(Vertical):
    """Compact D1/D2 reference spectrum with the wavemeter transitions."""

    def compose(self) -> ComposeResult:
        yield Static("Rb reference spectrum", classes="spectrum-title")
        yield PlotextPlot(id="rb-d1")
        yield PlotextPlot(id="rb-d2")
        yield DataTable(id="rb-transitions", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#rb-transitions", DataTable)
        table.add_column("Transition", key="transition")
        table.add_column("THz", key="frequency")
        table.add_column("Detuning", key="detuning")
        table.add_rows(
            [
                (label, f"{frequency:.9f}", f"{_detuning(label, frequency):+.3f} GHz")
                for label, frequency in Rb_transitions
                if "_center" not in label
            ]
        )
        self._draw("D1", "rb-d1")
        self._draw("D2", "rb-d2")

    def _draw(self, line: str, widget_id: str) -> None:
        transitions = [
            (label, frequency)
            for label, frequency in Rb_transitions
            if label.startswith(line) and "_center" not in label
        ]
        if not transitions:
            return

        center = np.mean([frequency for _label, frequency in transitions])
        x = np.linspace(
            min(frequency for _label, frequency in transitions) - 0.0015,
            max(frequency for _label, frequency in transitions) + 0.0015,
            700,
        )
        # A deliberately simple, normalized absorption profile: each listed
        # transition contributes the same Gaussian line. The point is to make
        # the structure and relative spacing legible, not to imply measured
        # line strengths or pressure broadening.
        sigma = 0.00012  # THz = 120 MHz
        absorption = sum(
            np.exp(-0.5 * ((x - frequency) / sigma) ** 2)
            for _label, frequency in transitions
        )
        absorption /= max(float(absorption.max()), 1.0)

        plot = self.query_one(f"#{widget_id}", PlotextPlot)
        plot.plt.clear_data()
        plot.plt.plot(((x - center) * 1e3).tolist(), absorption.tolist())
        plot.plt.title(f"Rb {line}")
        plot.plt.xlabel("Detuning (GHz)")
        plot.plt.ylabel("abs.")
        plot.refresh()


def _detuning(label: str, frequency: float) -> float:
    """Detuning from the corresponding isotope's D-line centre, in GHz."""
    isotope = "85" if "Rb85" in label else "87"
    line = label.split("-", 1)[0]
    center_label = f"Rb{isotope}_{line}_center"
    center = next(f for name, f in Rb_transitions if name == center_label)
    return (frequency - center) * 1e3
