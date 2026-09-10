"""Terminal plotting widget for LeCroy waveforms."""

from __future__ import annotations

import numpy as np
from textual_plotext import PlotextPlot

from ..scope import Waveform


class ScopeTracePlot(PlotextPlot):
    """Render one physical-unit oscilloscope waveform."""

    def on_mount(self) -> None:
        self.plt.title("Scope waveform — no acquisition yet")
        self.plt.xlabel("Time")
        self.plt.ylabel("Amplitude")

    def update_waveform(self, waveform: Waveform) -> None:
        x = np.asarray(waveform.x, dtype=float)
        y = np.asarray(waveform.y, dtype=float)
        if x.size == 0 or y.size == 0:
            return
        self.plt.clear_data()
        self.plt.plot(x.tolist(), y.tolist())
        self.plt.title(f"{waveform.channel} waveform")
        self.plt.xlabel(waveform.x_unit)
        self.plt.ylabel(waveform.y_unit)
        self.refresh()
