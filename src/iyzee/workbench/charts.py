"""Plots and exports for the workbench: the colour palette, ``field_chart``, PNG and CSV bytes."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

from iyzee.workbench.catalog import SWEEP_METRICS

# Colour-blind-safe (Okabe-Ito based) cycle. Plots give every run one colour from this list.
PALETTE = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#555555",
    "#B8A100",
)


LINESTYLES = ("-", "--", ":", "-.")


def _title(column: str) -> str:
    known = {
        **SWEEP_METRICS,
        "offset_mhz": "Laser frequency offset (MHz; shared reference)",
        "rbw_khz": "Resolution bandwidth (kHz)",
        "x_thz": "Measured laser frequency (THz)",
        "time": "Time (ms)",
        "sample": "Sample index",
        "delta_db": "Δ relative noise vs reference (dB)",
        "delta": "Difference between groups (A minus B)",
        "se": "Standard error",
        "group": "Group",
        "best_db": "Best relative noise (dB)",
        "best_x": "Position of best point",
        "mean_db": "Mean relative noise (dB)",
        "rel_noise_db": "Relative noise (dB)",
        "value": "Amplitude",
        "rms": "RMS amplitude",
        "minimum": "Minimum amplitude",
        "maximum": "Maximum amplitude",
        "stddev": "Standard deviation",
        "peak_to_peak": "Peak-to-peak amplitude",
    }
    return known.get(column, column.replace("_", " ").capitalize())


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
    faint_detail: str | None = None,
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
    * ``faint_detail`` draws that detail category thinner, translucent and behind others, useful
      for a reference trace paired with a measurement.

    The figure is not registered with pyplot, so repeated calls do not accumulate; save it
    with :func:`figure_bytes` or ``figure.savefig``.
    """
    from matplotlib.figure import Figure

    band_columns = list(band) if band else []
    data = _tidy(frame, (x, y, color, detail, facet, *band_columns))
    groups = list(dict.fromkeys(c for c in (color, detail) if c is not None and c not in {x, y}))
    hues, styles = _series_styles(data, groups, color, detail, palette)
    spec = _Spec(
        x=x,
        y=y,
        groups=groups,
        color=color,
        detail=detail,
        faint_detail=faint_detail,
        facet=facet,
        mark=mark,
        band=band,
        x_log=x_log,
        y_log=y_log,
        hues=hues,
        styles=styles,
        categories=list(dict.fromkeys(data[x].to_list())) if mark == "bars" else [],
    )

    panels = data[facet].unique(maintain_order=True).to_list() if facet else [None]
    ncols = min(len(panels), 3)
    nrows = math.ceil(len(panels) / ncols)
    figure = Figure(figsize=(6.2 * ncols + 3.2, 4.2 * nrows + 0.6), layout="constrained")
    axes = figure.subplots(nrows, ncols, squeeze=False, sharex=True)

    handles: dict[str, Any] = {}
    for slot, panel in zip(axes.flat, panels, strict=False):
        part = data.filter(pl.col(facet) == panel) if facet else data
        _draw_panel(slot, part, spec, handles)
        _decorate(slot, spec, panel)
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


@dataclass(frozen=True)
class _Spec:
    """What ``field_chart`` has resolved about one figure, shared by its drawing helpers."""

    x: str
    y: str
    groups: list[str]
    color: str | None
    detail: str | None
    faint_detail: str | None
    facet: str | None
    mark: str
    band: tuple[str, str] | None
    x_log: bool
    y_log: bool
    hues: dict[Any, str]
    styles: dict[Any, str]
    categories: list[Any]


def _tidy(frame: pl.DataFrame, wanted: Sequence[str | None]) -> pl.DataFrame:
    """The columns the chart needs, without rows missing x or y (sorted by x)."""
    x, y = wanted[0], wanted[1]
    for column in wanted:
        if column is not None and column not in frame.columns:
            raise ValueError(f"column {column!r} is not in the selected table")
    columns = list(dict.fromkeys(c for c in wanted if c is not None))
    data = frame.select(columns).drop_nulls([c for c in (x, y) if c is not None]).sort(str(x))
    if data.is_empty():
        raise ValueError("no usable rows for these plot fields")
    return data


def _series_styles(
    data: pl.DataFrame,
    groups: list[str],
    color: str | None,
    detail: str | None,
    palette: Mapping[str, str] | None,
) -> tuple[dict[Any, str], dict[Any, str]]:
    """A hue per colour value and a line style per detail value, fixed for the whole figure."""
    hues: dict[Any, str] = {}
    styles: dict[Any, str] = {}
    for combo in data.select(groups).iter_rows() if groups else []:
        values = dict(zip(groups, combo, strict=True))
        if color in values and values[color] not in hues:
            fallback = PALETTE[len(hues) % len(PALETTE)]
            hues[values[color]] = (palette or {}).get(values[color], fallback)
        if detail in values and values[detail] not in styles:
            styles[values[detail]] = LINESTYLES[len(styles) % len(LINESTYLES)]
    return hues, styles


def _draw_panel(slot: Any, part: pl.DataFrame, spec: _Spec, handles: dict[str, Any]) -> None:
    """Draw every series of one panel and remember one legend handle per label."""
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in part.to_dicts():
        buckets.setdefault(tuple(record[g] for g in spec.groups), []).append(record)
    for position, (key, records) in enumerate(buckets.items()):
        named = dict(zip(spec.groups, key, strict=True))
        label = " · ".join(str(v) for v in key) or None
        hue = spec.hues.get(named.get(spec.color)) if spec.color else None
        xs = [r[spec.x] for r in records]
        ys = [r[spec.y] for r in records]
        if spec.mark == "points":
            artist = slot.scatter(xs, ys, s=26, alpha=0.85, color=hue, label=label)
        elif spec.mark == "bars":
            artist = _draw_bars(slot, spec, xs, ys, position, len(buckets), hue, label)
        else:
            faint = (
                spec.faint_detail is not None
                and named.get(spec.detail) == spec.faint_detail
            )
            dashes = (
                "--"
                if faint
                else "-"
                if spec.faint_detail is not None
                else spec.styles.get(named.get(spec.detail) if spec.detail else None, "-")
            )
            artist = _draw_line(slot, spec, records, xs, ys, hue, label, dashes, faint)
        if label and label not in handles:
            handles[label] = artist
    if spec.mark == "bars":
        slot.set_xticks(range(len(spec.categories)), [_tick_text(c) for c in spec.categories])


def _draw_bars(
    slot: Any,
    spec: _Spec,
    xs: list[Any],
    ys: list[Any],
    position: int,
    count: int,
    hue: str | None,
    label: str | None,
) -> Any:
    """One series of grouped bars, side by side with the others at each x category."""
    width = 0.8 / count
    shifted = [spec.categories.index(v) + (position - (count - 1) / 2) * width for v in xs]
    return slot.bar(shifted, ys, width=width, alpha=0.85, color=hue, label=label)


def _draw_line(
    slot: Any,
    spec: _Spec,
    records: list[dict[str, Any]],
    xs: list[Any],
    ys: list[Any],
    hue: str | None,
    label: str | None,
    dashes: str,
    faint: bool = False,
) -> Any:
    """One line, with its shaded band behind it when the figure has one."""
    if spec.band:
        low, high = spec.band
        shaded = [r for r in records if r[low] is not None and r[high] is not None]
        if shaded:
            slot.fill_between(
                [r[spec.x] for r in shaded],
                [r[low] for r in shaded],
                [r[high] for r in shaded],
                color=hue,
                alpha=0.18,
                linewidth=0,
            )
    (artist,) = slot.plot(
        xs,
        ys,
        marker="o" if len(xs) <= 80 else None,
        markersize=3.5,
        linewidth=1.0 if faint else 1.5,
        linestyle=dashes,
        color=hue,
        alpha=0.32 if faint else None,
        zorder=1 if faint else 2,
        label=label,
    )
    return artist


def _decorate(slot: Any, spec: _Spec, panel: Any) -> None:
    """Scales, zero line, grid, spines and labels of one panel."""
    if spec.x_log:
        slot.set_xscale("log")
    if spec.y_log:
        slot.set_yscale("log")
    if spec.y.startswith(("rel_noise", "delta")):
        slot.axhline(0, linestyle="--", linewidth=1, alpha=0.7, color="black")
    slot.grid(True, which="major", alpha=0.25)
    slot.spines[["top", "right"]].set_visible(False)
    slot.set_xlabel(_title(spec.x))
    slot.set_ylabel(_title(spec.y))
    if spec.facet:
        slot.set_title(f"{spec.facet}: {panel}", loc="left", fontsize="small")


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
