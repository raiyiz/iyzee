"""Focused behavior checks for the read-only plotting helpers."""

from __future__ import annotations

import polars as pl
import pytest

from iyzee.workbench import field_chart, figure_bytes


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "run": ["A", "A", "B", "B", "A", "A", "B", "B"],
            "kind": ["frequency"] * 4 + ["bandwidth"] * 4,
            "channel": ["C1", "C1", "C2", "C2"] * 2,
            "x": [0.0, 1.0, 0.0, 1.0, 1.0, 2.0, 1.0, 2.0],
            "y": [1.0, 2.0, 1.5, 2.5, 2.0, 3.0, 2.5, 3.5],
            "low": [0.8, 1.8, 1.3, 2.3, 1.8, 2.8, 2.3, 3.3],
            "high": [1.2, 2.2, 1.7, 2.7, 2.2, 3.2, 2.7, 3.7],
        }
    )


def test_field_chart_draws_faceted_series_and_uncertainty_bands() -> None:
    figure = field_chart(
        _frame(),
        x="x",
        y="y",
        color="run",
        detail="channel",
        facet="kind",
        band=("low", "high"),
    )

    assert len(figure.axes) == 2
    assert all(axis.lines for axis in figure.axes)
    assert all(axis.collections for axis in figure.axes)
    assert figure_bytes(figure).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("mark", ["points", "bars"])
def test_field_chart_supports_point_and_bar_marks(mark: str) -> None:
    figure = field_chart(_frame(), x="x", y="y", color="run", mark=mark)
    axis = figure.axes[0]

    if mark == "points":
        assert len(axis.collections) == 2
    else:
        assert axis.patches


def test_field_chart_rejects_missing_columns_and_empty_data() -> None:
    with pytest.raises(ValueError, match="not in the selected table"):
        field_chart(_frame(), x="missing", y="y")

    empty = pl.DataFrame({"x": [], "y": []}, schema={"x": pl.Float64, "y": pl.Float64})
    with pytest.raises(ValueError, match="no usable rows"):
        field_chart(empty, x="x", y="y")
