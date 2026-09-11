"""Plotting helpers for experiment results."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

from .step import StepResult


def build_figure(results: list[StepResult]):
    """Build (but do not display) the squeezing-minus-shot-noise figure.

    Split out of :func:`multiplot` so non-interactive callers — saving to
    disk, or a TUI that renders traces itself with something like
    ``textual-plotext`` — can get the figure without ``matplotlib`` trying
    to pop up a blocking GUI window.
    """
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
    """Build and display the squeezing-minus-shot-noise figure.

    Kept for the script/CLI entry point (``main.py``) and existing callers.
    Non-interactive callers should use :func:`build_figure` instead.
    """
    build_figure(results)
    plt.show()
