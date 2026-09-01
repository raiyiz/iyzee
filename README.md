# iyzee

Small Python control and measurement toolkit for a lab setup, currently centered on automated noise measurements with a Keysight MXA.

## Current layout

```text
src/iyzee/
├── main.py              # measurement procedures / orchestration
├── base.py              # shared channels, instrument addresses, test device
├── mxa.py               # Keysight MXA SCPI/VISA driver
├── power.py             # power supply + shutter control
├── scope.py             # LeCroy oscilloscope communication
└── wavemeter_readout.py # wavemeter / laser setpoint control
```

## Measurement flow

`main.py` is the procedure layer. It configures instruments, sequences acquisitions, performs basic analysis/plotting, and saves results.

Current procedures:

- `record_bw_seq()` — sweep MXA resolution bandwidth and acquire squeezing/shot-noise traces.
- `record_freq_seq()` — scan laser frequency through the wavemeter while coordinating shutter and MXA measurements.
- `prepare_analyzer()` — shared MXA setup.
- `acquire_trace()` — shared single-trace acquisition sequence.

`mxa.py` is the hardware abstraction for the Keysight MXA. New MXA capabilities should be implemented there as reusable SCPI/VISA methods; procedures in `main.py` should call those methods rather than contain raw SCPI commands.

## Documentation

The technical documentation connects the measurement physics to the analyzer state, SCPI commands, and Python implementation. The MXA and measurement guide covers the measurement chain, RBW/VBW, detector and averaging semantics, ENBW, synchronization, trace transfer, noise density and band power, analyzer noise cancellation, trigger timing, and the squeezing/shot-noise workflow.

The source is written in Typst and compiled in both CI systems. Each pipeline publishes the compiled PDFs as artifacts for review and download.

- [MXA and measurement guide](docs/mxa-and-measurements.typ) — source
- [Cleanup summary](docs/cleanup-summary.typ) — source
- [GitHub Actions documentation artifacts](https://github.com/raiyiz/iyzee/actions/workflows/ci.yml)
- [GitLab CI documentation artifacts](https://github.com/raiyiz/iyzee/-/pipelines)

For a reproducible measurement, the relevant analyzer settings should travel with the data: frequency range and points, RBW/VBW, detector, averaging, sweep time, attenuation/reference level, trigger state, and calibration context.

## Design direction

Keep the separation simple while the project is small:

1. **`main.py` — what to measure:** procedures, scan parameters, sequencing, and experiment-level analysis/output.
2. **`mxa.py` — how to control the MXA:** reusable instrument operations and SCPI details.
3. **Other instrument modules — hardware drivers:** power/shutter, wavemeter, oscilloscope, etc.
4. **`base.py` — shared infrastructure:** channel/address definitions and the lightweight test device.

As more procedures are added, split `main.py` into a `measurements/` package rather than allowing one orchestration file to grow indefinitely. Plotting and data serialization can also move into dedicated helpers once they are shared by multiple procedures.

## `moonshine` cleanup

The branch starts with a conservative cleanup rather than a large redesign:

- shared device definitions moved out of `__init__.py` into `base.py`;
- package initialization now only exposes the small public base API;
- repeated trace-acquisition logic in `main.py` is centralized in `acquire_trace()`;
- analyzer defaults and procedure-specific parameters are separated;
- plotting is performed once after all traces are acquired;
- measurement directories are created recursively and data is saved as compressed NumPy archives;
- the power-supply/shutter module has been simplified and its global-output disable command corrected.

The next structural cleanup should make imports consistently package-relative and then add a proper mock/test layer for the instrument drivers. That should happen before the codebase becomes much larger.
