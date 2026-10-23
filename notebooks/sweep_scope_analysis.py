"""Code-first companion to the iyzee workbench.

``workbench.py`` is the point-and-click front end. This notebook shows the same analysis as
a few lines of Python on a ``Workset``: pick recordings by filter, ``#ref`` or name, and
every table and plot follows. Edit the ``ws = ...`` line and everything below updates.
It is read-only and never connects to instruments.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium", app_title="iyzee · analysis recipes")


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

    from workset import Workset

    return Workset, data_root, mo, pl


@app.cell
def _(mo):
    mo.md("""
    # iyzee · Analysis recipes

    Choose recordings in **one line**, then analyse. Run `scripts/demo_data.py` first
    if you have no recordings; synthetic runs are labelled and are not measurements.
    """)
    return


@app.cell
def _(Workset, data_root, mo):
    # Pick by filter ...     Workset.open(data_root(), kind="frequency", latest=3)
    # ... or by #ref / name: Workset.open(data_root(), ["#02", "#05", "scan-pumped"])
    ws = Workset.open(data_root(), latest=4)
    mo.vstack(
        [
            mo.Html(ws.banner()),
            mo.ui.table(ws.rows.select("ref", "kind", "started", "status"), selection=None),
        ]
    )
    return (ws,)


@app.cell
def _(mo, ws):
    mo.vstack(
        [
            mo.md("## Which sweep was best?"),
            mo.ui.table(ws.scoreboard(), selection=None)
            if not ws.sweeps.is_empty()
            else mo.md("_No sweeps in this set._"),
        ]
    )
    return


@app.cell
def _(mo, ws):
    # The standard comparison figures; one call.
    mo.vstack(
        [item for heading, fig in ws.quick_plots() for item in (mo.md(f"### {heading}"), fig)]
        or [mo.md("_Nothing to plot in this set._")]
    )
    return


@app.cell
def _(mo, pl, ws):
    # Tables are ordinary Polars frames; plots reuse the set's run colours.
    quiet = ws.points.filter(pl.col("rel_noise_mean_db") < 0)
    mo.vstack(
        [
            mo.md("## Your own question: points below shot noise"),
            ws.plot(quiet, x="point", y="rel_noise_mean_db", mark="points", facet="kind")
            if not quiet.is_empty()
            else mo.md("_No points below shot noise._"),
        ]
    )
    return


@app.cell
def _(mo, ws):
    mo.md("""
    ## Cheat sheet

    ~~~python
    ws.delta("#02")                  # every run minus run #02, on the shared x range
    ws.point_traces(3)               # raw squeezing / shot-noise traces of point 3
    ws.array("trace_squeezing", 3)   # a saved array, overlaid across runs
    ws.traces(["C1"], budget=2000)   # scope waveforms
    ws.subset(kind="frequency")      # a smaller set that keeps its colours
    g = ws.with_groups({"#02": "base", "#05": "pumped", "#07": "pumped"})  # or "name", "kind", ...
    g.plot_groups(band="sem")        # group means with a spread band
    g.group_diff("pumped", "base")   # difference, standard error, |t| > 2 flag
    WorksetStore().save("my set", g)               # saved sets keep the groups too
    Workset.from_saved(catalog, WorksetStore().get("my set"))
    ws.verify()                      # SHA-256 check against the manifests
    ws.png(fig)                      # figure bytes (or ws.png(fig, "svg"))
    ~~~
    """)
    return


if __name__ == "__main__":
    app.run()
