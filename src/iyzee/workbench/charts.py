"""Plot tidy workbench tables and export figures or CSV data."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import polars as pl

from .catalog import LINESTYLES, PALETTE
from .groups import _title

def _tick_text(value: Any) -> str:
    return (
        f"{value:g}"
        if isinstance(value, int | float) and not isinstance(value, bool)
        else str(value)
    )

def field_chart(
    frame: pl.DataFrame,
    *,
    x: str,
    y: str,
    color: str | None = "run",
    detail: str | None = None,
    facet: str | None = None,
    mark: str = "line",
    title: str = "",
    palette: Mapping[str, str] | None = None,
    x_log: bool = False,
    y_log: bool = False,
    band: tuple[str, str] | None = None,
) -> Any:
    """Build a reusable Matplotlib figure from any tidy Polars table.

    Choose columns instead of maintaining a one-off plot per experiment:

    * ``color`` and ``detail`` split the rows into series. ``color`` picks the hue (looked up
      in ``palette`` when the value is there, so a run keeps its colour across figures);
      ``detail`` varies the line style, e.g. run = colour, channel = dashes.
    * ``facet`` draws one panel per value, with a shared x axis.
    * ``mark`` is ``"line"``, ``"points"`` or ``"bars"`` (grouped side by side per x value).
    * ``band=("low_col", "high_col")`` shades between two columns behind each line (a
      spread or uncertainty band around a mean).

    The figure is not registered with pyplot, so repeated calls do not accumulate; save it
    with :func:`figure_bytes` or ``figure.savefig``.
    """
    from matplotlib.figure import Figure

    band_columns = list(band) if band else []
    for column in (x, y, color, detail, facet, *band_columns):
        if column is not None and column not in frame.columns:
            raise ValueError(f"column {column!r} is not in the selected table")
    columns = list(
        dict.fromkeys(c for c in (x, y, color, detail, facet, *band_columns) if c is not None)
    )
    data = frame.select(columns).drop_nulls([x, y]).sort(x)
    if data.is_empty():
        raise ValueError("no usable rows for these plot fields")

    groups = list(dict.fromkeys(c for c in (color, detail) if c is not None and c not in {x, y}))
    panels = data[facet].unique(maintain_order=True).to_list() if facet else [None]
    ncols = min(len(panels), 3)
    nrows = math.ceil(len(panels) / ncols)

    # Colours are fixed per colour-value for the whole figure, whatever the panel.
    hues: dict[Any, str] = {}
    styles: dict[Any, str] = {}
    for record in data.select(groups).iter_rows() if groups else []:
        key = dict(zip(groups, record, strict=True))
        if color in key and key[color] not in hues:
            fallback = PALETTE[len(hues) % len(PALETTE)]
            hues[key[color]] = (palette or {}).get(key[color], fallback)
        if detail in key and key[detail] not in styles:
            styles[key[detail]] = LINESTYLES[len(styles) % len(LINESTYLES)]

    categories = list(dict.fromkeys(data[x].to_list())) if mark == "bars" else []
    figure = Figure(figsize=(6.2 * ncols + 3.2, 4.2 * nrows + 0.6), layout="constrained")
    axes = figure.subplots(nrows, ncols, squeeze=False, sharex=True)
    handles: dict[str, Any] = {}

    for slot, panel in zip(axes.flat, panels, strict=False):
        part = data.filter(pl.col(facet) == panel) if facet else data
        buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for record in part.to_dicts():
            buckets.setdefault(tuple(record[g] for g in groups), []).append(record)
        for position, (key, records) in enumerate(buckets.items()):
            named = dict(zip(groups, key, strict=True))
            label = " · ".join(str(v) for v in key) or None
            style = {
                "color": hues.get(named.get(color)) if color else None,
                "label": label,
            }
            xs = [r[x] for r in records]
            ys = [r[y] for r in records]
            if mark == "points":
                artist = slot.scatter(xs, ys, s=26, alpha=0.85, **style)
            elif mark == "bars":
                width = 0.8 / len(buckets)
                shifted = [
                    categories.index(v) + (position - (len(buckets) - 1) / 2) * width for v in xs
                ]
                artist = slot.bar(shifted, ys, width=width, alpha=0.85, **style)
            else:
                if band:
                    shaded = [
                        r for r in records if r[band[0]] is not None and r[band[1]] is not None
                    ]
                    if shaded:
                        slot.fill_between(
                            [r[x] for r in shaded],
                            [r[band[0]] for r in shaded],
                            [r[band[1]] for r in shaded],
                            color=style["color"],
                            alpha=0.18,
                            linewidth=0,
                        )
                (artist,) = slot.plot(
                    xs,
                    ys,
                    marker="o" if len(xs) <= 80 else None,
                    markersize=3.5,
                    linewidth=1.5,
                    linestyle=styles.get(named.get(detail), "-"),
                    **style,
                )
            if label and label not in handles:
                handles[label] = artist
        if mark == "bars":
            slot.set_xticks(range(len(categories)), [_tick_text(c) for c in categories])
        if x_log:
            slot.set_xscale("log")
        if y_log:
            slot.set_yscale("log")
        if y.startswith(("rel_noise", "delta")):
            slot.axhline(0, linestyle="--", linewidth=1, alpha=0.7, color="black")
        slot.grid(True, which="major", alpha=0.25)
        slot.spines[["top", "right"]].set_visible(False)
        slot.set_xlabel(_title(x))
        slot.set_ylabel(_title(y))
        if facet:
            slot.set_title(f"{facet}: {panel}", loc="left", fontsize="small")
    for slot in list(axes.flat)[len(panels) :]:
        slot.set_visible(False)

    heading = title or f"{_title(y)} by {_title(x)}"
    if facet:
        figure.suptitle(heading, x=0.01, ha="left", fontsize="large")
    else:
        axes[0, 0].set_title(heading, loc="left", pad=10)
    if handles:
        figure.legend(
            list(handles.values()),
            list(handles),
            loc="outside right upper",
            frameon=False,
            fontsize="small",
        )
    return figure

def figure_bytes(figure: Any, fmt: str = "png", dpi: int = 200) -> bytes:
    """Serialise a figure (``png``, ``svg`` or ``pdf``) for a download button or a file."""
    import io

    if fmt not in {"png", "svg", "pdf"}:
        raise ValueError(f"unsupported figure format: {fmt!r}")
    buffer = io.BytesIO()
    figure.savefig(buffer, format=fmt, dpi=dpi)
    return buffer.getvalue()

def csv_bytes(frame: pl.DataFrame) -> bytes:
    """CSV bytes suitable for a marimo download control."""
    return frame.write_csv().encode("utf-8")
