"""Live trace rendering for the Textual application."""

from __future__ import annotations

import numpy as np
from textual_plotext import PlotextPlot

from ..experiment.step import StepResult


class TracePlot(PlotextPlot):
    """Render the most recently acquired squeezing and shot-noise traces."""

    def on_mount(self) -> None:
        self.plt.title("Live trace")
        self.plt.xlabel("Trace point")
        self.plt.ylabel("Amplitude")

    def update_result(self, result: StepResult) -> None:
        squeezing = np.asarray(result.traces.get("squeezing", []), dtype=float)
        shot_noise = np.asarray(result.traces.get("shot_noise", []), dtype=float)
        if squeezing.size == 0 and shot_noise.size == 0:
            return

        self.plt.clear_data()
        if squeezing.size:
            self.plt.plot(list(range(len(squeezing))), squeezing.tolist(), label="squeezing")
        if shot_noise.size:
            self.plt.plot(list(range(len(shot_noise))), shot_noise.tolist(), label="shot noise")
        self.plt.title(f"Live trace — {result.label}")
        self.plt.xlabel("Trace point")
        self.plt.ylabel("Amplitude")
        self.plt.legend()
        self.refresh()
