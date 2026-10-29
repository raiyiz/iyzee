"""Run the TUI's frequency sweep without the TUI: a debugging aid, and a scripted measurement.

    uv run --extra analysis --group notebooks marimo edit notebooks/reproduce_frequency_sweep.py

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
def _(center, channel, duration_ms, points, step_khz):
    from iyzee.experiment import build_frequency_sweep

    step_thz = step_khz.value * 1e-9
    point_count = int(points.value)
    offsets_thz = [(i - (point_count - 1) / 2) * step_thz for i in range(point_count)]
    sweep = build_frequency_sweep(
        laser_center_thz=center.value,
        wavemeter_channel=int(channel.value),
        offsets_thz=offsets_thz,
        sweep_duration_ms=int(duration_ms.value),
    )
    return (sweep,)


@app.cell
def _(mo, run_button, sweep):
    from iyzee.experiment.runner import run_recorded_sweep
    from iyzee.lab import Lab

    mo.stop(
        not run_button.value,
        mo.md("*Nothing has been connected or moved. Tick the box and press the button.*"),
    )

    def on_step(index, total, step, result, error):
        if error is not None:
            print(f"[{index + 1}/{total}] {step.label}: FAILED: {error!r}")
        else:
            print(
                f"[{index + 1}/{total}] {step.label}: OK; "
                f"measured frequency = {result.meta['measured_frequency_thz']:.9f} THz"
            )

    def on_save_error(exc):
        print(f"WARNING: could not save sweep: {exc!r}")

    lab = Lab()
    try:
        for _key in ("mxa", "shutter", "wavemeter"):
            lab.connect(_key)
        handles = lab.handles
        mx = handles["mxa"].device
        shutter = handles["shutter"].device
        wavemeter = handles["wavemeter"].device
        if mx is None or shutter is None or wavemeter is None:
            raise RuntimeError("MXA, shutter or wavemeter is not connected")

        outcome = run_recorded_sweep(
            sweep,
            mx=mx,
            locks=[handles[key].lock for key in sweep.requires],
            shutter=shutter,
            wavemeter=wavemeter,
            on_step=on_step,
            on_save_error=on_save_error,
        )
        if outcome.setup_error is not None:
            raise outcome.setup_error
        if outcome.error is not None:
            raise outcome.error
        if outcome.record is None:
            raise RuntimeError("sweep finished without a run record")

        report = f"""
        **Run `{outcome.record.run_id}`: {outcome.record.status}**

        {len(outcome.results)} of {outcome.total} points measured · saved to `{outcome.save_path}`

        Open it in `notebooks/workbench.py` (press *Refresh index* there).
        """
    finally:
        lab.close_all()  # the shutter is closed before the supply is released
    mo.md(report)
    return


if __name__ == "__main__":
    app.run()
