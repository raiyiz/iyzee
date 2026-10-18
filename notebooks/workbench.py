"""Interactive, read-only workbench for iyzee measurement recordings.

Launch with:
    uv run --group notebooks marimo edit notebooks/workbench.py

To try it without instruments:
    uv run scripts/demo_data.py demo-data
    IYZEE_DATA_DIR=demo-data uv run --group notebooks marimo edit notebooks/workbench.py
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="full")


@app.cell
def _():
    import sys
    from pathlib import Path

    import marimo as mo
    import polars as pl

    from iyzee.config import data_root
    from iyzee.experiment import load_recording

    notebook_dir = str(Path(__file__).resolve().parent)
    if notebook_dir not in sys.path:
        sys.path.insert(0, notebook_dir)

    from toolbox import (
        KINDS,
        SWEEP_METRICS,
        best_points,
        channels_in,
        csv_bytes,
        delta_to_reference,
        describe,
        field_chart,
        scan_catalog,
        scope_channels,
        scope_traces,
        sweep_points,
        verify,
    )

    return (
        KINDS,
        Path,
        SWEEP_METRICS,
        best_points,
        channels_in,
        csv_bytes,
        data_root,
        delta_to_reference,
        describe,
        field_chart,
        load_recording,
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
    # iyzee · Data workbench

    Browse saved runs, **mark the recordings worth comparing**, and turn them into plots
    without writing a plotting function for every question. The catalog reads lightweight
    JSON sidecars first; waveform arrays are loaded only for marked recordings.

    **Read-only:** this notebook never connects to, configures, or acquires from an instrument.
    Synthetic examples are clearly labelled. Negative relative noise means the squeezing trace
    is below its shot-noise reference.
    """)
    return


@app.cell
def _(data_root, mo):
    data_directory = mo.ui.text(
        value=str(data_root()),
        label="Recording directory",
        full_width=True,
        debounce=True,
    )
    mo.vstack(
        [
            mo.md("## 1 · Find your recordings\nChange the directory here, or set IYZEE_DATA_DIR before launching marimo."),
            data_directory,
        ]
    )
    return (data_directory,)


@app.cell
def _(Path, data_directory, scan_catalog):
    catalog = scan_catalog(Path(data_directory.value).expanduser())
    return (catalog,)


@app.cell
def _(KINDS, mo):
    kind_filter = mo.ui.dropdown(
        options=["all", *KINDS],
        value="all",
        label="File type",
    )
    text_filter = mo.ui.text(
        value="",
        placeholder="e.g. aligned, aborted, scope",
        label="Find by name or status",
        debounce=True,
    )
    mo.vstack(
        [
            mo.md("### Browse"),
            mo.hstack([kind_filter, text_filter], align="end"),
        ]
    )
    return kind_filter, text_filter


@app.cell
def _(catalog, kind_filter, mo, pl, text_filter):
    visible = catalog
    if kind_filter.value != "all":
        visible = visible.filter(pl.col("kind") == kind_filter.value)
    query = text_filter.value.strip().lower()
    if query:
        visible = visible.filter(
            pl.any_horizontal(
                pl.col("label").str.to_lowercase().str.contains(query, literal=True),
                pl.col("status").str.to_lowercase().str.contains(query, literal=True),
                pl.col("kind").str.to_lowercase().str.contains(query, literal=True),
            )
        )
    if catalog.is_empty():
        browser_view = mo.vstack(
            [
                mo.md("**No recordings found in this directory.**"),
                mo.md(
                    "Create a self-contained demo catalog with "
                    "uv run scripts/demo_data.py demo-data, then set the directory above "
                    "to demo-data. These illustrative traces do not represent measurements."
                ),
            ]
        )
    else:
        browser_view = mo.vstack(
            [
                mo.md(
                    f"**{visible.height:,} of {catalog.height:,} recordings** · "
                    f"{int(catalog['synthetic'].sum())} synthetic"
                ),
                mo.ui.table(
                    visible.select(
                        "label", "kind", "started", "status", "items", "synthetic",
                        "warnings", "size_kib", "digest",
                    ),
                    selection=None,
                    page_size=12,
                ),
            ]
        )
    browser_view
    return (visible,)


@app.cell
def _(catalog, mo):
    labels = catalog["label"].to_list()
    marked_files = mo.ui.multiselect(
        options=labels,
        value=labels[: min(5, len(labels))],
        label="Mark recordings for analysis",
        full_width=True,
    )
    mo.vstack(
        [
            mo.md("## 2 · Mark and compare\nMark any mix of sweep and scope files. Selections are independent of the catalog filter."),
            marked_files,
        ]
    )
    return (marked_files,)


@app.cell
def _(
    best_points,
    catalog,
    describe,
    marked_files,
    mo,
    pl,
    scope_channels,
    sweep_points,
):
    marked = catalog.filter(pl.col("label").is_in(marked_files.value))
    summary = describe(marked)
    sweep_records = marked.filter(pl.col("kind").is_in(["frequency", "bandwidth"]))
    scope_records = marked.filter(pl.col("kind") == "scope")
    sweep_issues = []
    points = sweep_points(sweep_records, issues=sweep_issues)
    channel_stats = scope_channels(scope_records)

    kinds = ", ".join(f"{kind}: {count}" for kind, count in summary["by_kind"].items()) or "none"
    mo.vstack(
        [
            mo.md(
                f"### {summary['runs']} marked · {summary['items']} sweep points / scope channels · "
                f"{summary['synthetic']} synthetic\n**Kinds:** {kinds}"
            ),
            mo.ui.table(
                marked.select("label", "kind", "status", "started", "synthetic", "warnings", "path"),
                selection=None,
                page_size=10,
            ),
            mo.md("#### Best point per sweep"),
            mo.hstack(
                [
                    mo.vstack(
                        [
                            mo.md("**Frequency sweeps**"),
                            mo.ui.table(
                                best_points(points.filter(pl.col("kind") == "frequency")),
                                selection=None,
                                page_size=8,
                            ),
                        ]
                    ),
                    mo.vstack(
                        [
                            mo.md("**Bandwidth sweeps**"),
                            mo.ui.table(
                                best_points(points.filter(pl.col("kind") == "bandwidth")),
                                selection=None,
                                page_size=8,
                            ),
                        ]
                    ),
                ],
                align="start",
            ),
            mo.md("#### Scope channel statistics"),
            mo.ui.table(channel_stats, selection=None, page_size=10),
        ]
    )
    return channel_stats, marked, points, scope_records, sweep_issues, sweep_records


@app.cell
def _(channels_in, mo, scope_records):
    available_channels = channels_in(scope_records)
    chosen_channels = mo.ui.multiselect(
        options=available_channels,
        value=available_channels[: min(2, len(available_channels))],
        label="Scope channels to overlay",
    )
    trace_budget = mo.ui.slider(
        start=200,
        stop=8000,
        step=200,
        value=1600,
        label="Plot points per trace (preserves peaks)",
    )
    mo.vstack(
        [
            mo.md("### Scope waveform options"),
            mo.hstack([chosen_channels, trace_budget], align="start"),
        ]
    )
    return chosen_channels, trace_budget


@app.cell
def _(scope_records, scope_traces, chosen_channels, trace_budget):
    trace_issues = []
    traces = scope_traces(
        scope_records,
        chosen_channels.value,
        budget=int(trace_budget.value),
        issues=trace_issues,
    )
    return trace_issues, traces


@app.cell
def _(channel_stats, mo, pl, points, trace_issues, traces, sweep_issues):
    frame_map = {
        "Frequency sweep points": points.filter(pl.col("kind") == "frequency"),
        "Bandwidth sweep points": points.filter(pl.col("kind") == "bandwidth"),
        "Scope channel statistics": channel_stats,
        "Scope waveform samples": traces,
    }
    issues = sweep_issues + trace_issues
    issue_frame = pl.DataFrame(
        issues,
        schema={"run": pl.String, "problem": pl.String},
    )
    if issue_frame.is_empty():
        issue_view = mo.md("No marked recordings failed to load.")
    else:
        issue_view = mo.vstack(
            [
                mo.md(f"**{issue_frame.height} recording read issue(s)**"),
                mo.ui.table(issue_frame, selection=None, page_size=10),
            ]
        )
    issue_view
    return frame_map, issue_frame


@app.cell
def _(frame_map, mo):
    plot_sources = list(frame_map)
    populated = [name for name, frame in frame_map.items() if not frame.is_empty()]
    source = mo.ui.dropdown(
        options=plot_sources,
        value=populated[0] if populated else plot_sources[0],
        label="Data to plot",
    )
    mo.vstack(
        [
            mo.md("## 3 · Build a plot\nChoose the tidy table, then choose columns for the axes and grouping. Drag across the x-axis to brush a region of interest."),
            source,
        ]
    )
    return (source,)


@app.cell
def _(frame_map, mo, pl, source):
    plot_frame = frame_map[source.value]
    categorical = [
        column
        for column, dtype in plot_frame.schema.items()
        if dtype == pl.String and column not in {"run_id", "path"}
    ]
    numeric_types = {
        pl.Int8, pl.Int16, pl.Int32, pl.Int64,
        pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
        pl.Float32, pl.Float64,
    }
    numeric = [column for column, dtype in plot_frame.schema.items() if dtype in numeric_types]
    plot_columns = [column for column in plot_frame.columns if column not in {"run_id", "path", "kind"}]

    if plot_frame.is_empty() or not numeric or not plot_columns:
        x_field = y_field = color_field = detail_field = mark_type = None
        plot_controls = mo.md("Mark recordings of this type above to populate this plot.")
    else:
        x_default = next(
            (column for column in ("offset_mhz", "rbw_khz", "channel", "time", "point") if column in plot_columns),
            plot_columns[0],
        )
        y_default = next(
            (column for column in ("rel_noise_mean_db", "rms", "value", "peak_to_peak") if column in numeric),
            numeric[0],
        )
        color_options = ["(none)", *categorical]
        color_default = "run" if "run" in categorical else "channel" if "channel" in categorical else "(none)"
        detail_default = "channel" if source.value == "Scope waveform samples" and "channel" in categorical else "(none)"
        x_field = mo.ui.dropdown(options=plot_columns, value=x_default, label="X axis")
        y_field = mo.ui.dropdown(options=numeric, value=y_default, label="Y axis")
        color_field = mo.ui.dropdown(options=color_options, value=color_default, label="Color by")
        detail_field = mo.ui.dropdown(
            options=color_options,
            value=detail_default,
            label="Split lines by",
        )
        mark_type = mo.ui.dropdown(
            options=["line", "points", "bars"],
            value="line",
            label="Mark",
        )
        plot_controls = mo.vstack(
            [
                mo.hstack([x_field, y_field], align="start"),
                mo.hstack([color_field, detail_field, mark_type], align="start"),
            ]
        )
    plot_controls
    return color_field, detail_field, mark_type, plot_controls, plot_frame, x_field, y_field


@app.cell
def _(color_field, detail_field, field_chart, mo, mark_type, plot_frame, source, x_field, y_field):
    if x_field is None or y_field is None or plot_frame.is_empty():
        mo.md("No plottable rows yet. Mark some recordings and choose a populated data table.")
    else:
        color = None if color_field.value == "(none)" else color_field.value
        detail = None if detail_field.value == "(none)" else detail_field.value
        try:
            field_chart(
                plot_frame,
                x=x_field.value,
                y=y_field.value,
                color=color,
                detail=detail,
                mark=mark_type.value,
                title=f"{source.value} · {y_field.value} vs {x_field.value}",
            )
        except ValueError as exc:
            mo.md(f"**Cannot draw this combination:** {exc}")
    return


@app.cell
def _(marked, mo):
    verify_button = mo.ui.run_button(label="Verify SHA-256 checksums of marked files")
    mo.vstack(
        [
            mo.md("## 4 · Check and export\nChecksum verification reads each selected NPZ from disk; it does not alter recordings."),
            verify_button,
        ]
    )
    return (verify_button,)


@app.cell
def _(marked, mo, verify, verify_button):
    if verify_button.value:
        verification = verify(marked)
    else:
        verification = mo.md("Run checksum verification to check whether the saved arrays match their manifests.")
    verification
    return


@app.cell
def _(channel_stats, csv_bytes, marked, mo, points, traces):
    mo.vstack(
        [
            mo.md("### Download tables"),
            mo.hstack(
                [
                    mo.download(data=csv_bytes(marked), filename="iyzee-catalog.csv", label="Catalog CSV"),
                    mo.download(data=csv_bytes(points), filename="iyzee-sweep-points.csv", label="Sweep points CSV"),
                    mo.download(data=csv_bytes(channel_stats), filename="iyzee-scope-channels.csv", label="Scope statistics CSV"),
                    mo.download(data=csv_bytes(traces), filename="iyzee-scope-traces.csv", label="Scope waveforms CSV"),
                ],
                align="start",
            ),
            mo.md("""
            ### Reusable in code

            The tables are ordinary Polars dataframes. Keep a small selection in the notebook,
            then use normal expressions or list comprehensions for a focused analysis.

            ~~~python
            # Frequency points below shot noise, strongest first.
            quiet = (
                points.filter(
                    (pl.col("kind") == "frequency")
                    & (pl.col("rel_noise_mean_db") < 0)
                )
                .sort("rel_noise_mean_db")
            )

            # One best point per frequency run.
            best = {
                row["run"]: row
                for row in best_points(points.filter(pl.col("kind") == "frequency")).to_dicts()
            }

            # Inspect arrays of one marked file.
            recording = load_recording(Path(marked["path"][0]))
            for name, array in recording.arrays.items():
                print(name, array.shape)
            ~~~
            """),
        ]
    )
    return


@app.cell
def _(KINDS, SWEEP_METRICS, delta_to_reference, mo, points, pl):
    comparison_kind = mo.ui.dropdown(
        options=["frequency", "bandwidth"],
        value="frequency",
        label="Sweep kind",
    )
    comparison_points = points.filter(pl.col("kind") == comparison_kind.value)
    run_labels = comparison_points.select("run").unique(maintain_order=True)["run"].to_list()
    if run_labels:
        reference_run = mo.ui.dropdown(
            options=run_labels,
            value=run_labels[0],
            label="Reference run",
        )
        comparison_controls = mo.hstack([comparison_kind, reference_run], align="start")
    else:
        reference_run = None
        comparison_controls = mo.vstack(
            [comparison_kind, mo.md("Mark at least two runs of the same sweep type for a point-by-point comparison.")]
        )
    comparison_controls
    return comparison_kind, comparison_points, reference_run


@app.cell
def _(comparison_points, delta_to_reference, mo, pl, reference_run):
    if reference_run is None or comparison_points["run_id"].n_unique() < 2:
        delta_table = mo.md("Select multiple frequency or multiple bandwidth sweeps to compare against a reference.")
    else:
        reference_id = comparison_points.filter(pl.col("run") == reference_run.value)["run_id"][0]
        differences = delta_to_reference(comparison_points, reference_id)
        delta_table = mo.vstack(
            [
                mo.md("### Difference from reference\nPositive means more relative noise than the reference. Other runs are interpolated only over the shared x-range."),
                mo.ui.table(differences, selection=None, page_size=12),
            ]
        )
    delta_table
    return


if __name__ == "__main__":
    app.run()
