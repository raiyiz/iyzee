"""Interactive, read-only workbench for iyzee measurement recordings.

Launch with:
    uv run --extra analysis --group notebooks marimo edit notebooks/workbench.py

To try it without instruments:
    uv run --extra analysis iyz-demo demo-data
    IYZEE_DATA_DIR=demo-data uv run --extra analysis --group notebooks marimo edit notebooks/workbench.py

The flow is: find recordings -> build a working set -> every later section analyses that
set. The set is a ``Workset`` (see ``iyzee.workbench.workset``), so everything below is also
available in a script or IPython with two lines of code.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="full", app_title="iyzee · data workbench")


@app.cell
def _():
    from pathlib import Path

    import marimo as mo
    import polars as pl

    from iyzee.config import data_root
    from iyzee.workbench import (
        BAND_CHOICES,
        KINDS,
        SWEEP_METRICS,
        Workset,
        WorksetStore,
        default_selection,
        figure_bytes,
        pick,
        preview_facts,
        read_manifest,
        scan_catalog,
    )

    return (
        BAND_CHOICES,
        KINDS,
        Path,
        SWEEP_METRICS,
        Workset,
        WorksetStore,
        data_root,
        default_selection,
        figure_bytes,
        mo,
        pick,
        pl,
        preview_facts,
        read_manifest,
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
            "`uv run --extra analysis iyz-demo demo-data` and point the directory above at it."
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
    _dates = catalog["started"].drop_nulls()
    if _dates.is_empty():
        date_bounds = None
        date_filter = None
    else:
        date_bounds = (_dates.min().date(), _dates.max().date())
        date_filter = mo.ui.date_range(
            start=date_bounds[0], stop=date_bounds[1], value=date_bounds, label="Started"
        )
    only_in_set = mo.ui.checkbox(label="Only the working set")
    mo.vstack(
        [
            mo.md(
                "## 2 · Select recordings\n"
                "Filter the list, **tick rows**, then add them to the working set. "
                "Filters never change the working set, so you can collect runs "
                "of different types and dates. Hover a `ref` for details; click a header "
                "to sort; tick a row to preview it below."
            ),
            mo.hstack(
                [kind_filter, status_filter, origin_filter, date_filter, text_filter, only_in_set],
                align="end",
                justify="start",
                wrap=True,
            ),
        ]
    )
    return (
        date_bounds,
        date_filter,
        kind_filter,
        only_in_set,
        origin_filter,
        status_filter,
        text_filter,
    )


@app.cell
def _(
    catalog,
    date_bounds,
    date_filter,
    get_selection,
    kind_filter,
    mo,
    only_in_set,
    origin_filter,
    pick,
    pl,
    preview_facts,
    status_filter,
    text_filter,
):
    _chosen = list(get_selection())
    _window = date_filter.value if date_filter is not None else None
    _narrowed = _window is not None and tuple(_window) != date_bounds
    visible = pick(
        catalog,
        kind=kind_filter.value,
        status=status_filter.value,
        synthetic={"all": None, "measured only": False, "synthetic only": True}[
            origin_filter.value
        ],
        text=text_filter.value,
        since=_window[0] if _narrowed else None,
        until=_window[1] if _narrowed else None,
    )
    if only_in_set.value:
        visible = visible.filter(pl.col("path").is_in(_chosen))

    _hover = {
        row["ref"]: "\n".join(f"{key}: {value}" for key, value in preview_facts(row))
        for row in visible.iter_rows(named=True)
    }

    def _hover_cell(_row_id, column, value):
        return _hover.get(value) if column == "ref" else None

    def _style_cell(_row_id, column, value):
        if column == "in set" and value:
            return {"backgroundColor": "#d7efe0", "fontWeight": "bold", "textAlign": "center"}
        if column == "warnings" and value:
            return {"backgroundColor": "#fde8c8"}
        if column == "status" and value not in {"completed", "acquired"}:
            return {"color": "#b45309"}
        if column == "synthetic" and value:
            return {"fontStyle": "italic", "opacity": "0.75"}
        return {}

    catalog_table = mo.ui.table(
        visible.select(
            pl.when(pl.col("path").is_in(_chosen))
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
            "path",
        ),
        selection="multi",
        page_size=12,
        show_column_summaries=False,
        freeze_columns_left=["in set", "ref"],
        hidden_columns=["path"],
        style_cell=_style_cell,
        hover_template=_hover_cell,
        format_mapping={
            "started": lambda v: v.strftime("%Y-%m-%d %H:%M") if v else "",
            "size_kib": lambda v: f"{v:,.0f}" if v is not None else "",
        },
        header_tooltip={
            "in set": "✓ = already in the working set",
            "ref": "Stable id: 01 is the oldest recording in the index",
            "items": "Sweep points or scope channels stored in the file",
            "warnings": "Warnings recorded by the acquisition",
            "size_kib": "File size in KiB",
            "digest": "Whether a SHA-256 is recorded in the sidecar",
        },
    )
    mo.vstack(
        [
            mo.md(
                f"**{visible.height:,} of {catalog.height:,} recordings shown** · "
                f"{int(catalog['synthetic'].sum()) if catalog.height else 0} synthetic · "
                f"{len(_chosen)} in the working set"
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
def _(Workset, catalog, catalog_table, mo, pl, preview_facts, read_manifest):
    _value = catalog_table.value
    _ticked_rows = _value.to_dicts() if hasattr(_value, "to_dicts") else list(_value or [])
    if not _ticked_rows:
        _view = mo.md(
            "**Preview** · tick a row above to look inside a recording (facts, a thumbnail "
            "of its data, its saved arrays and its sidecar) before adding it."
        )
    else:
        _ref = _ticked_rows[0]["ref"]
        _row = catalog.filter(pl.col("ref") == _ref).row(0, named=True)
        _one = Workset.from_catalog(catalog, [_ref])
        _facts = "| | |\n|---|---|\n" + "\n".join(
            f"| **{key}** | {str(value).replace('|', '/')} |" for key, value in preview_facts(_row)
        )
        _thumb = _one.quick_plots(budget=800)
        _more = (
            f" · showing the first of {len(_ticked_rows)} ticked rows"
            if len(_ticked_rows) > 1
            else ""
        )
        _arrays = _one.array_inventory().drop("run", "run_id")
        _view = mo.vstack(
            [
                mo.md(f"### Preview · {_ref} {_row['name']}{_more}"),
                mo.hstack(
                    [
                        mo.md(_facts),
                        _thumb[0][1] if _thumb else mo.md("_Nothing plottable in this file._"),
                    ],
                    justify="start",
                    align="start",
                    wrap=True,
                ),
                mo.accordion(
                    {
                        "Saved arrays (name, shape, dtype)": mo.ui.table(
                            _arrays, selection=None, page_size=10
                        ),
                        "Sidecar JSON": mo.json(read_manifest(_row["path"])),
                    }
                ),
            ]
        )
    _view
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
def _(WorksetStore, mo):
    # Saved working sets live in the user's iyzee config folder, never in the data directory.
    store = WorksetStore()
    get_saved_rev, set_saved_rev = mo.state(0)
    get_notice, set_notice = mo.state("")
    # run_id -> group label. A plain dict on purpose: editing groups must not re-run cells.
    group_store: dict[str, str] = {}
    get_group_rev, set_group_rev = mo.state(0)
    return (
        get_group_rev,
        get_notice,
        get_saved_rev,
        group_store,
        set_group_rev,
        set_notice,
        set_saved_rev,
        store,
    )


@app.cell
def _(get_saved_rev, mo, store):
    get_saved_rev()  # rebuild the inputs after a save or delete
    _names = store.names()
    set_name = mo.ui.text(
        label="Save current set as", placeholder="e.g. repeat scans", max_length=80
    )
    saved_pick = mo.ui.dropdown(
        options=_names or ["(none saved)"],
        value=(_names or ["(none saved)"])[0],
        label="Saved sets",
    )
    confirm_delete = mo.ui.checkbox(label="confirm delete")
    mo.vstack(
        [
            mo.md("### Saved working sets"),
            mo.hstack(
                [set_name, saved_pick, confirm_delete], align="end", justify="start", wrap=True
            ),
        ]
    )
    return confirm_delete, saved_pick, set_name


@app.cell
def _(
    Workset,
    catalog,
    confirm_delete,
    get_notice,
    group_store,
    mo,
    saved_pick,
    set_group_rev,
    set_name,
    set_notice,
    set_saved_rev,
    set_selection,
    store,
    ws,
):
    def _save():
        try:
            entry = store.save(set_name.value, ws, groups=dict(group_store))
        except (ValueError, OSError) as exc:
            set_notice(f"⚠ Not saved: {exc}")
            return
        set_saved_rev(lambda n: n + 1)
        set_notice(f"Saved “{entry.name}”: {len(entry.runs)} runs, {len(entry.groups)} grouped.")

    def _load():
        name = saved_pick.value
        if name not in store.names():
            set_notice("⚠ Nothing to load: save a set first.")
            return
        loaded = Workset.from_saved(catalog, store.get(name))
        group_store.clear()
        group_store.update(loaded.group_map)
        set_selection(loaded.paths)
        set_group_rev(lambda n: n + 1)
        gone = f" · **not found now:** {', '.join(loaded.missing)}" if loaded.missing else ""
        set_notice(f"Loaded “{name}”: {len(loaded)} runs{gone}.")

    def _delete():
        name = saved_pick.value
        if not confirm_delete.value:
            set_notice("⚠ Tick “confirm delete” first.")
        elif store.delete(name):
            set_saved_rev(lambda n: n + 1)
            set_notice(f"Deleted “{name}”.")

    save_set = mo.ui.button(label="Save", on_click=lambda _: _save(), kind="success")
    load_set = mo.ui.button(label="Load (replaces the working set)", on_click=lambda _: _load())
    delete_set = mo.ui.button(label="Delete", on_click=lambda _: _delete(), kind="danger")
    _table = store.describe()
    mo.vstack(
        [
            mo.hstack([save_set, load_set, delete_set], justify="start"),
            mo.md(get_notice() or " "),
            mo.md(f"⚠ {store.problem}") if store.problem else mo.md(""),
            mo.ui.table(_table, selection=None, page_size=5, show_column_summaries=False)
            if not _table.is_empty()
            else mo.md("_No saved sets yet._"),
            mo.md(
                f"<small>Stored in `{store.path}`. Sets are matched back to files by path, then "
                "run id; `#refs` can shift when older recordings are added.</small>"
            ),
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
def _(get_group_rev, group_store, mo, pl, ws):
    get_group_rev()  # rebuild the editor after Load or "Fill groups"
    _run_by_ref = dict(zip(ws.rows["ref"], ws.rows["run_id"], strict=True))

    def _remember(edited):
        rows = edited.to_dicts() if hasattr(edited, "to_dicts") else list(edited)
        for row in rows:
            run = _run_by_ref.get(row["ref"])
            if run is None:
                continue
            label = str(row.get("group") or "").strip()
            if label:
                group_store[run] = label
            else:
                group_store.pop(run, None)

    group_editor = mo.ui.data_editor(
        ws.rows.select("ref", "name", "kind").with_columns(
            pl.Series(
                "group", [group_store.get(run, "") for run in ws.rows["run_id"]], dtype=pl.String
            )
        ),
        editable_columns=["group"],
        on_change=_remember,
        pagination=False,
    )
    return (group_editor,)


@app.cell
def _(group_editor, ws):
    # The groups in force: the typed labels, applied to the working set.
    _edited = group_editor.value
    _rows = _edited.to_dicts() if hasattr(_edited, "to_dicts") else list(_edited or [])
    _run_by_ref = dict(zip(ws.rows["ref"], ws.rows["run_id"], strict=True))
    ws_grouped = ws.with_groups(
        {
            _run_by_ref[row["ref"]]: str(row.get("group") or "").strip()
            for row in _rows
            if row["ref"] in _run_by_ref
        }
    )
    return (ws_grouped,)


@app.cell
def _(BAND_CHOICES, SWEEP_METRICS, mo, pl, ws_grouped):
    _in_groups = ws_grouped.points.filter(pl.col("run_id").is_in(list(ws_grouped.group_map)))
    _kinds = sorted(_in_groups["kind"].unique().to_list()) if not _in_groups.is_empty() else []
    _names = ws_grouped.group_names
    _band_text = {
        "std": "±1 standard deviation",
        "sem": "±1 standard error",
        "minmax": "min to max",
        "none": "no band",
    }
    _band_labels = {_band_text[choice]: choice for choice in BAND_CHOICES}
    group_kind = mo.ui.dropdown(
        options=_kinds or ["(none)"], value=(_kinds or ["(none)"])[0], label="Sweep kind"
    )
    group_metric = mo.ui.dropdown(
        options={label: key for key, label in SWEEP_METRICS.items()},
        value=SWEEP_METRICS["rel_noise_mean_db"],
        label="Metric",
    )
    group_band = mo.ui.dropdown(options=_band_labels, value="±1 standard deviation", label="Band")
    group_a = mo.ui.dropdown(
        options=_names or ["(none)"], value=(_names or ["(none)"])[0], label="A"
    )
    group_b = mo.ui.dropdown(
        options=_names or ["(none)"],
        value=(_names[1] if len(_names) > 1 else (_names or ["(none)"])[0]),
        label="B",
    )
    auto_by = mo.ui.dropdown(
        options=["recording name", "type", "status", "origin"],
        value="recording name",
        label="Fill groups from",
    )
    return auto_by, group_a, group_b, group_band, group_kind, group_metric


@app.cell
def _(auto_by, group_store, mo, set_group_rev, ws):
    def _fill():
        for row in ws.rows.iter_rows(named=True):
            label = {
                "recording name": row["name"],
                "type": row["kind"],
                "status": row["status"],
                "origin": "synthetic" if row["synthetic"] else "measured",
            }[auto_by.value]
            if label:
                group_store[row["run_id"]] = str(label)
        set_group_rev(lambda n: n + 1)

    def _clear():
        group_store.clear()
        set_group_rev(lambda n: n + 1)

    fill_groups = mo.ui.button(label="Fill groups", on_click=lambda _: _fill())
    clear_groups = mo.ui.button(
        label="Clear all groups", on_click=lambda _: _clear(), kind="danger"
    )
    return clear_groups, fill_groups


@app.cell
def _(
    group_a,
    group_b,
    group_band,
    group_kind,
    group_metric,
    mo,
    pl,
    ws_grouped,
):
    _names = ws_grouped.group_names
    _kind = None if group_kind.value == "(none)" else group_kind.value
    if not _names:
        groups_view = mo.md(
            "_No groups yet. Type a label (for example `baseline`, `pumped`) into the **group** column "
            "above for each run, or use **Fill groups**. Runs without a label are ignored here._"
        )
    else:
        _metric = group_metric.value
        _parts = [
            mo.md("#### Per-group summary\nSpread of each run's **best** point within its group."),
            mo.ui.table(
                ws_grouped.group_summary(_metric),
                selection=None,
                page_size=8,
                show_column_summaries=False,
            ),
        ]
        try:
            _parts += [
                mo.md(
                    "#### Group means\nEach run is interpolated onto one shared x grid (no extrapolation); "
                    "a group needs **two or more runs** for a spread band."
                ),
                ws_grouped.plot_groups(_metric, _kind, group_band.value),
            ]
        except ValueError as exc:
            _parts.append(mo.md(f"_No group means to draw: {exc}_"))
        if len(_names) > 1 and group_a.value != group_b.value:
            try:
                _diff = ws_grouped.group_diff(group_a.value, group_b.value, _metric, _kind)
            except ValueError as exc:
                _parts.append(mo.md(f"_No A/B difference: {exc}_"))
            else:
                _flagged = int(_diff["significant"].sum())
                _small = min(_diff["n_a"][0], _diff["n_b"][0]) < 3 if _diff.height else True
                _parts += [
                    mo.md(
                        f"#### Difference: {group_a.value} minus {group_b.value}\n"
                        f"**{_flagged} of {_diff.height}** shared x points differ by more than "
                        '2 standard errors (Welch). This is a rough guide to "bigger than the run-to-run '
                        'scatter", not a p-value.'
                        + (
                            " **With fewer than 3 runs in a group it is only indicative.**"
                            if _small
                            else ""
                        )
                    ),
                    ws_grouped.plot_group_diff(group_a.value, group_b.value, _metric, _kind),
                    mo.ui.table(_diff, selection=None, page_size=8, show_column_summaries=False),
                ]
        elif len(_names) > 1:
            _parts.append(mo.md("_Pick two different groups for A and B to see their difference._"))
        groups_view = mo.vstack(_parts)
    return (groups_view,)


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
    from iyzee.workbench import Workset

    ws = Workset.open("demo-data", kind="frequency", latest=3)   # filter on load
    ws = Workset.open("demo-data", ["#02", "#05", "scan-pumped"])  # or pick by #ref / name

    ws.points                       # tidy table: one row per sweep point
    ws.scoreboard()                 # best point, mean, count below shot noise
    ws.delta("#02")                 # every run minus run #02 on the shared x range
    ws.traces(["C1"], budget=2000)  # scope waveforms
    ws.point_traces(3)              # raw squeezing / shot-noise traces of point 3
    ws.array("trace_squeezing", 3)  # any saved array, overlaid across runs

    g = ws.with_groups({"#02": "base", "#05": "pumped", "#07": "pumped"})
    g.plot_groups(band="sem")       # group means with a spread band
    g.group_diff("pumped", "base")  # difference, standard error, |t| > 2 flag
    WorksetStore().save("my set", g)  # saved sets keep groups

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
    auto_by,
    clear_groups,
    delta_kind,
    delta_metric,
    delta_reference,
    delta_view,
    export_view,
    fill_groups,
    group_a,
    group_b,
    group_band,
    group_editor,
    group_kind,
    group_metric,
    groups_view,
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
                    "Groups and statistics (mean ± spread, A vs B)": mo.vstack(
                        [
                            mo.md(
                                "Label runs with a **group** (editable column), then compare groups. "
                                "Labels are kept when you change the working set and are saved with it."
                            ),
                            group_editor,
                            mo.hstack(
                                [auto_by, fill_groups, clear_groups], align="end", justify="start"
                            ),
                            mo.hstack([group_kind, group_metric, group_band], justify="start"),
                            mo.hstack([group_a, group_b], justify="start"),
                            groups_view,
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
