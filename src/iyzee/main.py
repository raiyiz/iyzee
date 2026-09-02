"""Entry point: run the currently selected measurement procedure.

This module used to hold the measurement procedures themselves. They now
live in :mod:`iyzee.experiment` as composable ``Step``s, so this file stays
thin: pick a procedure, plot it, save it.
"""

from .experiment import create_dirs, multiplot, run_bandwidth_sweep, save_step_results


def main():
    """Run the currently selected measurement procedure."""
    results = run_bandwidth_sweep()
    print(f"{len(results)=}")
    multiplot(results)
    savedir = create_dirs()
    save_step_results(results, savedir)
    return results


if __name__ == "__main__":
    main()
