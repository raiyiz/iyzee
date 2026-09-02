"""Plotting helpers for experiment results."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

from .step import StepResult


def multiplot(results: list[StepResult]) -> None:
    """Plot the squeezing-minus-shot-noise difference for each scan point."""
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
    plt.show()
