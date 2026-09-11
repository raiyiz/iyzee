"""Textual application entry point for the iyzee lab-control TUI.

Run with ``uv run iyzee-tui`` (or ``python -m iyzee.tui.app``).

Three screens cover the common tasks end to end:

- **Connect** (``c``): open/close links to the MXA, shutter/PSU,
  wavemeter, and scope.
- **Sweep** (``s``): configure and run a bandwidth or frequency sweep
  against the connected MXA, with a live progress bar and trace plot.
- **Traces** (``t``): browse previously recorded ``.npz`` runs on disk.

Connection state lives on the ``App`` itself (``self.handles``), not on
any one screen, so switching screens never drops a live instrument link.
"""

from __future__ import annotations

from textual.app import App

from .instruments import InstrumentHandle
from .screens.connect import ConnectScreen
from .screens.sweep import SweepScreen
from .screens.traces import TracesScreen


class IyzeeApp(App):
    """Connect to lab instruments, run sweeps, and browse recorded traces."""

    TITLE = "iyzee"
    SUB_TITLE = "lab instrument control"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        ("c", "show_connect", "Connect"),
        ("s", "show_sweep", "Sweep"),
        ("t", "show_traces", "Traces"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Shared app-level state: connected instrument handles, keyed by
        # InstrumentSpec.key (see instruments.py). Screens read and write
        # this directly via `self.app.handles` instead of passing state
        # between screens.
        self.handles: dict[str, InstrumentHandle] = {}

        # Screens are created once and reused across switch_screen() calls
        # so e.g. the Sweep screen's config inputs survive a trip to the
        # Connect screen and back.
        self._connect_screen = ConnectScreen()
        self._sweep_screen = SweepScreen()
        self._traces_screen = TracesScreen()

    def on_mount(self) -> None:
        self.push_screen(self._connect_screen)

    def action_show_connect(self) -> None:
        self.switch_screen(self._connect_screen)

    def action_show_sweep(self) -> None:
        self.switch_screen(self._sweep_screen)

    def action_show_traces(self) -> None:
        self.switch_screen(self._traces_screen)


def run() -> None:
    """Console-script entry point (``iyzee-tui``)."""
    IyzeeApp().run()


if __name__ == "__main__":
    run()
