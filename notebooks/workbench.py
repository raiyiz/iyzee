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
    import numpy as np
    import polars as pl

    from iyzee.config import data_root
    from iyzee.experiment import load_recording

    notebook_dir = str(Path(__file__).resolve().parent)
    if notebook_dir not in sys.path:
        sys.path.insert(0, notebook_dir)

    from toolbox import (
        KINDS,
        array_frame,
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
        array_frame,
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
    refresh_index = mo.ui.run_button(label="Refresh index")
    mo.vstack(
        [
            mo.md(
                "## 1 · Find your recordings\nChange the directory here, or set IYZEE_DATA_DIR before launching marimo. "
                "Press **Refresh index** after files are added while the notebook is open."
            ),
            mo.hstack([data_directory, refresh_index], align="end"),
        ]
    )
    return data_directory, refresh_index


@app.cell
def _(Path, data_directory, mo, refresh_index, scan_catalog):
    root = Path(data_directory.value).expanduser()
    refresh_index.value  # Re-index the directory when the button is pressed.
    catalog = scan_catalog(root)
    if not root.is_dir():
        index_status = mo.md(f"**Directory not found:** {root}")
    elif catalog.is_empty():
        index_status = mo.md(f"No .npz recordings found in {root}.")
    else:
        index_status = mo.md(f"Indexed **{catalog.height:,} recordings** in {root}.")
    index_status
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
                pl.col("name").str.to_lowercase().str.contains(query, literal=True),
                pl.col("instrument").str.to_lowercase().str.contains(query, literal=True),
                pl.col("path").str.to_lowercase().str.contains(query, literal=True),
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
                        "label",
                        "kind",
                        "started",
                        "status",
                        "items",
                        "synthetic",
                        "warnings",
                        "size_kib",
                        "digest",
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
            mo.md(
                "## 2 · Mark and compare\nMark any mix of sweep and scope files. Selections are independent of the catalog filter."
            ),
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
                marked.select(
                    "label", "kind", "status", "started", "synthetic", "warnings", "path"
                ),
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
def _(marked, mo):
    if marked.is_empty():
        raw_run = None
        run_view = mo.md("Mark at least one recording above to inspect its raw arrays.")
    else:
        run_labels = marked["label"].to_list()
        raw_run = mo.ui.dropdown(
            options=run_labels,
            value=run_labels[0],
            label="Recording",
        )
        run_view = raw_run
    mo.vstack(
        [
            mo.md(
                "## 3 · Explore raw arrays\nChoose any marked recording and a saved array. "
                "One-dimensional arrays plot directly; for two-dimensional arrays, choose a "
                "first-axis row (often a sweep point). Higher dimensions are never flattened silently."
            ),
            run_view,
        ]
    )
    return (raw_run,)


@app.cell
def _(Path, load_recording, marked, mo, pl, raw_run):
    if raw_run is None:
        raw_recording = None
        raw_array_name = None
        array_view = mo.md("Select a recording above to list its saved arrays.")
    else:
        row = marked.filter(pl.col("label") == raw_run.value).row(0, named=True)
        try:
            raw_recording = load_recording(Path(row["path"]))
        except Exception as exc:  # noqa: BLE001 - show a damaged selected file without breaking the notebook
            raw_recording = None
            raw_array_name = None
            array_view = mo.md(f"**Could not load {raw_run.value}:** {exc}")
        else:
            array_names = sorted(raw_recording.arrays)
            if not array_names:
                raw_array_name = None
                array_view = mo.md("This recording has no stored arrays.")
            else:
                preferred = next(
                    (name for name in ("trace_squeezing", "trace_shot_noise") if name in array_names),
                    array_names[0],
                )
                raw_array_name = mo.ui.dropdown(
                    options=array_names,
                    value=preferred,
                    label="Saved array",
                )
                array_view = raw_array_name
    array_view
    return raw_array_name, raw_recording


@app.cell
def _(mo, np, raw_array_name, raw_recording):
    if raw_recording is None or raw_array_name is None:
        raw_index = None
        index_view = mo.md("Choose a recording with a readable array above.")
    else:
        values = np.asarray(raw_recording.arrays[raw_array_name.value])
        if values.ndim == 2 and values.shape[0] > 0:
            raw_index = mo.ui.dropdown(
                options=list(range(values.shape[0])),
                value=0,
                label="First-axis index",
            )
            index_view = mo.vstack(
                [
                    raw_index,
                    mo.md(f"Array shape: {values.shape} · dtype: {values.dtype}"),
                ]
            )
        else:
            raw_index = None
            index_view = mo.md(f"Array shape: {values.shape} · dtype: {values.dtype}")
    index_view
    return (raw_index,)


@app.cell
def _(array_frame, field_chart, mo, np, pl, raw_array_name, raw_index, raw_recording, raw_run):
    selected_array_frame = pl.DataFrame(
        schema={"sample": pl.Int64, "value": pl.Float64}
    )
    if raw_recording is None or raw_array_name is None or raw_run is None:
        raw_array_plot = mo.md("Select a readable recording and array above to plot its data.")
    else:
        values = np.asarray(raw_recording.arrays[raw_array_name.value])
        if values.ndim == 1:
            index = None
            selected_array_frame = array_frame(values)
        elif values.ndim == 2 and values.shape[0] > 0:
            index = int(raw_index.value) if raw_index is not None else 0
            selected_array_frame = array_frame(values, index=index)
        else:
            index = None
            raw_array_plot = mo.md(
                f"Array shape {values.shape} is not a non-empty 1-D or 2-D array; "
                "choose another array rather than flattening it implicitly."
            )

        if selected_array_frame.height:
            selected_array_frame = selected_array_frame.with_columns(
                pl.lit(raw_run.value).alias("run"),
                pl.lit(raw_array_name.value).alias("array"),
                pl.lit(index, dtype=pl.Int64).alias("array_index"),
            )
            try:
                figure = field_chart(
                    selected_array_frame,
                    x="sample",
                    y="value",
                    color=None,
                    title=f"{raw_run.value} · {raw_array_name.value}"
                    + (f" · row {index}" if index is not None else ""),
                )
            except ValueError as exc:
                raw_array_plot = mo.md(f"**Cannot plot this array:** {exc}")
            else:
                raw_array_plot = mo.vstack(
                    [
                        mo.md(
                            f"Shape: {values.shape} · plotting "
                            + (f"row {index}" if index is not None else "the full array")
                        ),
                        figure,
                    ]
                )
        else:
            raw_array_plot = mo.md("The selected array or row contains no samples to plot.")
    raw_array_plot
    return (selected_array_frame,)


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
            mo.md(
                "## 4 · Build a plot\nChoose the tidy table, then choose columns for the axes, colour, and line grouping."
            ),
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
        pl.Int8,
        pl.Int16,
        pl.Int32,
        pl.Int64,
        pl.UInt8,
        pl.UInt16,
        pl.UInt32,
        pl.UInt64,
        pl.Float32,
        pl.Float64,
    }
    numeric = [column for column, dtype in plot_frame.schema.items() if dtype in numeric_types]
    plot_columns = [
        column for column in plot_frame.columns if column not in {"run_id", "path", "kind"}
    ]

    if plot_frame.is_empty() or not numeric or not plot_columns:
        x_field = y_field = color_field = detail_field = mark_type = None
        plot_controls = mo.md("Mark recordings of this type above to populate this plot.")
    else:
        x_default = next(
            (
                column
                for column in ("offset_mhz", "rbw_khz", "channel", "time", "point")
                if column in plot_columns
            ),
            plot_columns[0],
        )
        y_default = next(
            (
                column
                for column in ("rel_noise_mean_db", "rms", "value", "peak_to_peak")
                if column in numeric
            ),
            numeric[0],
        )
        color_options = ["(none)", *categorical]
        color_default = (
            "run" if "run" in categorical else "channel" if "channel" in categorical else "(none)"
        )
        detail_default = (
            "channel"
            if source.value == "Scope waveform samples" and "channel" in categorical
            else "(none)"
        )
        x_field = mo.ui.dropdown(options=plot_columns, value=x_default, label="X axis")
        y_field = mo.ui.dropdown(options=numeric, value=y_default, label="Y axis")
        color_field = mo.ui.dropdown(options=color_options, value=color_default, label="Color by")
        detail_field = mo.ui.dropdown(
            options=color_options,
            value=detail_default,
            label="Split lines by",
        )
        mark_default = "bars" if source.value == "Scope channel statistics" else "line"
        mark_type = mo.ui.dropdown(
            options=["line", "points", "bars"],
            value=mark_default,
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
        plot_view = mo.md(
            "No plottable rows yet. Mark some recordings and choose a populated data table."
        )
    else:
        color = None if color_field.value == "(none)" else color_field.value
        detail = None if detail_field.value == "(none)" else detail_field.value
        try:
            plot_view = field_chart(
                plot_frame,
                x=x_field.value,
                y=y_field.value,
                color=color,
                detail=detail,
                mark=mark_type.value,
                title=f"{source.value} · {y_field.value} vs {x_field.value}",
            )
        except ValueError as exc:
            plot_view = mo.md(f"**Cannot draw this combination:** {exc}")
    plot_view
    return


@app.cell
def _(mo):
    verify_button = mo.ui.run_button(label="Verify SHA-256 checksums of marked files")
    mo.vstack(
        [
            mo.md(
                "## 5 · Check and export\nChecksum verification reads each selected NPZ from disk; it does not alter recordings."
            ),
            verify_button,
        ]
    )
    return (verify_button,)


@app.cell
def _(marked, mo, verify, verify_button):
    if verify_button.value:
        verification = verify(marked)
    else:
        verification = mo.md(
            "Run checksum verification to check whether the saved arrays match their manifests."
        )
    verification
    return


@app.cell
def _(channel_stats, csv_bytes, marked, mo, points, selected_array_frame, traces):
    mo.vstack(
        [
            mo.md("### Download tables"),
            mo.hstack(
                [
                    mo.download(
                        data=csv_bytes(marked), filename="iyzee-catalog.csv", label="Catalog CSV"
                    ),
                    mo.download(
                        data=csv_bytes(points),
                        filename="iyzee-sweep-points.csv",
                        label="Sweep points CSV",
                    ),
                    mo.download(
                        data=csv_bytes(channel_stats),
                        filename="iyzee-scope-channels.csv",
                        label="Scope statistics CSV",
                    ),
                    mo.download(
                        data=csv_bytes(traces),
                        filename="iyzee-scope-traces.csv",
                        label="Scope waveforms CSV",
                    ),
                ],
                align="start",
            ),
            mo.download(
                data=csv_bytes(selected_array_frame),
                filename="iyzee-selected-array.csv",
                label="Selected raw array CSV",
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
def _(mo):
    comparison_kind = mo.ui.dropdown(
        options=["frequency", "bandwidth"],
        value="frequency",
        label="Sweep kind for point-by-point comparison",
    )
    mo.vstack(
        [
            mo.md("## Compare runs on a shared x-grid"),
            comparison_kind,
        ]
    )
    return (comparison_kind,)


@app.cell
def _(comparison_kind, mo, pl, points):
    comparison_points = points.filter(pl.col("kind") == comparison_kind.value)
    run_labels = comparison_points.select("run").unique(maintain_order=True)["run"].to_list()
    if run_labels:
        reference_run = mo.ui.dropdown(
            options=run_labels,
            value=run_labels[0],
            label="Reference run",
        )
        reference_view = reference_run
    else:
        reference_run = None
        reference_view = mo.md("Mark at least one run of the selected sweep kind above.")
    reference_view
    return comparison_points, reference_run


@app.cell
def _(comparison_points, delta_to_reference, mo, pl, reference_run):
    if reference_run is None or comparison_points["run_id"].n_unique() < 2:
        delta_table = mo.md(
            "Select multiple runs of the same sweep type to compare them against a reference."
        )
    else:
        reference_id = comparison_points.filter(pl.col("run") == reference_run.value)["run_id"][0]
        differences = delta_to_reference(comparison_points, reference_id)
        delta_table = mo.vstack(
            [
                mo.md(
                    "Positive values mean more relative noise than the reference. "
                    "Other runs are interpolated only over the shared x-range."
                ),
                mo.ui.table(differences, selection=None, page_size=12),
            ]
        )
    delta_table
    return


if __name__ == "__main__":
    app.run()
