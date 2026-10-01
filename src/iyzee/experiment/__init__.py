"""Composable, tested building blocks for hardware experiment procedures.

See ``docs/mxa-and-measurements.typ`` for the instrument-level model and the
project README for how this package fits together. In short: a ``Step``
describes one reproducible measurement point; ``run_sequence`` runs a list
of them against a shared, already-connected ``ExperimentContext``; and the
concrete procedures in ``procedures.py`` are configuration over that
machinery rather than hand-written loops.
"""

from .core import (
    ExperimentContext,
    RunRecord,
    Step,
    StepCallback,
    StepFailure,
    StepResult,
    run_sequence,
)
from .io import (
    Recording,
    build_figure,
    create_dirs,
    difference_series,
    difference_series_many,
    load_recording,
    multiplot,
    save_step_results,
)
from .procedures import (
    TRACE_SHOT,
    TRACE_SQZ,
    AnalyzerConfig,
    BandwidthStep,
    FrequencyStep,
    acquire_trace,
    bandwidth_sweep_steps,
    build_bandwidth_sweep,
    build_frequency_sweep,
    frequency_sweep_steps,
    prepare_analyzer,
    run_bandwidth_sweep,
    run_frequency_sweep,
)

__all__ = [
    "AnalyzerConfig",
    "BandwidthStep",
    "ExperimentContext",
    "FrequencyStep",
    "Recording",
    "RunRecord",
    "Step",
    "StepCallback",
    "StepFailure",
    "StepResult",
    "TRACE_SHOT",
    "TRACE_SQZ",
    "acquire_trace",
    "bandwidth_sweep_steps",
    "build_bandwidth_sweep",
    "build_figure",
    "create_dirs",
    "difference_series",
    "difference_series_many",
    "build_frequency_sweep",
    "frequency_sweep_steps",
    "load_recording",
    "multiplot",
    "prepare_analyzer",
    "run_bandwidth_sweep",
    "run_frequency_sweep",
    "run_sequence",
    "save_step_results",
]
