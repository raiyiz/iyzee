"""Run a sequence of experiment steps against a shared context."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from .step import ExperimentContext, Step, StepResult

log = logging.getLogger("iyzee.experiment")


def run_sequence(
    steps: Sequence[Step],
    ctx: ExperimentContext,
    *,
    on_error: str = "raise",
) -> list[StepResult]:
    """Run ``steps`` in order against ``ctx``, returning their results.

    ``on_error`` controls what happens when a step raises:

    - ``"raise"`` (default): propagate immediately. This matches the
      historical behavior of the hand-written procedures, where a failed
      point aborted the whole scan.
    - ``"skip"``: log the failure and continue with the remaining steps.
      Useful for long unattended scans where losing one point is
      preferable to losing the rest of the run.

    Connection lifecycle (connect/disconnect) is not this function's
    responsibility; ``ctx`` is expected to already be connected, and the
    caller is expected to tear it down (typically in a ``finally`` block
    around this call), the same way the previous hand-written procedures did.
    """
    if on_error not in ("raise", "skip"):
        raise ValueError(f"on_error must be 'raise' or 'skip', got {on_error!r}")

    results: list[StepResult] = []
    for index, step in enumerate(steps):
        step_name = getattr(step, "label", None) or f"step[{index}]"
        log.info("run %s: starting %s", ctx.run_id, step_name)
        try:
            result = step.run(ctx)
        except Exception:
            log.exception("run %s: %s failed", ctx.run_id, step_name)
            if on_error == "raise":
                raise
            continue
        log.info("run %s: finished %s", ctx.run_id, step_name)
        results.append(result)
    return results
