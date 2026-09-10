"""Application entry point and hardware resource boundary."""

from .experiment import create_dirs, multiplot, run_bandwidth_sweep, save_step_results
from .mxa import KeysightMXA


def main():
    """Open the required hardware, run the procedure, then plot and save."""
    with KeysightMXA() as mx:
        results = run_bandwidth_sweep(mx)

    print(f"{len(results)=}")
    multiplot(results)
    savedir = create_dirs()
    save_step_results(results, savedir)
    return results


if __name__ == "__main__":
    main()
