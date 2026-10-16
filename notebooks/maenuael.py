import marimo

__generated_with = "0.25.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import numpy as np

    return mo, np


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    Reproduce the TUI's frequency sweep
    Paste this whole cell into iyzee's internal IPython console. It reproduces the TUI's hardware setup, locking, point execution, error policy, and per-point saving. It deliberately omits only the Textual progress updates and live plot rendering, which helps isolate your silent-exit problem.
    """)
    return


@app.cell
def _(lab):
    from contextlib import ExitStack
    from dataclasses import asdict

    from iyzee.experiment import (
        ExperimentContext,
        RunRecord,
        build_frequency_sweep,
        create_dirs,
        prepare_analyzer,
        run_sequence,
        save_step_results,
    )

    def reproduce_tui_frequency_sweep():
        # Same connected devices and whole-run locks as the TUI.
        mxa_handle = lab.handles["mxa"]
        shutter_handle = lab.handles["shutter"]

        mx = mxa_handle.device
        shutter = shutter_handle.device

        if mx is None or shutter is None:
            raise RuntimeError("MXA or shutter is not connected")

        # Exact defaults and offset calculation from SweepScreen.
        center = 377.1052067
        channel = 4
        points = 5
        step_khz = 10

        step_thz = step_khz * 1e-9
        offsets_thz = [(i - (points // 2)) * step_thz for i in range(points)]

        steps, config = build_frequency_sweep(
            laser_center_thz=center,
            wavemeter_channel=channel,
            offsets_thz=offsets_thz,
            sweep_duration_ms=10,
        )

        collected = []
        savedir = None
        save_path = None
        save_warned = False
        failed = False

        with ExitStack() as stack:
            # Hold both device locks for the entire run.
            stack.enter_context(mxa_handle.lock)
            stack.enter_context(shutter_handle.lock)

            # Same analyzer initialization as the TUI.
            prepare_analyzer(mx, (1, 2), config)

            record = RunRecord(config=asdict(config))
            ctx = ExperimentContext(
                mx=mx,
                run_id=record.run_id,
                shutter=shutter,
                config=record.config,
            )

            def checkpoint():
                nonlocal savedir, save_path, save_warned

                try:
                    if savedir is None:
                        savedir = create_dirs()

                    save_path = save_step_results(
                        list(collected),
                        savedir,
                        record.as_metadata(),
                        name="frequency",
                        path=save_path,
                    )
                except Exception as exc:  # noqa: BLE001 - saving must never interrupt acquisition
                    # Like the TUI, a save failure must not stop the measurement.
                    if not save_warned:
                        print(f"WARNING: could not save sweep: {exc!r}")
                        save_warned = True

            def on_step(index, total, step, result, error):
                record.on_step(index, total, step, result, error)

                if error is not None:
                    print(f"[{index + 1}/{total}] {step.label}: FAILED: {error!r}")
                    return

                collected.append(result)

                # Save immediately after every successful point.
                checkpoint()

                print(
                    f"[{index + 1}/{total}] {step.label}: OK; "
                    f"measured frequency = "
                    f"{result.meta['measured_frequency_thz']:.9f} THz"
                )

            try:
                # Same policy as the TUI: log a failed point and continue.
                run_sequence(
                    steps,
                    ctx,
                    on_error="skip",
                    on_step=on_step,
                )
            except Exception:
                failed = True
                raise
            finally:
                record.finish(failed=failed)

                # Rewrite the final checkpoint with the completed run status.
                if collected:
                    checkpoint()
        print(f"\nRun ID: {record.run_id}")
        print(f"Status: {record.status}")
        print(f"Successful points: {len(collected)}/{len(steps)}")
        print(f"Saved file: {save_path}")

        return collected

    reproduce_tui_frequency_sweep()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    The TUI does not call `run_frequency_sweep()`. It builds the frequency steps itself, acquires the instrument locks, configures the MXA, runs `run_sequence(..., on_error="skip")`, and saves each successful point immediately.

    The relevant implementation is [`sweep.py`](https://github.com/raiyiz/iyzee/blob/buathy/src/iyzee/tui/screens/sweep.py), using [`procedures.py`](https://github.com/raiyiz/iyzee/blob/buathy/src/iyzee/experiment/procedures.py) and [`core.py`](https://github.com/raiyiz/iyzee/blob/buathy/src/iyzee/experiment/core.py).

    ## Reproduce the TUI's frequency sweep

    Paste this whole cell into iyzee's internal IPython console. It reproduces the TUI's hardware setup, locking, point execution, error policy, and per-point saving. It deliberately omits only the Textual progress updates and live plot rendering, which helps isolate your silent-exit problem.

    ```
    from contextlib import ExitStack
    from dataclasses import asdict

    from iyzee.experiment import (
        ExperimentContext,
        RunRecord,
        build_frequency_sweep,
        create_dirs,
        prepare_analyzer,
        run_sequence,
        save_step_results,
    )

    def reproduce_tui_frequency_sweep():
        # Same connected devices and whole-run locks as the TUI.
        mxa_handle = lab.handles["mxa"]
        shutter_handle = lab.handles["shutter"]

        mx = mxa_handle.device
        shutter = shutter_handle.device

        if mx is None or shutter is None:
            raise RuntimeError("MXA or shutter is not connected")

        # Exact defaults and offset calculation from SweepScreen.
        center = 377.1052067
        channel = 4
        points = 5
        step_khz = 10

        step_thz = step_khz * 1e-9
        offsets_thz = [
            (i - (points // 2)) * step_thz
            for i in range(points)
        ]

        steps, config = build_frequency_sweep(
            laser_center_thz=center,
            wavemeter_channel=channel,
            offsets_thz=offsets_thz,
            sweep_duration_ms=10,
        )

        collected = []
        savedir = None
        save_path = None
        save_warned = False
        failed = False

        with ExitStack() as stack:
            # Hold both device locks for the entire run.
            stack.enter_context(mxa_handle.lock)
            stack.enter_context(shutter_handle.lock)

            # Same analyzer initialization as the TUI.
            prepare_analyzer(mx, (1, 2), config)

            record = RunRecord(config=asdict(config))
            ctx = ExperimentContext(
                mx=mx,
                run_id=record.run_id,
                shutter=shutter,
                config=record.config,
            )

            def checkpoint():
                nonlocal savedir, save_path, save_warned

                try:
                    if savedir is None:
                        savedir = create_dirs()

                    save_path = save_step_results(
                        list(collected),
                        savedir,
                        record.as_metadata(),
                        name="frequency",
                        path=save_path,
                    )
                except Exception as exc:
                    # Like the TUI, a save failure must not stop the measurement.
                    if not save_warned:
                        print(f"WARNING: could not save sweep: {exc!r}")
                        save_warned = True

            def on_step(index, total, step, result, error):
                record.on_step(index, total, step, result, error)

                if error is not None:
                    print(
                        f"[{index + 1}/{total}] "
                        f"{step.label}: FAILED: {error!r}"
                    )
                    return

                collected.append(result)

                # Save immediately after every successful point.
                checkpoint()

                print(
                    f"[{index + 1}/{total}] {step.label}: OK; "
                    f"measured frequency = "
                    f"{result.meta['measured_frequency_thz']:.9f} THz"
                )

            try:
                # Same policy as the TUI: log a failed point and continue.
                run_sequence(
                    steps,
                    ctx,
                    on_error="skip",
                    on_step=on_step,
                )
            except Exception:
                failed = True
                raise
            finally:
                record.finish(failed=failed)

                # Rewrite the final checkpoint with the completed run status.
                if collected:
                    checkpoint()

        print(f"\nRun ID: {record.run_id}")
        print(f"Status: {record.status}")
        print(f"Successful points: {len(collected)}/{len(steps)}")
        print(f"Saved file: {save_path}")

        return collected


    results = reproduce_tui_frequency_sweep()
    ```

    ## What happens at each point?

    The TUI uses this exact measurement sequence:

    1\. Configure the MXA once before the run

    Center 1.5 MHz, span 0 Hz, RBW 24 kHz, automatic VBW and attenuation, immediate trigger, 10 ms sweep duration, 150 averages, LOG averaging. Traces 1 and 2 are enabled in AVER mode.

    2\. Set and measure the laser frequency

    Create a `Wavemeter()`, send the PID setpoint for channel 4, sleep for 0.5 seconds, and read the measured frequency.

    3\. Acquire squeezing

    Open the shutter, enable updates for trace 1, initiate a single sweep, wait for operation completion, read the ASCII trace data, disable trace updates, and close the shutter in a `finally` block.

    4\. Acquire shot noise

    Repeat the single-sweep acquisition for trace 2, with the shutter closed.

    5\. Record the point

    Append the result, save the growing recording to disk, update the run metadata and continue to the next setpoint.

    The default five setpoints are:

    | Point | Offset from center |
    | ----- | ------------------ |
    | 1     | -20 kHz            |
    | 2     | -10 kHz            |
    | 3     | 0 kHz              |
    | 4     | +10 kHz            |
    | 5     | +20 kHz            |
    """)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
