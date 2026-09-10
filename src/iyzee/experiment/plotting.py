"""Plotting helpers for experiment results."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure

from .step import StepResult


def build_figure(results: list[StepResult]) -> Figure:
    """Build the standard squeezing-minus-shot-noise figure without showing it."""
    fig, ax = plt.subplots()
    labels = []

    for result in results:
        squeezing = np.asarray(result.traces.get("squeezing"))
        shot_noise = np.asarray(result.traces.get("shot_noise"))
        difference = squeezing - shot_noise
        ax.plot(difference)
        labels.append(result.label)

    if labels:
        ax.legend(labels, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.1))
    ax.set_xlabel("Trace point")
    ax.set_ylabel("Squeezing - shot noise")
    fig.tight_layout()
    return fig


def multiplot(results: list[StepResult]) -> None:
    """Build and show the standard figure for script-based workflows."""
    build_figure(results)
    plt.show()
