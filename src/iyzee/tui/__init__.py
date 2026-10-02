"""Textual TUI for iyzee: connect to instruments, run sweeps, browse results."""

# Force a non-interactive backend before anything below has a chance to
# import matplotlib.pyplot (experiment.io, waveform_math, and the Console
# screen all do). The TUI never shows a live matplotlib window — Console
# strips any figure a script builds down to line data via figure_series()
# instead of calling show() on it, and Results only ever extract line
# data or call savefig(). Relying on matplotlib's automatic backend
# selection instead would be both unnecessary (nothing here uses it) and
# unsafe: an interactive backend (Tk/Qt/macOS, whichever happens to be
# installed and is auto-selected because a display is present) generally
# requires being driven from the main thread, which would rule out ever
# building a figure in a worker thread — exactly what the potentially slow,
# large-waveform figure-building in Results needs to do.
import matplotlib

matplotlib.use("Agg")

from .app import IyzeeApp, run  # noqa: E402 - see matplotlib.use() above

__all__ = ["IyzeeApp", "run"]
