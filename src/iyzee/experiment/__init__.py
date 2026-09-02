"""Composable, tested building blocks for hardware experiment procedures.

See ``docs/mxa-and-measurements.typ`` for the instrument-level model and the
project README for how this package fits together. In short: a ``Step``
describes one reproducible measurement point; ``run_sequence`` runs a list
of them against a shared, already-connected ``ExperimentContext``; and the
concrete procedures in ``procedures.py`` are configuration over that
machinery rather than hand-written loops.
"""

from .config import TRACE_SHOT, TRACE_SQZ, AnalyzerConfig, acquire_trace, prepare_analyzer
from .persistence import create_dirs, save_data, save_step_results
from .plotting import multiplot
from .procedures import (
    BandwidthStep,
    FrequencyStep,
    bandwidth_sweep_steps,
    frequency_sweep_steps,
    run_bandwidth_sweep,
    run_frequency_sweep,
)
from .runner import run_sequence
from .step import ExperimentContext, Step, StepResult

__all__ = [
    "AnalyzerConfig",
    "BandwidthStep",
    "ExperimentContext",
    "FrequencyStep",
    "Step",
    "StepResult",
    "TRACE_SHOT",
    "TRACE_SQZ",
    "acquire_trace",
    "bandwidth_sweep_steps",
    "create_dirs",
    "frequency_sweep_steps",
    "multiplot",
    "prepare_analyzer",
    "run_bandwidth_sweep",
    "run_frequency_sweep",
    "run_sequence",
    "save_data",
    "save_step_results",
]
