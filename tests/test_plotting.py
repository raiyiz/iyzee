from __future__ import annotations

import numpy as np
import pytest

from iyzee.tui.plotting import describe_timebase, format_si, time_axis, time_series


def test_si_formatting_and_the_timebase_description():
    for value, text in ((2.5e-6, "2.5 µs"), (1e-9, "1 ns"), (0.5, "500 ms"), (3.0, "3 s")):
        assert format_si(value, "s") == text
    assert format_si(1e9, "S/s") == "1 GS/s"

    lines = describe_timebase(-5e-6, 1e-9, 10_000, "S")
    assert lines[0] == "Window: -5 µs to 4.999 µs (10 µs, 10000 samples)"
    assert lines[1] == "Sample interval: 1 ns, 1 GS/s"


def test_time_axes_pick_a_readable_unit_and_one_axis_serves_every_trace():
    assert time_axis("S", 4e-6) == (pytest.approx(1e6), "µs")
    assert time_axis("S", 2.0) == (1.0, "s")
    assert time_axis("div", 10.0) == (1.0, "div")  # other units are left alone

    t = np.linspace(-1e-6, 1e-6, 5)
    series, label = time_series([(t, t * 0, "C1"), (t, t * 0, "C2")], "S")
    assert label == "Time (µs)"
    assert series[0][0][0] == pytest.approx(-1.0)
    assert series[1][0][-1] == pytest.approx(1.0)
