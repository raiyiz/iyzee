"""Run the TUI's frequency sweep without the TUI: a debugging aid, and a scripted measurement.

    uv run --group notebooks marimo edit notebooks/reproduce_frequency_sweep.py

This **drives real instruments**: it moves the laser's lock setpoint, opens and closes the
shutter and reconfigures the spectrum analyzer. So nothing happens when you open the
notebook: you have to tick the confirmation box and then press the button.

It does what ``SweepScreen`` does for a frequency sweep (same devices, same whole-run
locks, same analyzer set-up, same ``on_error="skip"`` policy, a checkpoint save after every
point) and leaves out only the Textual progress display and live plot. That makes it
useful for telling "the sweep itself" apart from "something in the TUI", for example when
chasing a silent exit.

Close the TUI first: this notebook opens its own connections, and the supplies and
instruments accept one at a time. Open the saved file in ``notebooks/workbench.py``.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(
    width="medium",
    app_title="iyzee · reproduce a frequency sweep",
)


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md("""
    # Reproduce a frequency sweep

    Runs the same measurement as the **Sweep** page, from a notebook. The defaults below
    are the TUI's.
    """)
    return


@app.cell
def _(mo):
    center = mo.ui.number(
        value=377.1052067, step=1e-7, label="Laser center (THz)", full_width=True
    )
    channel = mo.ui.number(1, 8, value=4, step=1, label="Wavemeter channel")
    points = mo.ui.number(1, 200, value=5, step=1, label="Points")
    step_khz = mo.ui.number(value=10, start=0.001, step=1, label="Offset step (kHz)")
    duration_ms = mo.ui.number(1, 10_000, value=10, step=1, label="Sweep duration (ms)")
    mo.hstack([center, channel, points, step_khz, duration_ms], widths="equal", align="end")
    return center, channel, duration_ms, points, step_khz


@app.cell
def _(mo):
    armed = mo.ui.checkbox(
        label=(
            "I understand this moves the laser setpoint, opens the shutter and "
            "reconfigures the analyzer, and that the TUI is closed."
        )
    )
    armed
    return (armed,)


@app.cell
def _(armed, mo):
    run_button = mo.ui.run_button(
        label="Connect and run the sweep",
        kind="danger",
        disabled=not armed.value,
    )
    run_button
    return (run_button,)


@app.cell
def _(center, channel, duration_ms, mo, points, run_button, step_khz):
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
    from iyzee.lab import Lab

    mo.stop(
        not run_button.value,
        mo.md("*Nothing has been connected or moved. Tick the box and press the button.*"),
    )

    def reproduce_tui_frequency_sweep(lab: Lab):
        # Same connected devices and whole-run locks as the TUI.
        mxa_handle = lab.handles["mxa"]
        shutter_handle = lab.handles["shutter"]
        wavemeter = lab.handles["wavemeter"].device

        mx = mxa_handle.device
        shutter = shutter_handle.device

        if mx is None or shutter is None or wavemeter is None:
            raise RuntimeError("MXA, shutter or wavemeter is not connected")

        # The offset calculation from SweepScreen._build_frequency_run.
        step_thz = step_khz.value * 1e-9
        offsets_thz = [(i - (int(points.value) // 2)) * step_thz for i in range(int(points.value))]

        sweep = build_frequency_sweep(
            laser_center_thz=center.value,
            wavemeter_channel=int(channel.value),
            offsets_thz=offsets_thz,
            sweep_duration_ms=int(duration_ms.value),
        )
        steps, config = sweep.steps, sweep.config

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
                wavemeter=wavemeter,
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
                except Exception as exc:  # noqa: BLE001
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
                    f"measured frequency = {result.meta['measured_frequency_thz']:.9f} THz"
                )

            try:
                # Same policy as the TUI: log a failed point and continue.
                run_sequence(steps, ctx, on_error="skip", on_step=on_step)
            except Exception:
                failed = True
                raise
            finally:
                record.finish(failed=failed)

                # Rewrite the final checkpoint with the completed run status.
                if collected:
                    checkpoint()

        return record, collected, len(steps), save_path

    lab = Lab()
    try:
        for _key in ("mxa", "shutter", "wavemeter"):  # connecting the shutter connects the PSU
            lab.connect(_key)
        record, collected, total, save_path = reproduce_tui_frequency_sweep(lab)
    finally:
        lab.close_all()  # the shutter is closed before the supply is released

    mo.md(
        f"""
        **Run `{record.run_id}`: {record.status}**

        {len(collected)} of {total} points measured · saved to `{save_path}`

        Open it in `notebooks/workbench.py` (press *Rescan folder* there).
        """
    )
    return


if __name__ == "__main__":
    app.run()
