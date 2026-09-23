"""Where experiment results go: measurement persistence (run directories,
compressed archives) and plotting helpers.

A run is saved as a matched pair of files sharing one file stem: a
``.npz`` holding purely numeric arrays (x-values, and one 2-D array per
named trace), and a ``.json`` sidecar holding per-point and run-level
metadata as plain JSON. Splitting them this way means the ``.npz`` never
needs ``allow_pickle=True`` to load — every array in it is a plain numeric
dtype — so reading back a saved run, including a run someone else wrote,
never risks NumPy's pickle-based object-array deserialization executing
code embedded in the file. (An earlier version of this module packed
everything, including per-point dicts, into a single ``dtype=object``
``.npz``; that required ``allow_pickle=True`` to read anything back at
all. Files written that way are not supported by this version.)
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from .core import StepResult

# Where all measurement runs are written and (in the TUI) read back from —
# <project root>/data, i.e. a sibling of src/, not inside the installed
# package. That makes it a fixed, predictable place regardless of the
# current working directory `iyzee`/`iyzee-tui` is launched from, and easy
# to find, back up or point other tools at, without digging into src/iyzee/.
#
# Computed once, from this file's own location (`src/iyzee/experiment/io.py`
# -> parents[3] is the checkout root), so it agrees with itself everywhere
# it's used and callers never repeat the path arithmetic (see
# `tui/screens/traces.py`, which reads from exactly this constant). This
# assumes a development checkout / editable install (this project's only
# supported way to run it — see the README); a real wheel installed
# elsewhere would resolve `parents[3]` to somewhere under site-packages, not
# a sensible data location.
DATA_ROOT = Path(__file__).resolve().parents[3] / "data"


def create_dirs() -> Path:
    """Create and return this month's measurement-data directory, under DATA_ROOT.

    One level of subdirectory (``data/2026-09/``), not one folder per run:
    a run's own file name already carries its date, time, and a short
    random suffix (see ``_new_stem``), so a per-run folder on top of that
    would mostly just be a lot of near-empty folders to click through.
    Grouping by month instead keeps the directory listing itself a
    reasonable size to browse, months or years into using this, without
    losing any ability to find a particular run (Traces, and this
    module's own readers, glob recursively).
    """
    month = datetime.now().astimezone().strftime("%Y-%m")
    data_dir = DATA_ROOT / month
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# Only letters, digits, and "-" survive into a file stem — notably not "_",
# which is reserved as the separator between the stem's own fields (see
# _new_stem/STEM_PATTERN), so a name can never be mistaken for part of the
# timestamp or the random suffix on either write or read.
_UNSAFE_STEM_CHARS = re.compile(r"[^A-Za-z0-9-]+")

# The inverse of _new_stem, used by the Traces page to recover a run's name
# (if it has one) from its filename for display. Matches only stems this
# module actually produces; anything else (a hand-placed or older-format
# file) simply doesn't match, which callers treat as "no name available"
# rather than an error.
STEM_PATTERN = re.compile(r"^\d{2}T\d{6}(?:_(?P<name>[A-Za-z0-9-]+))?_[0-9a-f]{6}$")


def _new_stem(name: str = "") -> str:
    """A new file stem for one run: the time (so a month's directory still
    lists chronologically), ``name`` if given, and a short random suffix.

    ``name`` is sanitized for the filesystem: any character that isn't a
    letter, digit, or "-" becomes "-" rather than being silently dropped,
    so two different unsafe names can't collide into the same stem. The
    suffix is six random hex digits (``secrets``, not the slower/weaker
    ``random``, though collision-resistance rather than unpredictability is
    the actual goal here) — enough that two runs started in the same
    second, or with the same name, still get different files.
    """
    timestamp = datetime.now().astimezone().strftime("%dT%H%M%S")
    token = secrets.token_hex(3)
    slug = _UNSAFE_STEM_CHARS.sub("-", name).strip("-")
    return "_".join(part for part in (timestamp, slug, token) if part)


def save_step_results(
    results: list[StepResult],
    savedir: Path,
    run_metadata: dict[str, Any] | None = None,
    *,
    name: str = "",
    path: Path | None = None,
) -> Path:
    """Save a run as a numeric ``.npz`` plus a plain-JSON ``.json`` sidecar
    sharing the same file stem.

    Every point contributes one value to ``"x_values"`` and one row to a
    ``"trace_<name>"`` array for each name in its ``result.traces`` (across
    all the ``StepResult``s, not just the first — a step that skips a
    trace some points have is not an error, see below). All of this
    application's own steps keep the same trace names and lengths for
    every point in one run (nothing here ever changes the analyzer's sweep
    point count mid-run — see ``AnalyzerConfig``/``prepare_analyzer``), so
    a real length mismatch almost certainly means something is wrong
    rather than an intentionally ragged run: it raises ``ValueError``
    rather than silently writing truncated or misaligned data. A point
    that's missing a trace *entirely* (``result.traces.get(name)`` is
    ``None``) is different from a length mismatch — that's a real,
    expected case (see ``FrequencyStep``/``BandwidthStep``'s docstrings
    for why a point's traces can be sparse), and gets a row of NaN rather
    than an error.

    Per-point metadata (``label``, ``x_unit``, and each point's own
    ``meta``) plus ``run_metadata`` go in the ``.json`` sidecar as plain,
    human-readable JSON — inspectable with ``cat``, not just this module.
    A ``meta`` value that isn't JSON-serializable is coerced with
    ``str()`` rather than failing the save outright: ``StepResult.meta``
    is typed ``dict[str, Any]``, and losing an entire run's data because
    one custom ``Step`` put something unexpected in there would be a far
    worse outcome than that one field round-tripping as text.

    By default a new file pair is created in ``savedir``, named from the
    current time, ``name`` if given, and a short random suffix (see
    ``_new_stem``). Pass ``path`` (the ``.npz`` path returned by an
    earlier call) to overwrite that pair instead — how a long run
    checkpoints itself after every point without littering the directory
    with one file pair per point; ``name`` is only consulted when a new
    stem is actually being chosen, so it's fine to keep passing it on
    every checkpoint call.

    Each file is written atomically (via a temporary name, then
    ``Path.replace``), so a crash or power cut mid-write leaves the
    previous checkpoint intact rather than a truncated file. The pair is
    not a single atomic unit, though — a reader could in principle see a
    freshly-written ``.npz`` next to the *previous* checkpoint's
    ``.json``, in the narrow window between the two replaces. Readers
    (Traces) tolerate a missing/unreadable sidecar already, and this
    module is the only writer, so that stale-for-an-instant window isn't
    worth a heavier two-phase commit here.
    """
    if path is None:
        path = savedir / f"{_new_stem(name)}.npz"
    json_path = path.with_suffix(".json")

    trace_names = list(dict.fromkeys(name for result in results for name in result.traces))
    arrays: dict[str, np.ndarray] = {
        "x_values": np.asarray([result.x_value for result in results], dtype=np.float64)
    }
    for trace_name in trace_names:
        arrays[f"trace_{trace_name}"] = _stack_trace(results, trace_name)

    points = [{"label": result.label, "x_unit": result.x_unit, **result.meta} for result in results]
    sidecar = {"run_metadata": run_metadata, "points": points}

    # ".part", not ".npz.tmp"/".json.tmp": np.savez appends ".npz" to names
    # that lack it (writing through a file object avoids that), and Traces
    # globs *.npz, so an in-flight file must not match either way.
    partial_npz = path.with_name(path.name + ".part")
    with partial_npz.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    partial_npz.replace(path)

    partial_json = json_path.with_name(json_path.name + ".part")
    partial_json.write_text(json.dumps(sidecar, indent=2, default=str))
    partial_json.replace(json_path)

    return path


def _stack_trace(results: list[StepResult], trace_name: str) -> np.ndarray:
    """One ``(len(results), width)`` float64 array for ``trace_name``: row
    ``i`` is ``results[i].traces[trace_name]``, or NaN if that point didn't
    have this trace at all. ``width`` is every present row's shared
    length; a length that disagrees raises (see ``save_step_results``).
    """
    rows: list[np.ndarray | None] = []
    width: int | None = None
    for result in results:
        trace = result.traces.get(trace_name)
        if trace is None:
            rows.append(None)
            continue
        row = np.asarray(trace, dtype=np.float64)
        if row.ndim != 1:
            raise ValueError(f"trace {trace_name!r} must be one-dimensional, got shape {row.shape}")
        if width is None:
            width = row.shape[0]
        elif row.shape[0] != width:
            raise ValueError(
                f"trace {trace_name!r} has inconsistent lengths across points "
                f"({width} vs {row.shape[0]}) - a run's points must all agree on "
                "the number of samples per trace"
            )
        rows.append(row)
    stacked = np.full((len(results), width or 0), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        if row is not None:
            stacked[i] = row
    return stacked


def difference_series(
    squeezing: object, shot_noise: object, label: str | None
) -> tuple[list[float], list[float], str | None] | None:
    """Compute one squeezing-minus-shot-noise line, ready to plot.

    The single source of truth for this computation — before this, the
    same three lines (subtract, build an x-index, attach a label) were
    written out independently in four places: this module's own
    ``build_figure`` (matplotlib), ``SweepScreen._plot_result`` (live,
    per point, plotext), ``TracesScreen._show`` (re-derived from a saved
    ``.npz``, plotext), and indirectly duplicated again in spirit by
    ``IyzeeConsole``'s figure rendering. Callers only differ in *where*
    the squeezing/shot_noise arrays came from (a live ``StepResult`` vs.
    an archived point) and *how* they draw the result (matplotlib vs.
    plotext) — this covers the part in between.

    Returns ``None`` if either trace is missing, matching the skip
    behavior ``SweepScreen``/``TracesScreen`` already had (``build_figure``
    previously didn't guard against this and would have raised on missing
    traces — this closes that gap as a side effect of consolidating).
    A trace that's present but entirely NaN (how ``save_step_results``
    marks a point that had no data for that trace at all — see
    ``_stack_trace``) is treated the same as missing, for the same reason.
    """
    if squeezing is None or shot_noise is None:
        return None
    squeezing = np.asarray(squeezing)
    shot_noise = np.asarray(shot_noise)
    if np.all(np.isnan(squeezing)) or np.all(np.isnan(shot_noise)):
        return None
    difference = squeezing - shot_noise
    return list(range(len(difference))), list(difference), label


def build_figure(results: list[StepResult]):
    """Build (but do not display) the squeezing-minus-shot-noise figure.

    Split out of :func:`multiplot` so non-interactive callers — saving to
    disk, or a TUI that renders traces itself with something like
    ``textual-plotext`` — can get the figure without ``matplotlib`` trying
    to pop up a blocking GUI window.
    """
    fig, ax = plt.subplots()
    labels: list[str] = []

    for result in results:
        series = difference_series(
            result.traces.get("squeezing"), result.traces.get("shot_noise"), result.label
        )
        if series is None:
            continue
        _x, difference, label = series
        ax.plot(difference)
        # difference_series() types its returned label as `str | None` because
        # it also accepts `None` in (for a caller with no label at all); here
        # `result.label` is a plain `str` (StepResult.label is not Optional),
        # so it comes back unchanged — this is just narrowing that for `legend()`.
        if label is not None:
            labels.append(label)

    if labels:
        ax.legend(labels, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.1))
    ax.set_xlabel("Trace point")
    ax.set_ylabel("Squeezing - shot noise")
    fig.tight_layout()
    return fig


def multiplot(results: list[StepResult]) -> None:
    """Build and display the squeezing-minus-shot-noise figure.

    Kept for the script/CLI entry point (``main.py``) and existing callers.
    Non-interactive callers should use :func:`build_figure` instead.
    """
    build_figure(results)
    plt.show()
