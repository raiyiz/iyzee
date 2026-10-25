# iyzee

**Lab instrument control and noise measurement for Python.**

iyzee connects a Keysight MXA, a LeCroy oscilloscope, a power supply with an optical shutter, and a wavemeter to a small Python API, reproducible measurement procedures, and a terminal UI.

[![CI](https://github.com/raiyiz/iyzee/actions/workflows/ci.yml/badge.svg)](https://github.com/raiyiz/iyzee/actions/workflows/ci.yml)

## Start here

Requires **Python 3.14+** and [uv](https://docs.astral.sh/uv/).

~~~sh
uv sync
uv run iyz
~~~

| Page | Key | Purpose |
| --- | --- | --- |
| **Connect** | `c` / `F1` | Connect and disconnect individual instruments |
| **Sweep** | `s` / `F2` | Bandwidth or frequency sweeps with live progress and traces |
| **Scope** | `o` | Read and apply scope settings, acquire and save waveforms |
| **Power** | `p` | Supply channels, global output and the shutter |
| **Results** | `t` / `F3` | Browse saved sweep and scope recordings |
| **Log** | `l` | Application log |

`j` / `k` move focus, `Escape` leaves a text field, `Ctrl+Q` quits at once, `q` quits after a second press.

## Use it as a library

Everything the TUI does goes through plain functions that import no Textual:

~~~python
from iyzee.devices.mxa import KeysightMXA
from iyzee.experiment import create_dirs, run_bandwidth_sweep, save_step_results

with KeysightMXA() as mx:  # connects on entry, closes on exit
    results = run_bandwidth_sweep(mx)  # a list of StepResult, one per RBW

save_step_results(results, create_dirs())  # the same .npz + .json pair the TUI writes
~~~

For several instruments in one session, use `Lab` and take each handle's lock around calls:

~~~python
from iyzee.lab import Lab

lab = Lab()
for key in ("mxa", "shutter", "wavemeter", "scope"):
    lab.connect(key)  # the shutter connects the supply it belongs to
with lab["mxa"].lock:
    lab.device("mxa").single_sweep_wait()
lab.close_all()
~~~

## Layout

~~~text
src/iyzee/
├── devices/             hardware drivers and instrument handles
├── experiment/          measurement procedures + result handling
├── analysis.py          what a saved sweep or scope recording means
├── lab.py               connected-instrument session (no Textual)
├── scope_workflows.py   reusable scope operations
├── waveform_math.py     scope trace operations
└── tui/                 interactive presentation/control
~~~

UI code composes operations; it does not own the instrument protocol. Nothing below `tui/` imports Textual. A screen should validate input, call the underlying operation, and display the result.

| Instrument | Interface | Used for |
| --- | --- | --- |
| **Keysight MXA** | VISA / SCPI | noise spectra and automated sweeps |
| **LeCroy scope** | VISA / VXI-11 | waveform acquisition, channel/trigger control |
| **Power supply (HMP4040)** | VISA (raw socket) | channel setpoints and outputs |
| **Shutter** | a channel of the supply | opened only around squeezing acquisitions |
| **Wavemeter** | HTTP | frequency readout and PID setpoints |

Connections are explicit and owned by the session, never made by constructing a driver. Every handle owns one lock that serializes calls to its instrument; a run holds the locks of every instrument it needs for its whole duration.

## Configuration

Environment variables: `IYZEE_SCOPE_IP`, `IYZEE_NOISE_ANALYZER_IP`, `IYZEE_POWER_SUPPLY_IP`, `IYZEE_WAVEMETER_IP`, `IYZEE_DATA_DIR`.

Or `config.toml` in the user config directory (or set `IYZEE_CONFIG`):

~~~toml
[addresses]
scope = "10.140.1.221"

[paths]
data = "/srv/iyzee-data"
~~~

A present but invalid configuration file is an error; iyzee does not fall back to defaults.

## Data

Measurements are written below `data/YYYY-MM/` by default, as a `.npz` (numbers) plus a `.json` manifest (meaning and provenance). Sweep recordings hold the traces with the analyzer and scan metadata for each point, plus run id, configuration and outcome (including aborted and failed runs). Scope recordings hold waveform data plus the requested and read-back configuration and instrument identity. The Results page browses them without reconnecting.

## Development

~~~sh
scripts/ci.sh test        # pytest (no instruments are touched)
scripts/ci.sh lint        # ruff
scripts/ci.sh typecheck   # mypy
scripts/ci.sh all         # all three
scripts/ci.sh hardware    # tests/hardware against real instruments (never part of `all`)
~~~

### Hardware tests

`tests/hardware/` is skipped unless you pass `--hardware`; addresses come from the same `IYZEE_*_IP` / `config.toml` as the app.

~~~sh
pytest --hardware                          # read-only: identity, error queues, settings, readings
pytest --hardware --hardware-actuate       # also: sweep the MXA, open the shutter, acquire on the scope
pytest --hardware -m "not scope"           # leave out an instrument that is not on the bench
~~~

Tests that change instrument state are marked `actuates`, restore what they changed, and confirm the shutter is closed afterwards. An unreachable instrument fails its tests rather than skipping them. Opening the shutter lets light through. The wavemeter setpoint test needs `IYZEE_TEST_LASER_SETPOINT_THZ` (see `tests/hardware/test_hw_wavemeter.py`).

## Safety

This software controls real laboratory hardware. Treat instrument commands and setpoints as real operations, never let a communication failure become a plausible measurement value, and understand the experiment state before changing a device setting.
