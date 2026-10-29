"""Plot tidy workbench tables and export figures or CSV data."""
from __future__ import annotations

import math
from collections.abc import Mapping
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


def _chart_selection(
    frame: pl.DataFrame,
    *,
    x: str,
    y: str,
    color: str | None,
    detail: str | None,
    facet: str | None,
    band: tuple[str, str] | None,
) -> tuple[pl.DataFrame, list[str], list[Any]]:
    band_columns = list(band) if band else []
    requested = (x, y, color, detail, facet, *band_columns)
    for column in requested:
        if column is not None and column not in frame.columns:
            raise ValueError(f"column {column!r} is not in the selected table")

    columns = list(dict.fromkeys(c for c in requested if c is not None))
    data = frame.select(columns).drop_nulls([x, y]).sort(x)
    if data.is_empty():
        raise ValueError("no usable rows for these plot fields")

    groups = list(dict.fromkeys(c for c in (color, detail) if c is not None and c not in {x, y}))
    panels = data[facet].unique(maintain_order=True).to_list() if facet else [None]
    return data, groups, panels


def _chart_styles(
    data: pl.DataFrame,
    groups: list[str],
    color: str | None,
    detail: str | None,
    palette: Mapping[str, str] | None,
) -> tuple[dict[Any, str], dict[Any, str]]:
    hues: dict[Any, str] = {}
    styles: dict[Any, str] = {}
    for record in data.select(groups).iter_rows() if groups else []:
        key = dict(zip(groups, record, strict=True))
        if color in key and key[color] not in hues:
            fallback = PALETTE[len(hues) % len(PALETTE)]
            hues[key[color]] = (palette or {}).get(key[color], fallback)
        if detail in key and key[detail] not in styles:
            styles[key[detail]] = LINESTYLES[len(styles) % len(LINESTYLES)]
    return hues, styles


def _chart_buckets(
    data: pl.DataFrame, groups: list[str]
) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in data.to_dicts():
        key = tuple(record[group] for group in groups)
        buckets.setdefault(key, []).append(record)
    return buckets


def _draw_series(
    slot: Any,
    *,
    key: tuple[Any, ...],
    records: list[dict[str, Any]],
    groups: list[str],
    color: str | None,
    detail: str | None,
    hues: Mapping[Any, str],
    styles: Mapping[Any, str],
    mark: str,
    x: str,
    y: str,
    band: tuple[str, str] | None,
    categories: list[Any],
    position: int,
    bucket_count: int,
    handles: dict[str, Any],
) -> None:
    named = dict(zip(groups, key, strict=True))
    label = " · ".join(str(value) for value in key) or None
    style = {"color": hues.get(named.get(color)) if color else None, "label": label}
    xs = [record[x] for record in records]
    ys = [record[y] for record in records]

    if mark == "points":
        artist = slot.scatter(xs, ys, s=26, alpha=0.85, **style)
    elif mark == "bars":
        width = 0.8 / bucket_count
        shifted = [
            categories.index(value) + (position - (bucket_count - 1) / 2) * width
            for value in xs
        ]
        artist = slot.bar(shifted, ys, width=width, alpha=0.85, **style)
    else:
        if band:
            shaded = [
                record
                for record in records
                if record[band[0]] is not None and record[band[1]] is not None
            ]
            if shaded:
                slot.fill_between(
                    [record[x] for record in shaded],
                    [record[band[0]] for record in shaded],
                    [record[band[1]] for record in shaded],
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
            linestyle=styles.get(named[detail], "-") if detail is not None and detail in named else "-",
            **style,
        )
    if label and label not in handles:
        handles[label] = artist


def _draw_panel(
    slot: Any,
    data: pl.DataFrame,
    *,
    groups: list[str],
    color: str | None,
    detail: str | None,
    hues: Mapping[Any, str],
    styles: Mapping[Any, str],
    mark: str,
    x: str,
    y: str,
    band: tuple[str, str] | None,
    categories: list[Any],
    handles: dict[str, Any],
) -> None:
    buckets = _chart_buckets(data, groups)
    for position, (key, records) in enumerate(buckets.items()):
        _draw_series(
            slot,
            key=key,
            records=records,
            groups=groups,
            color=color,
            detail=detail,
            hues=hues,
            styles=styles,
            mark=mark,
            x=x,
            y=y,
            band=band,
            categories=categories,
            position=position,
            bucket_count=len(buckets),
            handles=handles,
        )


def _format_axis(
    slot: Any,
    *,
    x: str,
    y: str,
    facet: str | None,
    panel: Any,
    x_log: bool,
    y_log: bool,
) -> None:
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


def _finish_chart(
    figure: Any,
    axes: Any,
    handles: dict[str, Any],
    *,
    title: str,
    x: str,
    y: str,
    facet: str | None,
) -> None:
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

    Use columns instead of maintaining one-off plots: color identifies a series, detail changes
    its line style, facet creates panels, and bands shades between two columns. The figure is
    not registered with pyplot and can be exported with :func:`figure_bytes`.
    """
    from matplotlib.figure import Figure

    data, groups, panels = _chart_selection(
        frame, x=x, y=y, color=color, detail=detail, facet=facet, band=band
    )
    ncols = min(len(panels), 3)
    nrows = math.ceil(len(panels) / ncols)
    hues, styles = _chart_styles(data, groups, color, detail, palette)
    categories = list(dict.fromkeys(data[x].to_list())) if mark == "bars" else []

    figure = Figure(figsize=(6.2 * ncols + 3.2, 4.2 * nrows + 0.6), layout="constrained")
    axes = figure.subplots(nrows, ncols, squeeze=False, sharex=True)
    handles: dict[str, Any] = {}

    for slot, panel in zip(axes.flat, panels, strict=False):
        part = data.filter(pl.col(facet) == panel) if facet else data
        _draw_panel(
            slot,
            part,
            groups=groups,
            color=color,
            detail=detail,
            hues=hues,
            styles=styles,
            mark=mark,
            x=x,
            y=y,
            band=band,
            categories=categories,
            handles=handles,
        )
        _format_axis(slot, x=x, y=y, facet=facet, panel=panel, x_log=x_log, y_log=y_log)
    for slot in list(axes.flat)[len(panels) :]:
        slot.set_visible(False)

    _finish_chart(figure, axes, handles, title=title, x=x, y=y, facet=facet)
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
