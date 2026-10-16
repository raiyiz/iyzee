"""Explore and compare iyzee recordings with Polars and reusable Matplotlib helpers.

From the iyzee checkout:
    uv run --with marimo --with polars marimo edit notebooks/sweep_scope_analysis.py

Read-only: this notebook loads saved recordings and never connects to hardware.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import hashlib
    import json
    from datetime import datetime
    from pathlib import Path

    import marimo as mo
    import matplotlib.pyplot as plt
    import numpy as np
    import polars as pl

    from iyzee.analysis import summarize_sweep
    from iyzee.config import data_root
    from iyzee.experiment import Recording, load_recording
    from iyzee.waveform_math import Trace, traces_from_scope_recording

    return (
        Path,
        Recording,
        Trace,
        data_root,
        datetime,
        hashlib,
        json,
        load_recording,
        mo,
        np,
        pl,
        plt,
        summarize_sweep,
        traces_from_scope_recording,
    )


@app.cell
def _(mo):
    mo.md("""
    # iyzee · Run analysis workbench

    The data-first notebook: **one catalog row per recording**, one row per sweep
    point, and one row per scope channel. Use the Polars frames for filtering,
    grouping, joins, list comprehensions, and quick comparisons. Load waveform
    arrays only when you need to inspect a particular run.

    The plotting functions below are reusable outside this notebook:
    **plot_sweep_comparison(...)**, **plot_scope(...)**, and **plot_trace(...)**.
    They accept an existing Matplotlib ax, so you can compose them into
    publication figures without changing their internals.

    All access is read-only. No instrument connection or acquisition is performed.
    Negative relative noise means the squeezing trace is below its shot-noise reference.
    """)
    return


@app.cell
def _(
    Path,
    Recording,
    Trace,
    data_root,
    datetime,
    hashlib,
    json,
    load_recording,
    np,
    pl,
    plt,
    traces_from_scope_recording,
):
    DATA_ROOT = data_root()

    def read_manifest(path: Path) -> dict:
        """Read the JSON sidecar; return an empty mapping if it is absent or invalid."""
        try:
            value = json.loads(path.with_suffix(".json").read_text())
        except OSError, json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _run_kind(path: Path, manifest: dict) -> str:
        if manifest.get("kind") == "scope-acquisition" or "_scope_" in path.name:
            return "scope"
        points = manifest.get("points") or []
        if (
            any(
                isinstance(point, dict)
                and ("wavemeter_channel" in point or "measured_frequency_thz" in point)
                for point in points
            )
            or "_frequency_" in path.name
        ):
            return "frequency"
        if points or manifest.get("run_metadata") or "_bandwidth_" in path.name:
            return "bandwidth"
        return "unknown"

    def build_catalog(paths: list[Path]) -> pl.DataFrame:
        """Build a lightweight index from sidecars without loading waveform arrays."""
        rows = []
        for path in paths:
            manifest = read_manifest(path)
            kind = _run_kind(path, manifest)
            run = manifest.get("run_metadata") or {}
            instrument = manifest.get("instrument") or {}
            acquisition = manifest.get("acquisition") or {}
            points = manifest.get("points") or []
            waveforms = manifest.get("waveforms") or []
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
                size_mib = path.stat().st_size / (1024**2)
            except OSError:
                modified = None
                size_mib = None
            rows.append(
                {
                    "run_id": path.stem,
                    "path": str(path.resolve()),
                    "kind": kind,
                    "modified_at": modified.isoformat(timespec="seconds") if modified else None,
                    "size_mib": size_mib,
                    "status": str(
                        run.get("status") or ("acquired" if kind == "scope" else "unknown")
                    ),
                    "n_points": len(points) if kind in ("frequency", "bandwidth") else None,
                    "n_channels": len(waveforms) if kind == "scope" else None,
                    "instrument": str(instrument.get("identity") or ""),
                    "address": str(instrument.get("address") or ""),
                    "frozen": acquisition.get("frozen") if kind == "scope" else None,
                    "warnings": len(acquisition.get("warnings") or []) if kind == "scope" else 0,
                    "checksum_recorded": bool(manifest.get("data_sha256")),
                }
            )
        schema = {
            "run_id": pl.String,
            "path": pl.String,
            "kind": pl.String,
            "modified_at": pl.String,
            "size_mib": pl.Float64,
            "status": pl.String,
            "n_points": pl.Int64,
            "n_channels": pl.Int64,
            "instrument": pl.String,
            "address": pl.String,
            "frozen": pl.Boolean,
            "warnings": pl.Int64,
            "checksum_recorded": pl.Boolean,
        }
        return pl.DataFrame(rows, schema=schema)

    def load_run(run_id: str) -> Recording:
        """Load one archive by its catalog run_id; arrays are loaded on demand."""
        matches = catalog.filter(pl.col("run_id") == run_id)
        if matches.is_empty():
            raise KeyError(f"No recording with run_id={run_id!r}")
        return load_recording(Path(matches.item(0, "path")))

    def verify_run(run_id: str) -> dict[str, str | bool]:
        """Compare one NPZ's SHA-256 with the digest stored in its sidecar."""
        matches = catalog.filter(pl.col("run_id") == run_id)
        if matches.is_empty():
            raise KeyError(f"No recording with run_id={run_id!r}")
        path = Path(matches.item(0, "path"))
        manifest = read_manifest(path)
        expected = manifest.get("data_sha256")
        if not expected:
            return {"run_id": run_id, "ok": False, "state": "no digest in manifest"}
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        ok = digest.hexdigest() == expected
        return {
            "run_id": run_id,
            "ok": ok,
            "state": "matches manifest" if ok else "MISMATCH",
        }

    def _styled_axes(ax, *, title=None, xlabel=None, ylabel=None):
        """Apply the shared house style to any Matplotlib axes."""
        if title is not None:
            ax.set_title(title, loc="left", pad=10)
        if xlabel is not None:
            ax.set_xlabel(xlabel)
        if ylabel is not None:
            ax.set_ylabel(ylabel)
        ax.grid(True, which="major", alpha=0.25)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        return ax.figure, ax

    def plot_trace(trace: Trace, *, ax=None, title=None, label=None, linewidth=1.2):
        """Plot one iyzee Trace; pass ax to compose it into an existing figure."""
        if ax is None:
            _, ax = plt.subplots(figsize=(8.5, 4), layout="constrained")
        ax.plot(trace.time, trace.values, label=label or trace.label, linewidth=linewidth)
        _styled_axes(
            ax,
            title=title or trace.label,
            xlabel=f"Time ({trace.time_unit})",
            ylabel=f"Signal ({trace.value_unit})",
        )
        ax.legend(frameon=False)
        return ax.figure, ax

    def plot_scope(recording: Recording, *, channels=None, ax=None, title=None):
        """Overlay selected scope channels that share a value unit."""
        traces = traces_from_scope_recording(recording)
        selected = set(channels) if channels is not None else None
        traces = [trace for trace in traces if selected is None or trace.label in selected]
        if not traces:
            raise ValueError("No matching scope channels have waveform data")
        units = {trace.value_unit for trace in traces}
        if len(units) > 1:
            raise ValueError(
                "Selected channels use different units; plot them on separate axes instead"
            )
        if ax is None:
            _, ax = plt.subplots(figsize=(8.5, 4), layout="constrained")
        for trace in traces:
            ax.plot(trace.time, trace.values, label=trace.label, linewidth=1.0)
        _styled_axes(
            ax,
            title=title or f"Scope capture · {recording.path.stem}",
            xlabel=f"Time ({traces[0].time_unit})",
            ylabel=f"Signal ({traces[0].value_unit})",
        )
        ax.legend(frameon=False, ncols=min(len(traces), 4))
        return ax.figure, ax

    def plot_scope_comparison(
        run_ids: list[str],
        *,
        channel: str,
        ax=None,
        title=None,
    ):
        """Overlay one channel across runs; all selected traces must share units."""
        traces = []
        for run_id in run_ids:
            recording = load_run(run_id)
            trace = next(
                (
                    trace
                    for trace in traces_from_scope_recording(recording)
                    if trace.label == channel
                ),
                None,
            )
            if trace is not None:
                traces.append((run_id, trace))
        if not traces:
            raise ValueError(f"No saved data for channel {channel!r} in the selected runs")
        units = {(trace.time_unit, trace.value_unit) for _, trace in traces}
        if len(units) > 1:
            raise ValueError("Selected runs use different units; compare like-for-like traces")
        if ax is None:
            _, ax = plt.subplots(figsize=(8.5, 4), layout="constrained")
        for run_id, trace in traces:
            ax.plot(trace.time, trace.values, label=run_id, linewidth=1.0)
        _styled_axes(
            ax,
            title=title or f"Scope comparison · {channel}",
            xlabel=f"Time ({traces[0][1].time_unit})",
            ylabel=f"Signal ({traces[0][1].value_unit})",
        )
        ax.legend(frameon=False, fontsize="small")
        return ax.figure, ax

    def plot_sweep_comparison(
        points: pl.DataFrame,
        run_ids: list[str],
        *,
        statistic="mean",
        ax=None,
        frequency_offset_mhz=True,
    ):
        """Compare sweep runs on a shared axis using the normalized point table."""
        if statistic not in ("mean", "minimum"):
            raise ValueError("statistic must be 'mean' or 'minimum'")
        selected = points.filter(pl.col("run_id").is_in(run_ids))
        kinds = selected.get_column("kind").unique().to_list() if not selected.is_empty() else []
        if len(kinds) > 1:
            raise ValueError("Choose bandwidth sweeps or frequency sweeps, not both")
        kind = kinds[0] if kinds else "bandwidth"
        y_column = "relative_noise_mean_db" if statistic == "mean" else "relative_noise_min_db"
        if ax is None:
            _, ax = plt.subplots(figsize=(8.5, 4.5), layout="constrained")
        for run_id in run_ids:
            subset = selected.filter(pl.col("run_id") == run_id).sort("point_index")
            xs = subset.get_column("axis_value").to_list()
            ys = subset.get_column(y_column).to_list()
            pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if x is not None and y is not None]
            if not pairs:
                continue
            x_values = [pair[0] for pair in pairs]
            y_values = [pair[1] for pair in pairs]
            if kind == "frequency":
                center = float(np.median(x_values))
                x_values = [
                    (value - center) * 1e6 if frequency_offset_mhz else value for value in x_values
                ]
            else:
                x_values = [value / 1e3 for value in x_values]
            ax.plot(x_values, y_values, marker="o", markersize=3.5, label=run_id)
        xlabel = (
            "Frequency offset (MHz)"
            if kind == "frequency" and frequency_offset_mhz
            else "Frequency (THz)"
            if kind == "frequency"
            else "Resolution bandwidth (kHz)"
        )
        _styled_axes(
            ax,
            title=f"Sweep comparison · {statistic} relative noise",
            xlabel=xlabel,
            ylabel="Squeezing - shot noise (dB)",
        )
        ax.axhline(0, linestyle="--", linewidth=1, alpha=0.75)
        if any(line.get_label() in run_ids for line in ax.lines):
            ax.legend(frameon=False, fontsize="small")
        return ax.figure, ax

    files = (
        sorted(DATA_ROOT.rglob("*.npz"), key=lambda path: path.stat().st_mtime, reverse=True)
        if DATA_ROOT.is_dir()
        else []
    )
    catalog = build_catalog(files)
    return (
        DATA_ROOT,
        catalog,
        load_run,
        plot_scope_comparison,
        plot_sweep_comparison,
        read_manifest,
    )


@app.cell
def _(DATA_ROOT, catalog, mo):
    mo.vstack(
        [
            mo.md(f"## Run catalog\n**Data root:** {DATA_ROOT}"),
            mo.ui.table(catalog.sort("modified_at", descending=True), selection=None, page_size=20),
        ]
    )
    return


@app.cell
def _(Path, catalog, load_run, pl, read_manifest, summarize_sweep):
    sweep_rows = []
    scope_rows = []
    read_errors = []

    for run in catalog.filter(pl.col("kind").is_in(["bandwidth", "frequency"])).iter_rows(
        named=True
    ):
        run_id = run["run_id"]
        try:
            recording = load_run(run_id)
            mean_summary = summarize_sweep(recording, "mean")
            min_summary = summarize_sweep(recording, "minimum")
            for mean_point, min_point in zip(mean_summary.points, min_summary.points, strict=True):
                measured = mean_point.measured
                sweep_rows.append(
                    {
                        "run_id": run_id,
                        "path": run["path"],
                        "kind": mean_summary.axis,
                        "point_index": mean_point.index,
                        "label": mean_point.label,
                        "requested": mean_point.requested,
                        "x_unit": mean_summary.x_unit,
                        "measured_frequency_thz": measured,
                        "axis_value": measured if measured is not None else mean_point.requested,
                        "relative_noise_mean_db": mean_point.relative_noise_db,
                        "relative_noise_min_db": min_point.relative_noise_db,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - isolate a broken recording and keep the rest usable
            read_errors.append({"run_id": run_id, "stage": "sweep summary", "error": str(exc)})

    for run in catalog.filter(pl.col("kind") == "scope").iter_rows(named=True):
        manifest = read_manifest(Path(run["path"]))
        settings_by_channel = {
            str(item.get("channel")): item
            for item in (
                (manifest.get("configuration") or {}).get("applied_channel_settings") or []
            )
            if isinstance(item, dict)
        }
        for waveform in manifest.get("waveforms") or []:
            if not isinstance(waveform, dict):
                continue
            channel = str(waveform.get("channel", "?"))
            stats = waveform.get("stats") or {}
            settings = settings_by_channel.get(channel, {})
            scope_rows.append(
                {
                    "run_id": run["run_id"],
                    "path": run["path"],
                    "modified_at": run["modified_at"],
                    "channel": channel,
                    "instrument": run["instrument"],
                    "address": run["address"],
                    "value_unit": waveform.get("value_unit"),
                    "time_unit": waveform.get("time_unit"),
                    "samples": stats.get("sample_count"),
                    "minimum": stats.get("min"),
                    "maximum": stats.get("max"),
                    "peak_to_peak": stats.get("peak_to_peak"),
                    "mean": stats.get("mean"),
                    "rms": stats.get("rms"),
                    "stddev": stats.get("stddev"),
                    "time_offset": waveform.get("time_offset"),
                    "time_interval": waveform.get("time_interval"),
                    "volts_per_div": settings.get("volts_per_div"),
                    "coupling": settings.get("coupling"),
                    "frozen": run["frozen"],
                    "warnings": run["warnings"],
                }
            )

    sweep_schema = {
        "run_id": pl.String,
        "path": pl.String,
        "kind": pl.String,
        "point_index": pl.Int64,
        "label": pl.String,
        "requested": pl.Float64,
        "x_unit": pl.String,
        "measured_frequency_thz": pl.Float64,
        "axis_value": pl.Float64,
        "relative_noise_mean_db": pl.Float64,
        "relative_noise_min_db": pl.Float64,
    }
    scope_schema = {
        "run_id": pl.String,
        "path": pl.String,
        "modified_at": pl.String,
        "channel": pl.String,
        "instrument": pl.String,
        "address": pl.String,
        "value_unit": pl.String,
        "time_unit": pl.String,
        "samples": pl.Int64,
        "minimum": pl.Float64,
        "maximum": pl.Float64,
        "peak_to_peak": pl.Float64,
        "mean": pl.Float64,
        "rms": pl.Float64,
        "stddev": pl.Float64,
        "time_offset": pl.Float64,
        "time_interval": pl.Float64,
        "volts_per_div": pl.Float64,
        "coupling": pl.String,
        "frozen": pl.Boolean,
        "warnings": pl.Int64,
    }
    sweep_points = pl.DataFrame(sweep_rows, schema=sweep_schema)
    scope_channels = pl.DataFrame(scope_rows, schema=scope_schema)
    sweep_run_schema = {
        "run_id": pl.String,
        "path": pl.String,
        "kind": pl.String,
        "n_points": pl.Int64,
        "n_usable": pl.Int64,
        "best_mean_relative_noise_db": pl.Float64,
        "average_mean_relative_noise_db": pl.Float64,
        "best_min_relative_noise_db": pl.Float64,
        "axis_min": pl.Float64,
        "axis_max": pl.Float64,
        "modified_at": pl.String,
        "status": pl.String,
    }
    sweep_runs = (
        sweep_points.group_by("run_id")
        .agg(
            pl.col("path").first(),
            pl.col("kind").first(),
            pl.len().cast(pl.Int64).alias("n_points"),
            pl.col("relative_noise_mean_db").count().cast(pl.Int64).alias("n_usable"),
            pl.col("relative_noise_mean_db").min().alias("best_mean_relative_noise_db"),
            pl.col("relative_noise_mean_db").mean().alias("average_mean_relative_noise_db"),
            pl.col("relative_noise_min_db").min().alias("best_min_relative_noise_db"),
            pl.col("axis_value").min().alias("axis_min"),
            pl.col("axis_value").max().alias("axis_max"),
        )
        .join(catalog.select("run_id", "modified_at", "status"), on="run_id", how="left")
        .sort("modified_at", descending=True)
        if not sweep_points.is_empty()
        else pl.DataFrame(schema=sweep_run_schema)
    )
    errors = pl.DataFrame(
        read_errors,
        schema={"run_id": pl.String, "stage": pl.String, "error": pl.String},
    )
    return errors, scope_channels, sweep_points, sweep_runs


@app.cell
def _(errors, mo, scope_channels, sweep_points, sweep_runs):
    mo.vstack(
        [
            mo.md("## Sweep run summary\nOne row per run for quick comparisons."),
            mo.ui.table(sweep_runs, selection=None, page_size=20),
            mo.md(
                "## Sweep points\nOne row per point; requested and measured setpoints stay separate."
            ),
            mo.ui.table(
                sweep_points.sort(["run_id", "point_index"]),
                selection=None,
                page_size=20,
            ),
            mo.md(
                "## Scope channel summaries\nOne row per run and channel; values come from the saved manifest."
            ),
            mo.ui.table(
                scope_channels.sort(["modified_at", "channel"], descending=[True, False]),
                selection=None,
                page_size=20,
            ),
            mo.md("## Read problems"),
            mo.ui.table(errors, selection=None, page_size=10)
            if not errors.is_empty()
            else mo.md("No sweep files failed to load."),
        ]
    )
    return


@app.cell
def _(catalog, mo, pl):
    sweep_ids = (
        catalog.filter(pl.col("kind").is_in(["bandwidth", "frequency"]))
        .get_column("run_id")
        .to_list()
    )
    scope_ids = catalog.filter(pl.col("kind") == "scope").get_column("run_id").to_list()
    selected_sweeps = mo.ui.multiselect(
        options=sweep_ids,
        value=sweep_ids[:5],
        label="Sweep runs to compare (select only one sweep kind)",
    )
    selected_statistic = mo.ui.dropdown(
        options=["mean", "minimum"],
        value="mean",
        label="Relative-noise statistic",
    )
    selected_scope_runs = mo.ui.multiselect(
        options=scope_ids,
        value=scope_ids[:3],
        label="Scope runs to compare",
    )
    return selected_scope_runs, selected_statistic, selected_sweeps


@app.cell
def _(
    mo,
    pl,
    scope_channels,
    selected_scope_runs,
    selected_statistic,
    selected_sweeps,
):
    channels_for_runs = (
        scope_channels.filter(pl.col("run_id").is_in(selected_scope_runs.value))
        .group_by("channel")
        .agg(pl.col("run_id").n_unique().alias("n_runs"))
        .filter(pl.col("n_runs") == len(selected_scope_runs.value))
        .sort("channel")
        .get_column("channel")
        .to_list()
        if selected_scope_runs.value
        else []
    )
    selected_scope_channel = mo.ui.dropdown(
        options=channels_for_runs,
        value=channels_for_runs[0] if channels_for_runs else None,
        label="Common scope channel",
    )
    mo.vstack(
        [
            mo.md("## Compare runs"),
            mo.hstack([selected_sweeps, selected_statistic], gap=2, align="start"),
            mo.hstack([selected_scope_runs, selected_scope_channel], gap=2, align="start"),
        ]
    )
    return (selected_scope_channel,)


@app.cell
def _():
    return


@app.cell
def _(
    mo,
    plot_scope_comparison,
    plot_sweep_comparison,
    selected_scope_channel,
    selected_scope_runs,
    selected_statistic,
    selected_sweeps,
    sweep_points,
):
    plots = []
    if selected_sweeps.value:
        try:
            sweep_fig, _ = plot_sweep_comparison(
                sweep_points,
                list(selected_sweeps.value),
                statistic=selected_statistic.value,
            )
            plots.append(sweep_fig)
        except ValueError as exc:
            plots.append(mo.md(f"**Sweep plot:** {exc}").callout(kind="warn"))
    else:
        plots.append(mo.md("Select one or more sweep runs to compare."))

    if selected_scope_runs.value and selected_scope_channel.value:
        try:
            scope_fig, _ = plot_scope_comparison(
                list(selected_scope_runs.value),
                channel=selected_scope_channel.value,
            )
            plots.append(scope_fig)
        except (ValueError, OSError, KeyError) as exc:
            plots.append(mo.md(f"**Scope plot:** {exc}").callout(kind="warn"))
    else:
        plots.append(mo.md("Select scope runs and a common channel to overlay."))

    mo.vstack([mo.md("## Plots"), *plots])
    return


@app.cell
def _(mo):
    mo.md("""
    ## Handy patterns

    The frames are ordinary Polars dataframes; use filter, select, group_by, and
    sort before converting a small result to Python rows with iter_rows(named=True).
    """)

    mo.md("""
    ~~~python
    # All sweep points below shot noise, sorted from strongest to weakest.
    below_shot_noise = (
        sweep_points
        .filter(pl.col("relative_noise_mean_db") < 0)
        .sort("relative_noise_mean_db")
    )

    # The best mean point for each run.
    best_point_per_run = (
        sweep_points
        .filter(pl.col("relative_noise_mean_db").is_not_null())
        .sort("relative_noise_mean_db")
        .group_by("run_id", maintain_order=True)
        .first()
    )

    # Small loops remain natural; arrays stay on the recording object.
    for run_id in selected_sweeps.value:
        recording = load_run(run_id)
        print(run_id, recording.arrays.keys())

    # Compose the sweep helper into your own figure.
    fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    plot_sweep_comparison(sweep_points, selected_sweeps.value, ax=ax)

    # Compare one channel across several saved scope runs.
    fig_scope, ax_scope = plt.subplots(figsize=(10, 4), layout="constrained")
    plot_scope_comparison(selected_scope_runs.value, channel="C1", ax=ax_scope)

    # Or plot a single channel from any loaded recording.
    recording = load_run(selected_scope_runs.value[0])
    trace = traces_from_scope_recording(recording)[0]
    plot_trace(trace)
    ~~~
    """)
    return


@app.cell
def _(np):
    fpath = "/home/gsec/sqz/repos/iyzee/data/2026-10/16T194451_frequency-manual_5cf579.npz"
    print(dir(np))
    return (fpath,)


@app.cell
def _(np):
    lst = dir(np)
    for l in lst:
        if "read" not in l:
            print(l)
    return


@app.cell
def _(fpath, np):
    data = np.load(fpath)
    return (data,)


@app.cell
def _(data):
    data.keys()
    return


@app.cell
def _(data):
    data["trace_squeezing"]
    data["trace_shot_noise"]
    it = list(data.items())
    print(it)
    return


@app.cell
def _(data):
    zd = list(zip(data["trace_squeezing"], data["trace_shot_noise"], strict=False))

    # plt.plot(accu[0][0])
    print(zd)
    return (zd,)


@app.cell
def _(plt, zd):
    _fig, ax = plt.subplots(figsize=(10, 4), layout="constrained")
    for sqz, _shn in zd:
        # ax.plot(sqz)
        # ax.plot(shn)
        ax.plot(sqz)
    return


@app.cell
def _():
    # fig.plot()
    return


@app.cell
def _(plt, zd):
    for r in zd:
        for tr in r:
            plt.plot(tr)
    plt.show()
    return


@app.cell
def _(plt, zd):

    plt.plot(zd[1][0])
    plt.plot(zd[1][1])
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
