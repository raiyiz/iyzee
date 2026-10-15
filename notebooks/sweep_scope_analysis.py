"""Explore saved iyzee sweeps and LeCroy scope traces with marimo.

From the iyzee checkout:
    uv run --with marimo marimo edit notebooks/sweep_scope_analysis.py

Read-only: this notebook opens saved recordings and never connects to hardware.
"""

import marimo

__generated_with = "0.19.7"
app = marimo.App(width="medium")


@app.cell
def _():
    import hashlib
    from pathlib import Path

    import matplotlib.pyplot as plt
    import marimo as mo
    import numpy as np

    from iyzee.analysis import summarize_sweep
    from iyzee.config import data_root
    from iyzee.experiment import load_recording
    from iyzee.waveform_math import traces_from_scope_recording

    return (
        Path,
        data_root,
        hashlib,
        load_recording,
        mo,
        np,
        plt,
        summarize_sweep,
        traces_from_scope_recording,
    )


@app.cell
def _(mo):
    mo.md(
        """
        # iyzee · Measurement explorer

        A small, read-only workbench for **saved sweeps** and **scope waveforms**.
        Pick a recording, check its manifest, then inspect the data with iyzee's
        existing analysis routines.

        - **Sweeps:** relative noise (squeezing minus shot noise), selected trace pairs,
          requested and measured frequency.
        - **Scope:** waveform plot, RMS / peak-to-peak / spread, acquisition warnings.

        Negative relative noise means squeezing is below the shot-noise reference.
        This notebook reads saved files only; it does not talk to laboratory hardware.
        """
    )
    return


@app.cell
def _(Path, data_root, mo):
    root = data_root()
    browser_root = root if root.is_dir() else Path.cwd()
    files = (
        sorted(
            root.rglob("*.npz"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if root.is_dir()
        else []
    )
    browser = mo.ui.file_browser(
        initial_path=browser_root,
        filetypes=[".npz"],
        selection_mode="file",
        multiple=False,
        value=files[0] if files else None,
        label="Recording (.npz; the matching .json manifest is loaded automatically)",
    )
    mo.vstack([mo.md(f"**Data root:** {root}"), browser])
    return (browser,)


@app.cell
def _(hashlib):
    def sha256(path):
        """Hash the recording incrementally without copying it all into RAM."""
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    return (sha256,)


@app.cell
def _(browser, load_recording, mo, sha256):
    path = browser.path(index=0) if browser.value else None
    recording = load_recording(path) if path is not None else None
    arrays = recording.arrays if recording is not None else {}
    manifest = recording.metadata if recording is not None else {}
    kind = str(manifest.get("kind", "unknown"))
    digest = manifest.get("data_sha256")
    if not digest:
        checksum = "not recorded"
    elif sha256(path) == digest:
        checksum = "matches manifest"
    else:
        checksum = "MISMATCH"

    run = manifest.get("run_metadata") or {}
    instrument = manifest.get("instrument") or {}
    acquisition = manifest.get("acquisition") or {}
    warnings = acquisition.get("warnings") or []

    if recording is None:
        overview = mo.md(
            "Choose a saved .npz recording above. The newest file under iyzee's "
            "configured data root is preselected when available."
        ).callout(kind="info")
    else:
        lines = [
            f"**File:** {path.name}",
            f"**Kind:** {kind}",
            f"**Checksum:** {checksum}",
        ]
        if kind == "scope-acquisition":
            lines.extend(
                [
                    f"**Instrument:** {instrument.get('identity') or 'identity not recorded'}",
                    f"**Frozen acquisition:** {acquisition.get('frozen', 'not recorded')}",
                ]
            )
            if warnings:
                lines.append("**Warnings:**\n" + "\n".join(f"- {w}" for w in warnings))
        else:
            lines.extend(
                [
                    f"**Run status:** {run.get('status', 'not recorded')}",
                    f"**Points:** {len(arrays.get('x_values', []))}",
                ]
            )
            failed = run.get("failed_steps") or []
            if failed:
                lines.append(f"**Failed steps:** {len(failed)}")
        overview = mo.md("\n\n".join(lines)).callout(
            kind="danger" if checksum == "MISMATCH" else "success"
        )
    overview
    return arrays, kind, manifest, path, recording


@app.cell
def _(arrays, kind, mo, recording, traces_from_scope_recording):
    count = (
        len(arrays.get("x_values", []))
        if recording is not None and kind != "scope-acquisition"
        else 0
    )
    traces = (
        traces_from_scope_recording(recording)
        if recording is not None and kind == "scope-acquisition"
        else []
    )
    statistic = mo.ui.dropdown(
        options={
            "Mean relative noise": "mean",
            "Minimum relative noise": "minimum",
        },
        value="mean",
        label="Sweep statistic",
    )
    point = mo.ui.slider(
        start=0,
        stop=max(count - 1, 0),
        step=1,
        value=0,
        disabled=count < 2,
        label="Sweep point (zero-based index)",
    )
    channels = {trace.label: trace.label for trace in traces} or {
        "No scope channels": ""
    }
    channel = mo.ui.dropdown(
        options=channels,
        value=next(iter(channels.values())),
        label="Scope channel",
    )
    if kind in ("bandwidth", "frequency"):
        controls = mo.vstack([statistic, point])
    elif kind == "scope-acquisition":
        controls = channel
    else:
        controls = mo.md(
            "Select a sweep or scope recording to display analysis controls."
        )
    controls
    return channel, point, statistic, traces


@app.cell
def _(
    arrays,
    channel,
    kind,
    manifest,
    mo,
    np,
    plt,
    point,
    recording,
    statistic,
    summarize_sweep,
    traces,
):
    if recording is None:
        analysis = mo.md("## Analysis\nSelect a recording to get started.")
    elif kind in ("bandwidth", "frequency"):
        summary = summarize_sweep(recording, statistic.value)
        points = summary.points
        usable = [p for p in points if p.relative_noise_db is not None]

        if summary.axis == "bandwidth":
            x = [p.requested / 1e3 for p in usable]
            xlabel = "Resolution bandwidth (kHz)"
        else:
            frequencies = [
                p.measured if p.measured is not None else p.requested for p in points
            ]
            reference = float(np.median(frequencies)) if frequencies else 0.0
            x = [
                (
                    (p.measured if p.measured is not None else p.requested)
                    - reference
                )
                * 1e6
                for p in usable
            ]
            xlabel = "Frequency offset from median scan frequency (MHz)"

        fig, ax = plt.subplots(figsize=(9, 4), constrained_layout=True)
        if usable:
            ax.plot(x, [p.relative_noise_db for p in usable], marker="o")
            ax.axhline(0, linestyle="--", linewidth=1)
            ax.set_xlabel(xlabel)
            ax.set_ylabel(f"{statistic.value.title()} relative noise (dB)")
            ax.grid(True, alpha=0.25)
        else:
            ax.text(
                0.5,
                0.5,
                "No finite trace pairs in this recording",
                ha="center",
                va="center",
            )
            ax.set_axis_off()
        ax.set_title("Sweep response - negative is below shot noise")

        rows = [
            {
                "point": p.index,
                "label": p.label,
                f"requested ({summary.x_unit})": p.requested,
                "measured frequency (THz)": p.measured,
                f"relative noise ({statistic.value}, dB)": p.relative_noise_db,
            }
            for p in points
        ]
        table = mo.ui.table(rows, selection=None, page_size=12)

        i = int(point.value)
        squeezing_rows = arrays.get("trace_squeezing")
        shot_rows = arrays.get("trace_shot_noise")
        trace_fig, (a, b) = plt.subplots(
            2,
            1,
            figsize=(9, 5.5),
            sharex=True,
            constrained_layout=True,
        )
        has_pair = (
            squeezing_rows is not None
            and shot_rows is not None
            and i < len(squeezing_rows)
            and i < len(shot_rows)
            and np.shape(squeezing_rows[i]) == np.shape(shot_rows[i])
            and np.isfinite(squeezing_rows[i]).any()
            and np.isfinite(shot_rows[i]).any()
        )
        if has_pair:
            sq = np.asarray(squeezing_rows[i], float)
            shot = np.asarray(shot_rows[i], float)
            samples = np.arange(sq.size)
            a.plot(samples, sq, label="Squeezing")
            a.plot(samples, shot, label="Shot noise")
            a.set_ylabel("Power (dBm)")
            a.legend(frameon=False, ncols=2)
            a.set_title(f"Point {i}: analyzer traces")
            b.plot(samples, sq - shot)
            b.axhline(0, linestyle="--", linewidth=1)
            b.set_ylabel("Difference (dB)")
            b.set_xlabel("Trace sample")
        else:
            a.text(
                0.5,
                0.5,
                "No usable trace pair at this point",
                ha="center",
                va="center",
            )
            a.set_axis_off()
            b.set_axis_off()
        for axis in (a, b):
            axis.grid(True, alpha=0.2)

        analysis = mo.vstack(
            [
                mo.md(
                    "## Sweep analysis\n"
                    "The upper plot reduces each trace pair to one value; "
                    "choose mean or minimum above."
                ),
                fig,
                table,
                mo.md(
                    "### Inspect an individual point\n"
                    "Trace samples are plotted as saved; no smoothing or "
                    "resampling is applied."
                ),
                trace_fig,
            ]
        )
    elif kind == "scope-acquisition":
        trace = next((t for t in traces if t.label == channel.value), None)
        if trace is None:
            analysis = mo.md(
                "## Scope analysis\nNo usable channel arrays were found."
            )
        else:
            values = np.asarray(trace.values, float)
            times = np.asarray(trace.time, float)
            clean = values[np.isfinite(values)]
            fig, ax = plt.subplots(figsize=(9, 4), constrained_layout=True)
            ax.plot(times, values, linewidth=1)
            ax.set(
                xlabel=f"Time ({trace.time_unit})",
                ylabel=f"Voltage ({trace.value_unit})",
                title=f"Scope waveform - {trace.label}",
            )
            ax.grid(True, alpha=0.25)
            if clean.size:
                stats = [
                    {"metric": "Samples", "value": values.size, "unit": "samples"},
                    {
                        "metric": "Finite samples",
                        "value": clean.size,
                        "unit": "samples",
                    },
                    {
                        "metric": "Minimum",
                        "value": float(np.min(clean)),
                        "unit": trace.value_unit,
                    },
                    {
                        "metric": "Maximum",
                        "value": float(np.max(clean)),
                        "unit": trace.value_unit,
                    },
                    {
                        "metric": "Peak-to-peak",
                        "value": float(np.ptp(clean)),
                        "unit": trace.value_unit,
                    },
                    {
                        "metric": "Mean",
                        "value": float(np.mean(clean)),
                        "unit": trace.value_unit,
                    },
                    {
                        "metric": "RMS (includes DC offset)",
                        "value": float(np.sqrt(np.mean(clean**2))),
                        "unit": trace.value_unit,
                    },
                    {
                        "metric": "Standard deviation",
                        "value": float(np.std(clean)),
                        "unit": trace.value_unit,
                    },
                ]
                meta = next(
                    (
                        waveform
                        for waveform in manifest.get("waveforms", [])
                        if isinstance(waveform, dict)
                        and waveform.get("channel") == trace.label
                    ),
                    {},
                )
                note = (
                    f"Time offset: {meta.get('time_offset', 'not recorded')} s; "
                    f"sample interval: {meta.get('time_interval', 'not recorded')} s"
                )
                analysis = mo.vstack(
                    [
                        mo.md(
                            "## Scope analysis\n"
                            "Statistics ignore non-finite samples; the waveform "
                            "plot shows recorded samples."
                        ),
                        fig,
                        mo.md(note),
                        mo.ui.table(stats, selection=None, page_size=10),
                    ]
                )
            else:
                analysis = mo.vstack(
                    [
                        fig,
                        mo.md(
                            "No finite samples are available for statistics."
                        ).callout(kind="warn"),
                    ]
                )
    else:
        analysis = mo.md(
            f"## Analysis unavailable\nManifest kind {kind!r} is not a sweep or scope recording."
        ).callout(kind="warn")
    analysis
    return


if __name__ == "__main__":
    app.run()
