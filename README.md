# iyzee

Small Python control and measurement toolkit for a lab setup, centered on
automated noise measurements with a Keysight MXA.

## Layout

```text
src/iyzee/
├── main.py               # legacy script entry point
├── tui/                  # interactive terminal UI
│   ├── app.py            # Textual application and navigation
│   ├── devices.py        # worker-safe device ownership / serialization
│   ├── trace_plot.py     # MXA trace presentation
│   ├── scope_screen.py   # LeCroy scope page
│   └── scope_plot.py     # LeCroy waveform presentation
├── base.py               # shared VISA lifecycle, instrument IPs, PSU channels
├── mxa.py                # Keysight MXA SCPI/VISA driver
├── power.py              # power supply + optical shutter control
├── scope.py              # LeCroy high-level driver and waveform conversion
├── vicp.py               # VICP framing and TCP transport
├── wavemeter_readout.py  # wavemeter / laser setpoint control
└── experiment/            # composable measurement procedures
    ├── step.py            # Step protocol, ExperimentContext, StepResult
    ├── runner.py          # run_sequence(): executes a list of Steps
    ├── config.py          # AnalyzerConfig, prepare_analyzer(), acquire_trace()
    ├── procedures.py      # BandwidthStep, FrequencyStep, run_*_sweep()
    ├── persistence.py     # create_dirs(), save_data(), save_step_results()
    └── plotting.py        # build_figure() + script-facing multiplot()
```

## How a measurement runs

A measurement is a sequence of `Step`s run against a shared, already-connected
`ExperimentContext`. The important ownership boundary is explicit:

1. `main.py` or the TUI creates the required hardware objects and owns their
   lifecycle.
2. A `run_*` procedure in `procedures.py` receives those already-owned devices
   and configures them via `prepare_analyzer()`. It does not create, connect,
   or disconnect hardware.
3. The procedure builds a list of `Step`s (e.g. `BandwidthStep`,
   `FrequencyStep`) — each one describes a single reproducible measurement
   point.
4. `run_sequence()` runs each step in order. It can log failures, skip failed
   points with `on_error="skip"`, and optionally report successful steps via
   `on_step(index, total, result)` for interactive clients.
5. Each step returns a `StepResult`: the scan coordinate, its unit, the
   acquired traces, and metadata needed to reproduce that point.
6. `save_step_results()` writes the results to a compressed `.npz` archive
   with per-point metadata.

Constructing a device does not connect it. `BaseDevice` opens the VISA resource
when `connect()` is called, normally through the explicit `with device:`
boundary in script code or from a TUI worker. This keeps hardware access out of
experiment construction and makes procedure tests independent of real
instruments.

Adding a new experiment means adding a new `Step` subclass and a factory
function in `procedures.py`, not writing a new hand-rolled loop.

## Terminal UI

The interactive UI is built with [Textual](https://textual.textualize.io/) and
`textual-plotext`. Launch it with:

```bash
iyzee-tui
```

Starting the UI does **not** contact any instrument. The initial dashboard is a
safe idle state: device indicators are disconnected, the run status is idle,
and the trace area is blank until a successful acquisition. Press **Connect**
for the device you want to use, then start the workflow.

The main dashboard covers MXA and shutter/PSU workflows. Press **LeCroy scope**
or `s` to open the dedicated scope page. That page deliberately keeps the first
scope slice small and useful: explicitly connect/disconnect, query `*IDN?`,
choose a channel, acquire one physical-unit waveform, and inspect it in the
terminal. Scope acquisition uses the scope's current front-panel acquisition
configuration; it does not introduce a second scope-configuration system yet.

The UI is deliberately a thin presentation layer. Experiment logic remains in
`experiment/`, while `tui/devices.py` owns lazy device creation and serializes
hardware access. Synchronous PyVISA, VICP socket, and wavemeter calls stay
synchronous in their drivers but are invoked from Textual thread workers, so the
event loop never waits for instrument I/O.

The LeCroy VICP implementation is split into `vicp.py` and `scope.py`.
`VICPTransport` owns TCP connection state, exact reads, complete writes, framed
header decoding, frame-size limits, timeouts, and EOI-based message termination.
The scope driver owns LeCroy commands and binary waveform interpretation. This
keeps protocol details out of the TUI and makes the wire protocol testable with
fake sockets.

For script workflows, `multiplot()` keeps its existing blocking `plt.show()`
behavior. TUI code uses `build_figure()` only when a Matplotlib figure is
needed and renders live acquisitions through the terminal plotting widgets.

### UI extension path

Keep future UI work in layers:

- **Application / screens:** navigation, configuration forms, status, keyboard
  bindings.
- **Device manager:** resource ownership, serialized access, connection state,
  and eventually device discovery/health checks.
- **Presentation widgets:** live traces, run history, tables, and saved-result
  browsing.
- **Experiment layer:** reusable `Step`s, procedures, and progress hooks.
- **Drivers/transports:** synchronous, hardware-specific SCPI/HTTP/VICP APIs.

A useful rule is: the TUI should orchestrate existing experiment operations,
not grow a second implementation of a sweep or acquisition loop.

The initial UI intentionally has no hard-stop button. Textual thread workers
cannot safely kill a running synchronous instrument call. Cancellation should
therefore be introduced as an explicit experiment-level mechanism (for
example a cooperative cancellation flag checked between steps), with shutter
shutdown guaranteed in `finally` blocks.

The plotting widgets are isolated from the experiment layer, so their terminal
backend can be replaced without changing acquisition or persistence APIs.

## Instrument drivers

- **`mxa.py`** — hardware abstraction for the Keysight MXA. New MXA
  capabilities should be implemented here as reusable SCPI/VISA methods;
  code in `experiment/` should call those methods rather than contain raw
  SCPI strings.
- **`power.py`** — PSU control plus `ShutterControl`, a thin wrapper that
  drives the optical shutter through one PSU channel.
- **`scope.py` + `vicp.py`** — LeCroy oscilloscope control over the native VICP
  TCP protocol. `scope.py` exposes explicit connect/close lifecycle and typed
  waveform data; `vicp.py` isolates framing and socket behavior.
- **`wavemeter_readout.py`** — wavemeter readout and PID setpoint control over
  HTTP, plus Rubidium transition-frequency reference tables used for
  reporting laser detuning.
- **`base.py`** — shared infrastructure: `BaseDevice` (VISA connect/close/
  context-manager lifecycle), `IP` (instrument addresses), `CH` (PSU channel
  IDs). `KeysightMXA` and `PSU` both build on `BaseDevice`.

## Safety notes

This is laboratory/instrument-control software; a few rules matter more here
than in typical application code:

- Never turn a hardware communication failure into a plausible measurement
  value (see `WavemeterReadoutError`, `KeysightMXA.wait_opc()`).
- Don't change instrument setpoints or SCPI behavior without understanding
  and testing the change — these drive real hardware.

## Documentation

The technical documentation connects the measurement physics to the analyzer
state, SCPI commands, and Python implementation. The MXA and measurement guide
covers the measurement chain, RBW/VBW, detector and averaging semantics, ENBW,
synchronization, trace transfer, noise density, band power, analyzer noise
cancellation, trigger timing, and the squeezing/shot-noise workflow.

The source is written in Typst and compiled in CI.

- [MXA and measurement guide](docs/mxa-and-measurements.typ) — source
- [Cleanup summary](docs/cleanup-summary.typ) — source
- [GitHub Actions documentation artifacts](https://github.com/raiyiz/iyzee/actions/workflows/ci.yml)

For a reproducible measurement, the relevant analyzer settings should travel
with the data: frequency range and points, RBW/VBW, detector, averaging,
sweep time, attenuation/reference level, trigger state, and calibration
context. `StepResult.meta` and `save_step_results()`'s per-point metadata are
how that happens in practice.

## Design direction

Keep the separation simple while the project is small:

1. **Application boundaries:** `main.py` for scripts and `tui/` for interactive
   control.
2. **`experiment/procedures.py` — what to measure:** concrete procedures,
   scan parameters, sequencing.
3. **`experiment/{step,runner,config,persistence,plotting}.py` — the
   machinery a procedure is built from.**
4. **`mxa.py` / `power.py` / `scope.py` / `wavemeter_readout.py` — how to
   control each instrument:** reusable, hardware-specific operations.
5. **`base.py` and `vicp.py` — shared infrastructure:** VISA/VICP lifecycle,
   addresses, framing, and transport boundaries.

As more procedures are added, split `procedures.py` further rather than
letting one file grow indefinitely.

## Known gaps

- The TUI currently assumes the lab's known static instrument addresses.
- Scope configuration, triggering, measurement tables, coordinated MXA+scope
  experiments, cancellation, and a run-history browser are not yet exposed by
  the TUI.
- `wavemeter_readout.py`'s frequency constants and `single_readout()` /
  `set_pid_setpoint()` parameters are bare floats (THz/GHz/MHz mixed via a
  `scal` factor) rather than explicitly unit-typed.

Physically sensitive driver changes should be reviewed against the real
hardware, not just tests.
