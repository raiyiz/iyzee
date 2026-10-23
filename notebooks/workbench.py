"""Interactive, read-only workbench for iyzee measurement recordings.

Launch with:
    uv run --group notebooks marimo edit notebooks/workbench.py

To try it without instruments:
    uv run scripts/demo_data.py demo-data
    IYZEE_DATA_DIR=demo-data uv run --group notebooks marimo edit notebooks/workbench.py

The flow is: find recordings -> build a working set -> every later section analyses that
set. The set is a ``Workset`` (see ``notebooks/workset.py``), so everything below is also
available in a script or IPython with two lines of code.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="full", app_title="iyzee · data workbench")


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
        KINDS,
        SWEEP_METRICS,
        default_selection,
        figure_bytes,
        pick,
        scan_catalog,
    )
    from workset import Workset

    return (
        KINDS,
        Path,
        SWEEP_METRICS,
        Workset,
        data_root,
        default_selection,
        figure_bytes,
        mo,
        pick,
        pl,
        scan_catalog,
    )


@app.cell
def _(mo):
    mo.md("""
    # iyzee · Data workbench

    **1 Find** a directory → **2 Select** recordings into a *working set* → **3 Overview**,
    **4 Compare** and **5 Toolbox** all analyse that set.

    The working set stays in the **sidebar** (open it with the ☰ button on the left), and every section
    repeats which runs it is using. Each recording has a stable id like `#07` that is the same in every
    table, legend and plot, and a run keeps one colour in all plots.

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
                "Press **Refresh index** after files are added while the notebook is open "
                "(your working set is kept)."
            ),
            mo.hstack([data_directory, refresh_index], align="end"),
        ]
    )
    return data_directory, refresh_index


@app.cell
def _(Path, data_directory):
    root = Path(data_directory.value).expanduser()
    return (root,)


@app.cell
def _(mo, refresh_index, root, scan_catalog):
    refresh_index.value  # Re-index the directory when the button is pressed.
    catalog = scan_catalog(root)
    if not root.is_dir():
        index_status = mo.md(f"**Directory not found:** {root}")
    elif catalog.is_empty():
        index_status = mo.md(
            f"No .npz recordings found in {root}. Create demo data with "
            "`uv run scripts/demo_data.py demo-data` and point the directory above at it."
        )
    else:
        index_status = mo.md(f"Indexed **{catalog.height:,} recordings** in {root}.")
    index_status
    return (catalog,)


@app.cell
def _(default_selection, mo, root):
    # The working set is a list of file paths that survives filtering and re-indexing;
    # it starts with the newest recordings and is reset only when the directory changes.
    get_selection, set_selection = mo.state(default_selection(root))
    return get_selection, set_selection


@app.cell
def _(KINDS, catalog, mo):
    kind_filter = mo.ui.dropdown(options=["all", *KINDS], value="all", label="Type")
    status_filter = mo.ui.dropdown(
        options=["all", *sorted(catalog["status"].unique().to_list())],
        value="all",
        label="Status",
    )
    origin_filter = mo.ui.dropdown(
        options=["all", "measured only", "synthetic only"], value="all", label="Origin"
    )
    text_filter = mo.ui.text(
        value="",
        placeholder="name, status, instrument, path ...",
        label="Find",
        debounce=True,
    )
    mo.vstack(
        [
            mo.md(
                "## 2 · Select recordings\n"
                "Filter the list, **tick rows**, then add them to the working set. "
                "Filters never change the working set, so you can collect runs "
                "of different types and dates."
            ),
            mo.hstack([kind_filter, status_filter, origin_filter, text_filter], align="end"),
        ]
    )
    return kind_filter, origin_filter, status_filter, text_filter


@app.cell
def _(
    catalog,
    get_selection,
    kind_filter,
    mo,
    origin_filter,
    pick,
    pl,
    status_filter,
    text_filter,
):
    visible = pick(
        catalog,
        kind=kind_filter.value,
        status=status_filter.value,
        synthetic={"all": None, "measured only": False, "synthetic only": True}[
            origin_filter.value
        ],
        text=text_filter.value,
    )
    _chosen = set(get_selection())
    catalog_table = mo.ui.table(
        visible.select(
            pl.when(pl.col("path").is_in(list(_chosen)))
            .then(pl.lit("✓"))
            .otherwise(pl.lit(""))
            .alias("in set"),
            "ref",
            "name",
            "kind",
            "started",
            "status",
            "items",
            "synthetic",
            "warnings",
            "size_kib",
            "digest",
        ),
        selection="multi",
        page_size=12,
        show_column_summaries=False,
    )
    mo.vstack(
        [
            mo.md(
                f"**{visible.height:,} of {catalog.height:,} recordings shown** · "
                f"{int(catalog['synthetic'].sum()) if catalog.height else 0} synthetic"
            ),
            catalog_table,
        ]
    )
    return catalog_table, visible


@app.cell
def _(catalog, catalog_table, mo, set_selection, visible):
    _ref_to_path = dict(zip(catalog["ref"], catalog["path"], strict=True))

    def _ticked():
        value = catalog_table.value
        rows = value.to_dicts() if hasattr(value, "to_dicts") else list(value or [])
        return [_ref_to_path[r["ref"]] for r in rows if r["ref"] in _ref_to_path]

    def _add(paths):
        set_selection(lambda current: [*current, *[p for p in paths if p not in current]])

    add_ticked = mo.ui.button(label="Add ticked rows", on_click=lambda _: _add(_ticked()))
    add_newest = mo.ui.button(
        label="Add 3 newest shown",
        on_click=lambda _: _add(visible.sort("started", descending=True).head(3)["path"].to_list()),
    )
    add_shown = mo.ui.button(
        label=f"Add all {visible.height} shown", on_click=lambda _: _add(visible["path"].to_list())
    )
    replace_ticked = mo.ui.button(
        label="Replace set with ticked rows", on_click=lambda _: set_selection(_ticked())
    )
    clear_set = mo.ui.button(label="Clear set", on_click=lambda _: set_selection([]), kind="danger")
    mo.hstack([add_ticked, add_newest, add_shown, replace_ticked, clear_set], justify="start")
    return


@app.cell
def _(Workset, catalog, get_selection, mo, pl):
    ws = Workset.from_catalog(catalog, get_selection(), strict=False)
    ws_table = mo.ui.table(
        ws.rows.select("ref", "name", "kind", "started", "status", "synthetic", "warnings"),
        selection="multi",
        page_size=10,
        show_column_summaries=False,
    )
    return ws, ws_table


@app.cell
def _(mo, set_selection, ws, ws_table):
    def _remove():
        value = ws_table.value
        rows = value.to_dicts() if hasattr(value, "to_dicts") else list(value or [])
        drop = {r["ref"] for r in rows}
        paths = {p for ref, p in zip(ws.rows["ref"], ws.rows["path"], strict=True) if ref in drop}
        set_selection(lambda current: [p for p in current if p not in paths])

    remove_ticked = mo.ui.button(label="Remove ticked from set", on_click=lambda _: _remove())
    mo.vstack(
        [
            mo.md(f"### Working set · {len(ws)} recording(s)"),
            mo.Html(ws.banner(title="In use")),
            mo.md(
                "Not found in the current index (skipped): " + ", ".join(ws.missing)
                if ws.missing
                else ""
            ),
            ws_table,
            remove_ticked,
        ]
    )
    return


@app.cell
def _(mo, ws):
    _summary = ws.summary()
    mo.sidebar(
        [
            mo.md("## Working set"),
            mo.Html(ws.banner(title=f"{len(ws)} run(s)", vertical=True)),
            mo.md(
                f"{_summary['items']} sweep points / scope channels · "
                f"{_summary['synthetic']} synthetic\n\n"
                "Change it in **section 2**."
            ),
        ]
    )
    return


@app.cell
def _(mo, pl, ws):
    mo.stop(
        ws.rows.is_empty(),
        mo.md("## 3 · Overview\nThe working set is empty: add recordings in section 2."),
    )
    _board = ws.scoreboard()
    _issues = ws.issues
    mo.vstack(
        [
            mo.md("## 3 · Overview"),
            mo.Html(ws.banner()),
            mo.hstack(
                [
                    mo.stat(len(ws), label="runs"),
                    mo.stat(ws.sweeps.height, label="sweeps"),
                    mo.stat(ws.scopes.height, label="scope acquisitions"),
                    mo.stat(int(ws.rows["synthetic"].sum()), label="synthetic"),
                    mo.stat(int(ws.rows["warnings"].sum()), label="warnings"),
                ],
                justify="start",
            ),
            mo.md(
                "#### Sweep scoreboard · best point per run\n`best_x` is the laser offset (MHz) for frequency "
                "sweeps and the RBW (kHz) for bandwidth sweeps."
            ),
            mo.ui.table(_board, selection=None, page_size=10)
            if not _board.is_empty()
            else mo.md("_No sweeps in the working set._"),
            mo.md("#### Scope channel statistics"),
            mo.ui.table(ws.channel_stats, selection=None, page_size=10)
            if not ws.channel_stats.is_empty()
            else mo.md("_No scope acquisitions in the working set._"),
            mo.md(f"**{_issues.height} recording read issue(s)**")
            if not _issues.is_empty()
            else mo.md(""),
            mo.ui.table(_issues, selection=None, page_size=8)
            if not _issues.is_empty()
            else mo.md(""),
        ]
    )
    return


@app.cell
def _(SWEEP_METRICS, mo, ws):
    quick_metric = mo.ui.dropdown(
        options={label: key for key, label in SWEEP_METRICS.items()},
        value=SWEEP_METRICS["rel_noise_mean_db"],
        label="Sweep metric",
    )
    chosen_channels = mo.ui.multiselect(
        options=ws.channels,
        value=ws.channels[: min(2, len(ws.channels))],
        label="Scope channels",
    )
    split_channels = mo.ui.checkbox(value=True, label="One panel per channel")
    trace_budget = mo.ui.slider(
        start=200, stop=8000, step=200, value=1600, label="Plot points per trace (keeps peaks)"
    )
    mo.vstack(
        [
            mo.md(
                "## 4 · Compare\nStandard comparisons of everything in the working set. "
                "Use the Toolbox below for a different quantity or grouping."
            ),
            mo.Html(ws.banner()),
            mo.hstack([quick_metric, chosen_channels, split_channels, trace_budget], align="end"),
        ]
    )
    return chosen_channels, quick_metric, split_channels, trace_budget


@app.cell
def _(chosen_channels, mo, quick_metric, split_channels, trace_budget, ws):
    _figures = ws.quick_plots(
        metric=quick_metric.value,
        channels=chosen_channels.value,
        budget=int(trace_budget.value),
        facet_channels=split_channels.value,
    )
    mo.vstack(
        [item for heading, figure in _figures for item in (mo.md(f"#### {heading}"), figure)]
        or [mo.md("_Nothing to plot yet: add sweeps or scope recordings to the working set._")]
    )
    return


# --- Toolbox widgets -----------------------------------------------------------------


@app.cell
def _(mo, ws):
    _longest = int(ws.sweeps["items"].max() or 1) if not ws.sweeps.is_empty() else 1
    point_index = mo.ui.number(
        start=0, stop=max(_longest - 1, 0), step=1, value=0, label="Sweep point"
    )
    return (point_index,)


@app.cell
def _(mo, pl, point_index, ws):
    _frame = ws.point_traces(int(point_index.value))
    if _frame.is_empty():
        point_view = mo.md("_No sweeps with this point in the working set._")
    else:
        _relative = _frame.filter(pl.col("signal") == "relative noise")
        _levels = _frame.filter(pl.col("signal") != "relative noise")
        point_view = mo.vstack(
            [
                mo.md(
                    f"Raw traces of point {int(point_index.value)} in every sweep: relative noise "
                    "(squeezing minus shot noise), then both levels per run."
                ),
                ws.plot(
                    _relative,
                    x="sample",
                    y="value",
                    title=f"Relative noise · point {int(point_index.value)}",
                ),
                ws.plot(
                    _levels,
                    x="sample",
                    y="value",
                    color="signal",
                    facet="run",
                    title=f"Squeezing vs shot noise · point {int(point_index.value)}",
                ),
            ]
        )
    point_frame = _frame
    return point_frame, point_view


@app.cell
def _(mo, ws):
    _inventory = ws.array_inventory()
    _names = ws.array_names
    array_name = mo.ui.dropdown(
        options=_names or ["(none)"],
        value="trace_squeezing"
        if "trace_squeezing" in _names
        else (_names[0] if _names else "(none)"),
        label="Saved array",
    )
    array_row = mo.ui.number(start=0, stop=10_000, step=1, value=0, label="Row (2-D arrays)")
    array_inventory_view = (
        mo.ui.table(_inventory.drop("run_id"), selection=None, page_size=10)
        if not _inventory.is_empty()
        else mo.md("_No arrays to list._")
    )
    return array_inventory_view, array_name, array_row


@app.cell
def _(array_name, array_row, mo, ws):
    raw_frame = ws.array(array_name.value, int(array_row.value))
    if raw_frame.is_empty():
        raw_view = mo.md("_No run in the working set has this array (or this row)._")
    else:
        raw_view = ws.plot(
            raw_frame,
            x="sample",
            y="value",
            title=f"{array_name.value} · row {int(array_row.value)}",
        )
    return raw_frame, raw_view


@app.cell
def _(mo, ws):
    _kinds = sorted(ws.points["kind"].unique().to_list()) if not ws.sweeps.is_empty() else []
    delta_kind = mo.ui.dropdown(
        options=_kinds or ["(none)"], value=(_kinds or ["(none)"])[0], label="Sweep kind"
    )
    return (delta_kind,)


@app.cell
def _(SWEEP_METRICS, delta_kind, mo, pl, ws):
    _runs = (
        ws.points.filter(pl.col("kind") == delta_kind.value)["run"]
        .unique(maintain_order=True)
        .to_list()
        if not ws.sweeps.is_empty()
        else []
    )
    delta_reference = mo.ui.dropdown(
        options=_runs or ["(none)"], value=(_runs or ["(none)"])[0], label="Reference run"
    )
    delta_metric = mo.ui.dropdown(
        options={label: key for key, label in SWEEP_METRICS.items()},
        value=SWEEP_METRICS["rel_noise_mean_db"],
        label="Metric",
    )
    return delta_metric, delta_reference


@app.cell
def _(delta_kind, delta_metric, delta_reference, mo, ws):
    try:
        _delta = ws.delta(delta_reference.value, delta_kind.value, delta_metric.value)
    except ValueError as exc:
        delta_frame = ws.points.clear()
        delta_view = mo.md(f"_{exc}_")
    else:
        delta_frame = _delta
        if _delta.is_empty():
            delta_view = mo.md(
                "_Needs at least two runs of this sweep kind with overlapping x ranges._"
            )
        else:
            _x = "offset_mhz" if delta_kind.value == "frequency" else "rbw_khz"
            delta_view = mo.vstack(
                [
                    mo.md(
                        "Other runs minus the reference, interpolated only over the range both cover. "
                        "**Positive = noisier than the reference.**"
                    ),
                    ws.plot(_delta, x=_x, y="delta_db", title=f"Δ vs {delta_reference.value}"),
                    mo.ui.table(_delta, selection=None, page_size=10),
                ]
            )
    return delta_frame, delta_view


@app.cell
def _(mo, pl, point_frame, raw_frame, ws):
    frame_map = {
        **ws.tables(),
        "Raw array across runs": raw_frame,
        "Point traces (squeezing / shot noise)": point_frame,
    }
    source = mo.ui.dropdown(
        options=list(frame_map),
        value=next(
            (name for name, frame in frame_map.items() if not frame.is_empty()), "Run scoreboard"
        ),
        label="Data to plot",
    )
    return frame_map, source


@app.cell
def _(frame_map, mo, pl, source):
    plot_frame = frame_map[source.value]
    _numeric = [c for c, d in plot_frame.schema.items() if d.is_numeric()]
    _text = [
        c for c, d in plot_frame.schema.items() if d == pl.String and c not in {"run_id", "path"}
    ]
    _columns = [c for c in plot_frame.columns if c not in {"run_id", "path"}]
    if plot_frame.is_empty() or not _numeric:
        x_field = y_field = color_field = detail_field = facet_field = mark_type = None
        x_log = y_log = None
        plot_controls = mo.md(
            "_The selected table is empty: add matching recordings to the working set._"
        )
    else:
        _x_default = next(
            (
                c
                for c in ("offset_mhz", "rbw_khz", "time", "sample", "channel", "best_db")
                if c in _columns
            ),
            _columns[0],
        )
        _y_default = next(
            (c for c in ("rel_noise_mean_db", "value", "rms", "best_db") if c in _numeric),
            _numeric[0],
        )
        _group = ["(none)", *_text]
        _color_default = "run" if "run" in _text else "(none)"
        _detail_default = "channel" if source.value == "Scope waveform samples" else "(none)"
        _facet_default = "(none)"
        x_field = mo.ui.dropdown(options=_columns, value=_x_default, label="X axis")
        y_field = mo.ui.dropdown(options=_numeric, value=_y_default, label="Y axis")
        color_field = mo.ui.dropdown(options=_group, value=_color_default, label="Colour by")
        detail_field = mo.ui.dropdown(options=_group, value=_detail_default, label="Line style by")
        facet_field = mo.ui.dropdown(options=_group, value=_facet_default, label="Panel per")
        mark_type = mo.ui.dropdown(
            options=["line", "points", "bars"],
            value="bars"
            if source.value in {"Scope channel statistics", "Run scoreboard"}
            else "line",
            label="Mark",
        )
        x_log = mo.ui.checkbox(label="log X")
        y_log = mo.ui.checkbox(label="log Y")
        plot_controls = mo.vstack(
            [
                mo.hstack([x_field, y_field, mark_type], align="start"),
                mo.hstack([color_field, detail_field, facet_field, x_log, y_log], align="start"),
            ]
        )
    return (
        color_field,
        detail_field,
        facet_field,
        mark_type,
        plot_controls,
        plot_frame,
        x_field,
        x_log,
        y_field,
        y_log,
    )


@app.cell
def _(
    color_field,
    detail_field,
    facet_field,
    figure_bytes,
    mark_type,
    mo,
    plot_frame,
    source,
    ws,
    x_field,
    x_log,
    y_field,
    y_log,
):
    def _opt(widget):
        return None if widget.value == "(none)" else widget.value

    custom_figure = None
    if x_field is None or plot_frame.is_empty():
        plot_view = mo.md("_Nothing to plot._")
    else:
        try:
            custom_figure = ws.plot(
                plot_frame,
                x=x_field.value,
                y=y_field.value,
                color=_opt(color_field),
                detail=_opt(detail_field),
                facet=_opt(facet_field),
                mark=mark_type.value,
                x_log=x_log.value,
                y_log=y_log.value,
                title=f"{source.value} · {y_field.value} vs {x_field.value}",
            )
        except ValueError as exc:
            plot_view = mo.md(f"**Cannot draw this combination:** {exc}")
        else:
            _figure = custom_figure
            plot_view = mo.vstack(
                [
                    custom_figure,
                    mo.hstack(
                        [
                            mo.download(
                                lambda: figure_bytes(_figure, "png"),
                                filename="iyzee-plot.png",
                                label="PNG",
                            ),
                            mo.download(
                                lambda: figure_bytes(_figure, "svg"),
                                filename="iyzee-plot.svg",
                                label="SVG",
                            ),
                            mo.download(
                                lambda: figure_bytes(_figure, "pdf"),
                                filename="iyzee-plot.pdf",
                                label="PDF",
                            ),
                        ],
                        justify="start",
                    ),
                ]
            )
    return (plot_view,)


@app.cell
def _(mo):
    verify_button = mo.ui.run_button(label="Verify SHA-256 checksums of the working set")
    return (verify_button,)


@app.cell
def _(mo, verify_button, ws):
    verification = (
        mo.ui.table(ws.verify(), selection=None, page_size=10)
        if verify_button.value
        else mo.md("Check whether the saved arrays still match the checksums in their manifests.")
    )
    return (verification,)


@app.cell
def _(Workset, frame_map, mo, ws):
    _downloads = [
        mo.download(
            lambda frame=frame: Workset.csv(frame),
            filename="iyzee-" + name.lower().replace(" ", "-").replace("/", "-") + ".csv",
            label=name,
        )
        for name, frame in frame_map.items()
    ]
    export_view = mo.vstack(
        [
            mo.md("#### Download tables (CSV)"),
            mo.hstack(
                [
                    mo.download(
                        lambda: Workset.csv(ws.rows),
                        filename="iyzee-working-set.csv",
                        label="Working set",
                    ),
                    *_downloads,
                ],
                justify="start",
                wrap=True,
            ),
        ]
    )
    return (export_view,)


@app.cell
def _(mo):
    recipes_view = mo.md("""
    The same working set is available in code, with no notebook widgets:

    ~~~python
    from workset import Workset            # run from notebooks/, or add it to sys.path

    ws = Workset.open("demo-data", kind="frequency", latest=3)   # filter on load
    ws = Workset.open("demo-data", ["#02", "#05", "scan-pumped"])  # or pick by #ref / name

    ws.points                       # tidy table: one row per sweep point
    ws.scoreboard()                 # best point, mean, count below shot noise
    ws.delta("#02")                 # every run minus run #02 on the shared x range
    ws.traces(["C1"], budget=2000)  # scope waveforms
    ws.point_traces(3)              # raw squeezing / shot-noise traces of point 3
    ws.array("trace_squeezing", 3)  # any saved array, overlaid across runs

    fig = ws.plot(ws.points, x="offset_mhz", y="rel_noise_min_db",
                  mark="points", facet="run")                 # colours follow the runs
    open("fig.png", "wb").write(ws.png(fig))

    # Tables are ordinary Polars frames:
    quiet = ws.points.filter(pl.col("rel_noise_mean_db") < 0).sort("rel_noise_mean_db")
    ~~~
    """)
    return (recipes_view,)


@app.cell
def _(
    array_inventory_view,
    array_name,
    array_row,
    delta_kind,
    delta_metric,
    delta_reference,
    delta_view,
    export_view,
    mo,
    plot_controls,
    plot_view,
    point_index,
    point_view,
    raw_view,
    recipes_view,
    source,
    verification,
    verify_button,
    ws,
):
    mo.vstack(
        [
            mo.md(
                "## 5 · Toolbox\nEach tool works on the working set. Open only the ones you need."
            ),
            mo.Html(ws.banner()),
            mo.accordion(
                {
                    "Custom plot builder": mo.vstack([source, plot_controls, plot_view]),
                    "Point inspector · squeezing vs shot noise": mo.vstack(
                        [point_index, point_view]
                    ),
                    "Raw arrays across runs": mo.vstack(
                        [
                            mo.hstack([array_name, array_row], justify="start"),
                            raw_view,
                            mo.md("#### Arrays in each file"),
                            array_inventory_view,
                        ]
                    ),
                    "Compare against a reference run": mo.vstack(
                        [
                            mo.hstack([delta_kind, delta_reference, delta_metric], justify="start"),
                            delta_view,
                        ]
                    ),
                    "Integrity and export": mo.vstack([verify_button, verification, export_view]),
                    "Use the same working set in code": recipes_view,
                }
            ),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
