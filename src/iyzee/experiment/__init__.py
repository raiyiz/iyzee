"""Composable, tested building blocks for hardware experiment procedures.

See the guide's MXA part (:guide:`part-mxa`) for the instrument-level model and
the Architecture part (:guide:`arch-execution`) for how this package fits together. In short: a ``Step``
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
    difference_statistic,
    difference_values_many,
    load_recording,
    multiplot,
    save_step_results,
)
from .procedures import (
    DEFAULT_SWEEP_DURATION_MS,
    FREQUENCY_STEP_THZ,
    SWEEP_KINDS,
    TRACE_SHOT,
    TRACE_SQZ,
    AnalyzerConfig,
    BandwidthStep,
    FrequencyStep,
    Sweep,
    SweepStep,
    acquire_pair,
    acquire_trace,
    bandwidth_sweep_steps,
    build_bandwidth_sweep,
    build_frequency_sweep,
    frequency_sweep_steps,
    prepare_analyzer,
    run_bandwidth_sweep,
    run_frequency_sweep,
    run_sweep,
)

__all__ = [
    "AnalyzerConfig",
    "BandwidthStep",
    "DEFAULT_SWEEP_DURATION_MS",
    "ExperimentContext",
    "FREQUENCY_STEP_THZ",
    "FrequencyStep",
    "Recording",
    "RunRecord",
    "SWEEP_KINDS",
    "Step",
    "StepCallback",
    "StepFailure",
    "StepResult",
    "Sweep",
    "SweepStep",
    "TRACE_SHOT",
    "TRACE_SQZ",
    "acquire_pair",
    "acquire_trace",
    "bandwidth_sweep_steps",
    "build_bandwidth_sweep",
    "build_figure",
    "build_frequency_sweep",
    "create_dirs",
    "difference_series",
    "difference_series_many",
    "difference_statistic",
    "difference_values_many",
    "frequency_sweep_steps",
    "load_recording",
    "multiplot",
    "prepare_analyzer",
    "run_bandwidth_sweep",
    "run_frequency_sweep",
    "run_sequence",
    "run_sweep",
    "save_step_results",
]
