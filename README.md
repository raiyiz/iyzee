# iyzee

Small Python control and measurement toolkit for a lab setup, currently centered on automated noise measurements with a Keysight MXA.

## Current layout

```text
src/iyzee/
├── main.py              # measurement procedures / orchestration
├── mxa.py               # Keysight MXA SCPI/VISA driver
├── power.py             # power supply + shutter control
├── scope.py             # LeCroy oscilloscope communication
├── wavemeter_readout.py # wavemeter / laser setpoint control
└── __init__.py          # shared device/address definitions and test device
```

## Measurement flow

The current entry point is `main.py`. It configures the MXA, performs measurement sequences, combines the acquired traces, plots the result, and is intended to save measurement data.

The main procedures are:

- `record_bw_seq()` — sweep the analyzer RBW and acquire squeezing/shot-noise traces.
- `record_freq_seq()` — scan the laser frequency through the wavemeter while coordinating the shutter and MXA.
- `prepare_analyzer()` — common MXA setup for the procedures.

`mxa.py` is deliberately lower-level: it contains the Keysight MXA SCPI commands for frequency/bandwidth, sweep/averaging, traces, markers, triggering, and data acquisition. New MXA capabilities should generally be added there rather than directly in `main.py`.

## Development direction

For the next stage, keep the separation clear:

1. **`main.py` = what measurement to perform** — procedures, sequencing, parameters, and analysis/output.
2. **`mxa.py` = how to control the MXA** — reusable instrument operations and SCPI details.
3. **Other instrument modules = hardware-specific drivers** — shutter/power supply, wavemeter, oscilloscope, etc.

As the number of measurement procedures grows, we should consider moving individual procedures and data/plotting helpers out of `main.py` into dedicated modules. The first refactor should be conservative: preserve the existing measurement behavior while reducing orchestration-file size and keeping hardware commands behind instrument APIs.

## Notes for `moonshine`

This branch is the workspace for extending the measurement procedures and the Keysight MXA interface. Before a larger refactor, we should also clean up package imports (`from base import ...`, etc.), clarify the device abstraction, and add a small test/mock layer so measurement logic can be exercised without connected instruments.
