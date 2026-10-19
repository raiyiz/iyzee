"""Code-first companion to the interactive iyzee workbench.

Open notebooks/workbench.py for file marking and plot controls. This notebook demonstrates
the same reusable tables and plotting functions directly, so analysis can stay ordinary Python.
It is read-only and does not connect to instruments.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import sys
    from pathlib import Path

    import marimo as mo
    import polars as pl

    from iyzee.config import data_root

    notebook_dir = str(Path(__file__).resolve().parent)
    if notebook_dir not in sys.path:
        sys.path.insert(0, notebook_dir)

    from toolbox import (
        best_points,
        channels_in,
        csv_bytes,
        field_chart,
        scan_catalog,
        scope_channels,
        scope_traces,
        sweep_points,
        verify,
    )

    return (
        best_points,
        channels_in,
        csv_bytes,
        data_root,
        field_chart,
        mo,
        pl,
        scan_catalog,
        scope_channels,
        scope_traces,
        sweep_points,
        verify,
    )


@app.cell
def _(mo):
    mo.md("""
    # iyzee · Analysis recipes

    A compact, code-first tour of the analysis layer behind the workbench:

    1. Index saved NPZ/JSON pairs without loading waveform arrays.
    2. Mark the runs you care about.
    3. Work with tidy Polars tables for comparisons, grouping, and joins.
    4. Reuse a small plot function, or replace it with your own Matplotlib code.

    Run the demo generator first if you do not have saved recordings. Synthetic runs are
    intentionally labelled and must not be mistaken for measurements.
    """)
    return


@app.cell
def _(data_root, mo, scan_catalog):
    root = data_root()
    catalog = scan_catalog(root)
    if catalog.is_empty():
        catalog_view = mo.md(
            f"No recordings found in {root}. Try scripts/demo_data.py demo-data and point "
            "IYZEE_DATA_DIR at that directory before opening marimo."
        )
    else:
        catalog_view = mo.vstack(
            [
                mo.md(
                    f"**{catalog.height} recordings** · "
                    f"{int(catalog['synthetic'].sum())} synthetic · read-only catalog"
                ),
                mo.ui.table(
                    catalog.select(
                        "label",
                        "kind",
                        "started",
                        "status",
                        "items",
                        "synthetic",
                        "warnings",
                        "digest",
                    ),
                    selection=None,
                    page_size=12,
                ),
            ]
        )
    catalog_view
    return (catalog,)


@app.cell
def _(catalog, mo):
    labels = catalog["label"].to_list()
    marked = mo.ui.multiselect(
        options=labels,
        value=labels[: min(5, len(labels))],
        label="Recordings to include",
        full_width=True,
    )
    mo.vstack([mo.md("## Mark a small working set"), marked])
    return (marked,)


@app.cell
def _(
    best_points,
    catalog,
    channels_in,
    marked,
    mo,
    pl,
    scope_channels,
    scope_traces,
    sweep_points,
):
    chosen = catalog.filter(pl.col("label").is_in(marked.value))
    sweeps = chosen.filter(pl.col("kind").is_in(["frequency", "bandwidth"]))
    scopes = chosen.filter(pl.col("kind") == "scope")
    issues = []
    points = sweep_points(sweeps, issues=issues)
    channel_stats = scope_channels(scopes)
    channels = channels_in(scopes)
    traces = scope_traces(scopes, channels, budget=1400, issues=issues)
    errors = pl.DataFrame(
        issues,
        schema={"run": pl.String, "problem": pl.String},
    )

    mo.vstack(
        [
            mo.md(f"## Tidy data · {chosen.height} marked recordings"),
            mo.md("### Sweep points"),
            mo.ui.table(points, selection=None, page_size=12),
            mo.md("### Best mean relative-noise point per run"),
            mo.ui.table(
                best_points(points.filter(pl.col("kind") == "frequency")),
                selection=None,
                page_size=8,
            ),
            mo.ui.table(
                best_points(points.filter(pl.col("kind") == "bandwidth")),
                selection=None,
                page_size=8,
            ),
            mo.md("### Scope channel statistics"),
            mo.ui.table(channel_stats, selection=None, page_size=10),
            mo.md("### Read issues"),
            mo.ui.table(errors, selection=None, page_size=8)
            if not errors.is_empty()
            else mo.md("No selected recording failed to load."),
        ]
    )
    return points, traces


@app.cell
def _(field_chart, mo, pl, points, traces):
    plots = []
    frequency = points.filter(pl.col("kind") == "frequency")
    bandwidth = points.filter(pl.col("kind") == "bandwidth")
    if not frequency.is_empty():
        plots.extend(
            [
                mo.md("### Frequency scans · shared offset reference"),
                field_chart(
                    frequency,
                    x="offset_mhz",
                    y="rel_noise_mean_db",
                    color="run",
                    title="Frequency sweep comparison",
                ),
            ]
        )
    if not bandwidth.is_empty():
        plots.extend(
            [
                mo.md("### Bandwidth scans"),
                field_chart(
                    bandwidth,
                    x="rbw_khz",
                    y="rel_noise_mean_db",
                    color="run",
                    title="Resolution-bandwidth comparison",
                ),
            ]
        )
    if not traces.is_empty():
        plots.extend(
            [
                mo.md("### Scope captures"),
                field_chart(
                    traces,
                    x="time",
                    y="value",
                    color="run",
                    detail="channel",
                    title="Marked scope waveforms",
                ),
            ]
        )
    if plots:
        plot_view = mo.vstack(plots)
    else:
        plot_view = mo.md("Mark compatible sweep or scope recordings above to make plots.")
    plot_view
    return


@app.cell
def _(mo):
    mo.md("""
    ## Small, reusable analysis patterns

    The toolbox returns ordinary Polars frames. Filter early, keep axis units explicit,
    and use simple loops or comprehensions when a table is not the right shape.
    """)
    mo.md("""
    ~~~python
    # Points below shot noise, strongest first.
    quiet = (
        points.filter(pl.col("rel_noise_mean_db") < 0)
        .sort("rel_noise_mean_db")
    )

    # Summarize repeat scans without losing the run label.
    by_run = (
        points.group_by(["kind", "run"])
        .agg(
            pl.col("rel_noise_mean_db").min().alias("best_db"),
            pl.col("rel_noise_mean_db").mean().alias("mean_db"),
        )
        .sort("best_db")
    )

    # Compare runs at common x values; no extrapolation outside overlap.
    frequency = points.filter(pl.col("kind") == "frequency")
    if not frequency.is_empty():
        differences = delta_to_reference(frequency, frequency["run_id"][0])

    # Inspect available raw data, then plot a selected 1-D / 2-D array.
    from toolbox import array_frame

    if not chosen.is_empty():
        row = chosen.row(0, named=True)
        recording = load_recording(Path(row["path"]))
        print({name: array.shape for name, array in recording.arrays.items()})
        preferred = next(
            (
                name
                for name in ("trace_squeezing", "trace_shot_noise")
                if name in recording.arrays
            ),
            None,
        )
        if preferred is not None:
            raw = array_frame(recording.arrays[preferred], index=0)
            fig = field_chart(raw, x="sample", y="value", color=None, title=preferred)

    # Use the same chart helper, or compose a publication figure yourself.
    fig = field_chart(
        frequency,
        x="offset_mhz",
        y="rel_noise_min_db",
        color="run",
        title="Lowest relative noise by laser detuning",
    )
    ~~~
    """)
    return


@app.cell
def _(catalog, csv_bytes, marked, mo, pl, verify):
    export_chosen = catalog.filter(pl.col("label").is_in(marked.value))
    mo.vstack(
        [
            mo.md("## Exports and integrity"),
            mo.download(
                data=csv_bytes(export_chosen),
                filename="iyzee-marked-catalog.csv",
                label="Download marked catalog",
            ),
            mo.ui.table(verify(export_chosen), selection=None, page_size=10),
        ]
    )
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
