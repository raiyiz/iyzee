"""Run a sequence of experiment steps against a shared context."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from .step import ExperimentContext, Step, StepResult

log = logging.getLogger("iyzee.experiment")

# Called after each step is attempted, whether it succeeded or failed:
# on_step(index, total, step, result, error). Exactly one of
# ``result``/``error`` is not None. This is the seam a UI (or any other
# observer) hooks into for live progress; ``run_sequence`` itself stays
# UI-agnostic and keeps working with no callback at all.
StepCallback = Callable[[int, int, Step, "StepResult | None", "BaseException | None"], None]


def run_sequence(
    steps: Sequence[Step],
    ctx: ExperimentContext,
    *,
    on_error: str = "raise",
    on_step: StepCallback | None = None,
) -> list[StepResult]:
    """Run ``steps`` in order against ``ctx``, returning their results.

    ``on_error`` controls what happens when a step raises:

    - ``"raise"`` (default): propagate immediately. This matches the
      historical behavior of the hand-written procedures, where a failed
      point aborted the whole scan.
    - ``"skip"``: log the failure and continue with the remaining steps.
      Useful for long unattended scans where losing one point is
      preferable to losing the rest of the run.

    ``on_step``, if given, is called after every step is attempted (see
    :data:`StepCallback`). It is the hook a caller uses for live progress
    reporting (a progress bar, a live-updating trace plot, ...) without
    ``run_sequence`` needing to know anything about how that progress is
    displayed. Any exception raised *inside* ``on_step`` itself propagates
    immediately, regardless of ``on_error``.

    Connection lifecycle (connect/disconnect) is not this function's
    responsibility; ``ctx`` is expected to already be connected, and the
    caller is expected to tear it down (typically in a ``finally`` block
    around this call), the same way the previous hand-written procedures did.
    """
    if on_error not in ("raise", "skip"):
        raise ValueError(f"on_error must be 'raise' or 'skip', got {on_error!r}")

    total = len(steps)
    results: list[StepResult] = []
    for index, step in enumerate(steps):
        step_name = getattr(step, "label", None) or f"step[{index}]"
        log.info("run %s: starting %s", ctx.run_id, step_name)
        try:
            result = step.run(ctx)
        except Exception as exc:
            log.exception("run %s: %s failed", ctx.run_id, step_name)
            if on_step is not None:
                on_step(index, total, step, None, exc)
            if on_error == "raise":
                raise
            continue
        log.info("run %s: finished %s", ctx.run_id, step_name)
        results.append(result)
        if on_step is not None:
            on_step(index, total, step, result, None)
    return results
