"""The workbench analysis library: browse, compare and plot saved iyzee recordings.

This is the code behind ``notebooks/workbench.py``, kept as ordinary typed Python so it can be
used from scripts and IPython too. It needs polars (``uv sync --extra analysis``); the TUI never
imports it.
"""

from iyzee.workbench.catalog import (
    CATALOG_SCHEMA,
    KINDS,
    SCOPE_METRICS,
    SWEEP_KINDS,
    SWEEP_METRICS,
    default_selection,
    describe,
    pick,
    read_manifest,
    scan_catalog,
    verify,
)
from iyzee.workbench.charts import (
    LINESTYLES,
    PALETTE,
    csv_bytes,
    field_chart,
    figure_bytes,
)
from iyzee.workbench.groups import (
    BAND_CHOICES,
    group_curves,
    group_difference,
    group_summary,
)
from iyzee.workbench.tables import (
    array_frame,
    array_inventory,
    array_overlay,
    best_points,
    channels_in,
    decimate,
    delta_to_reference,
    pair_trace,
    preview_facts,
    scope_channels,
    scope_traces,
    scoreboard,
    sweep_points,
)
from iyzee.workbench.workset import (
    ISSUE_SCHEMA,
    POINT_TRACE_SCHEMA,
    SavedSet,
    Workset,
    WorksetStore,
    default_store_path,
)

__all__ = [
    "BAND_CHOICES",
    "CATALOG_SCHEMA",
    "ISSUE_SCHEMA",
    "KINDS",
    "LINESTYLES",
    "PALETTE",
    "POINT_TRACE_SCHEMA",
    "SCOPE_METRICS",
    "SWEEP_KINDS",
    "SWEEP_METRICS",
    "SavedSet",
    "Workset",
    "WorksetStore",
    "array_frame",
    "array_inventory",
    "array_overlay",
    "best_points",
    "channels_in",
    "csv_bytes",
    "decimate",
    "default_selection",
    "default_store_path",
    "delta_to_reference",
    "describe",
    "field_chart",
    "figure_bytes",
    "group_curves",
    "group_difference",
    "group_summary",
    "pair_trace",
    "pick",
    "preview_facts",
    "read_manifest",
    "scan_catalog",
    "scope_channels",
    "scope_traces",
    "scoreboard",
    "sweep_points",
    "verify",
]
