"""Core abstractions for composable, hardware-driving experiment steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class StepResult:
    """The outcome of running one experiment step.

    ``traces`` holds named acquisitions (e.g. ``"squeezing"``, ``"shot_noise"``)
    so a step can capture more than the historical squeezing/shot-noise pair
    without changing this schema. ``meta`` carries whatever instrument state
    or context is relevant to reproducing this specific point (e.g. RBW/VBW,
    averaging count, laser setpoint) and is what makes a saved measurement
    self-describing rather than a bare array of numbers.
    """

    label: str
    x_value: float
    x_unit: str
    traces: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentContext:
    """Already-connected hardware handles and run-level metadata shared by
    every step in a sequence.

    A context is built once per run and passed to each step's ``run()``;
    steps do not open or close connections themselves — that stays the
    responsibility of the procedure that builds the context, the same way
    ``record_bw_seq``/``record_freq_seq`` used to own connect/disconnect.
    """

    mx: Any
    run_id: str
    shutter: Any | None = None
    config: dict[str, Any] = field(default_factory=dict)


class Step(Protocol):
    """A single reproducible measurement point in an experiment sequence."""

    def run(self, ctx: ExperimentContext) -> StepResult: ...
