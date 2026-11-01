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
    from iyzee.experiment import FREQUENCY_STEP_THZ

    center = mo.ui.number(value=377.1052067, step=1e-7, label="Laser center (THz)", full_width=True)
    channel = mo.ui.number(1, 8, value=4, step=1, label="Wavemeter channel")
    points = mo.ui.number(1, 200, value=5, step=1, label="Points")
    step_khz = mo.ui.number(
        value=FREQUENCY_STEP_THZ * 1e9,
        start=0.001,
        step=1,
        label="Offset step (kHz)",
    )
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
    from iyzee.experiment import SweepRunner, build_frequency_sweep
    from iyzee.lab import Lab

    mo.stop(
        not run_button.value,
        mo.md("*Nothing has been connected or moved. Tick the box and press the button.*"),
    )

    def reproduce_tui_frequency_sweep(lab: Lab):
        # The same runner the TUI's Sweep page uses: whole-run locks, analyzer setup, a save
        # after every point, and the final status written to the file.
        devices = {key: lab.handles[key].device for key in ("mxa", "shutter", "wavemeter")}
        if any(device is None for device in devices.values()):
            raise RuntimeError("MXA, shutter or wavemeter is not connected")

        # The offset calculation from SweepScreen._build_frequency_run.
        step_thz = step_khz.value * 1e-9
        point_count = int(points.value)
        offsets_thz = [(i - (point_count - 1) / 2) * step_thz for i in range(point_count)]
        sweep = build_frequency_sweep(
            laser_center_thz=center.value,
            wavemeter_channel=int(channel.value),
            offsets_thz=offsets_thz,
            sweep_duration_ms=int(duration_ms.value),
        )

        def show(index, total, step, result, error):
            if error is not None:
                print(f"[{index + 1}/{total}] {step.label}: FAILED: {error!r}")
                return
            print(
                f"[{index + 1}/{total}] {step.label}: OK; "
                f"measured frequency = {result.meta['measured_frequency_thz']:.9f} THz"
            )

        runner = SweepRunner(
            sweep,
            devices,
            [lab.handles[key].lock for key in sweep.requires],
            on_point=show,
            on_save_error=lambda exc: print(f"WARNING: could not save sweep: {exc!r}"),
        )
        return runner.run(), len(sweep.steps)

    lab = Lab()
    try:
        for _key in ("mxa", "shutter", "wavemeter"):  # connecting the shutter connects the PSU
            lab.connect(_key)
        outcome, total = reproduce_tui_frequency_sweep(lab)
    finally:
        lab.close_all()  # the shutter is closed before the supply is released

    mo.stop(
        outcome.setup_error is not None,
        mo.md(f"**Analyzer setup failed:** `{outcome.setup_error}`. Nothing was measured."),
    )
    record = outcome.record
    collected, save_path = outcome.collected, outcome.save_path
    mo.md(
        f"""
        **Run `{record.run_id}`: {record.status}**

        {len(collected)} of {total} points measured · saved to `{save_path}`

        Open it in `notebooks/workbench.py` (press *Refresh index* there).
        """
    )
    return


if __name__ == "__main__":
    app.run()
