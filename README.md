# iyzee

Small Python control and measurement toolkit for a lab setup, centered on
automated noise measurements with a Keysight MXA.

## Layout

```text
src/iyzee/
├── main.py               # entry point: run a procedure, plot it, save it
├── base.py                # shared VISA lifecycle, instrument IPs, PSU channels
├── mxa.py                  # Keysight MXA SCPI/VISA driver
├── power.py                 # power supply + optical shutter control
├── scope.py                  # LeCroy oscilloscope communication
├── wavemeter_readout.py        # wavemeter / laser setpoint control
└── experiment/                  # composable measurement procedures
    ├── step.py                   # Step protocol, ExperimentContext, StepResult
    ├── runner.py                  # run_sequence(): executes a list of Steps
    ├── config.py                   # AnalyzerConfig, prepare_analyzer(), acquire_trace()
    ├── procedures.py                # BandwidthStep, FrequencyStep, run_bandwidth_sweep(), run_frequency_sweep()
    ├── persistence.py                # create_dirs(), save_data(), save_step_results()
    └── plotting.py                    # multiplot()
```

## How a measurement runs

A measurement is a sequence of `Step`s run against a shared, already-connected
`ExperimentContext`:

1. A `run_*` procedure in `procedures.py` (e.g. `run_bandwidth_sweep()`) builds
   an `AnalyzerConfig`, connects the MXA via `prepare_analyzer()`, and opens
   the shutter if the procedure needs one.
2. It builds a list of `Step`s (e.g. `BandwidthStep`, `FrequencyStep`) — each
   one describes a single reproducible measurement point.
3. `run_sequence()` runs each step in order, logging progress and either
   stopping on the first failure (`on_error="raise"`, the default) or skipping
   a bad point and continuing (`on_error="skip"`, useful for long unattended
   scans).
4. Each step returns a `StepResult`: the scan coordinate, its unit, the
   acquired traces, and any metadata needed to reproduce that point (RBW/VBW,
   laser setpoint, etc.).
5. `multiplot()` plots the results and `save_step_results()` writes them to a
   compressed `.npz` archive, with per-point metadata embedded alongside the
   data — the saved file is self-describing, not a bare array of numbers.

`main.py` just wires these four calls together for whichever procedure is
currently selected; it does not contain measurement logic itself.

Adding a new experiment means adding a new `Step` subclass and a factory
function in `procedures.py`, not writing a new hand-rolled loop.

## Instrument drivers

- **`mxa.py`** — hardware abstraction for the Keysight MXA. New MXA
  capabilities should be implemented here as reusable SCPI/VISA methods;
  code in `experiment/` should call those methods rather than contain raw
  SCPI strings.
- **`power.py`** — PSU control plus `ShutterControl`, a thin wrapper that
  drives the optical shutter through one PSU channel.
- **`scope.py`** — LeCroy oscilloscope driver (VICP protocol over TCP). Not
  yet unified with `BaseDevice`'s connection lifecycle; treat as a standalone
  legacy driver.
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
state, SCPI commands, and Python implementation. The MXA and measurement
guide covers the measurement chain, RBW/VBW, detector and averaging
semantics, ENBW, synchronization, trace transfer, noise density and band
power, analyzer noise cancellation, trigger timing, and the squeezing/shot-
noise workflow.

The source is written in Typst and compiled in both CI systems. Each pipeline
publishes the compiled PDFs as artifacts for review and download.

- [MXA and measurement guide](docs/mxa-and-measurements.typ) — source
- [Cleanup summary](docs/cleanup-summary.typ) — source
- [GitHub Actions documentation artifacts](https://github.com/raiyiz/iyzee/actions/workflows/ci.yml)
- GitLab CI publishes the same documentation set as pipeline artifacts; the
  repository does not currently declare its GitLab mirror URL.

For a reproducible measurement, the relevant analyzer settings should travel
with the data: frequency range and points, RBW/VBW, detector, averaging,
sweep time, attenuation/reference level, trigger state, and calibration
context. `StepResult.meta` and `save_step_results()`'s per-point metadata are
how that happens in practice.

## Design direction

Keep the separation simple while the project is small:

1. **`experiment/procedures.py` — what to measure:** concrete procedures,
   scan parameters, sequencing.
2. **`experiment/{step,runner,config,persistence,plotting}.py` — the
   machinery a procedure is built from:** the `Step` abstraction, execution,
   analyzer setup, saving, and plotting.
3. **`mxa.py` / `power.py` / `scope.py` / `wavemeter_readout.py` — how to
   control each instrument:** reusable, hardware-specific operations.
4. **`base.py` — shared infrastructure:** connection lifecycle, addresses,
   channel definitions.

As more procedures are added, split `procedures.py` further rather than
letting one file grow indefinitely.

## Known gaps

- `scope.py`'s `LeCroy` driver is not integrated with `BaseDevice`'s
  connection lifecycle (no context-manager support, no injectable transport
  beyond the low-level socket helpers already covered by tests).
- `wavemeter_readout.py`'s frequency constants and `single_readout()` /
  `set_pid_setpoint()` parameters are bare floats (THz/GHz/MHz mixed via a
  `scal` factor) rather than explicitly unit-typed.

Both driver modules above handle physically sensitive behavior (laser
frequency locking, live socket protocol parsing) and are deliberately left
alone during routine cleanup passes — changes there should be reviewed
against the real hardware, not just tests.
