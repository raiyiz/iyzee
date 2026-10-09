# iyzee

**Lab instrument control and noise measurement for Python.**

iyzee connects laboratory hardware to a small Python API, reproducible measurement procedures, and an interactive terminal UI. It is built around a Keysight MXA, with support for a LeCroy oscilloscope, power supply/shutter, and wavemeter. Hardware control and measurement logic is usable from scripts and the IPython console; the TUI organizes them for interactive work.

[![CI](https://github.com/raiyiz/iyzee/actions/workflows/ci.yml/badge.svg)](https://github.com/raiyiz/iyzee/actions/workflows/ci.yml)

## Start here

### Install

Requires **Python 3.14+** and [uv](https://docs.astral.sh/uv/).

~~~sh
uv sync
~~~

### Run the interactive lab

~~~sh
uv run iyz
~~~

The TUI is the way to run interactive measurements:

~~~text
┌─────────┬──────────────────────────────────────────────┐
│ Connect │  connect / disconnect instruments            │
│ Sweep   │  configure and run MXA measurements          │
│ Scope   │  configure and acquire oscilloscope traces   │
│ Rb      │  browse rubidium D1/D2 transitions           │
│ Results │  inspect and export saved measurements       │
│ Console │  work directly with the live Python objects  │
│ Log     │  inspect application logs                    │
└─────────┴──────────────────────────────────────────────┘
~~~

Navigate with the letter keys (<code>c s o r t i l</code>) or the navigation rail. <code>F1</code>–<code>F4</code> provide quick access to Connect, Sweep, Results, and Console.

### Use it as a library

There is no separate command-line script: you use either the TUI or the package directly. Everything the TUI does goes through plain functions that import no Textual and are safe to call from your own script or notebook:

~~~python
from iyzee.devices.mxa import KeysightMXA
from iyzee.experiment import create_dirs, run_bandwidth_sweep, save_step_results

with KeysightMXA() as mx:  # connects on entry, closes on exit
    results = run_bandwidth_sweep(mx)  # a list of StepResult, one per RBW

save_step_results(results, create_dirs())  # the same .npz + .json pair the TUI writes
~~~

The [guide](docs/chapters/tui-and-devices.typ) compares the two ways of working, and `docs/examples/` has scripts for loading and checking saved recordings.

---

## The workflow

A typical measurement has a deliberately small number of layers:

~~~mermaid
flowchart LR
    U["TUI / script / IPython"] --> L["Lab session"]
    L --> P["Experiment procedures"]
    P --> D["Device drivers"]
    D --> H["Real hardware"]
    P --> R["Recorded results"]
    R --> T["Results / plots"]
~~~

**The important boundary:** UI code composes operations; it does not own the instrument protocol. Device drivers expose reusable hardware operations, while <code>experiment/</code> contains measurement procedures and result handling.

For the architecture and the reasoning behind this split, see the [architecture chapter](docs/chapters/architecture.typ).

## Instruments

| Instrument | Interface | What iyzee uses it for |
| --- | --- | --- |
| **Keysight MXA** | VISA / SCPI | noise spectra and automated sweeps |
| **LeCroy scope** | VISA / VXI-11 | waveform acquisition and channel/trigger control |
| **Power supply / shutter** | VISA | supply control and optical shutter |
| **Wavemeter** | HTTP | frequency readout and PID setpoints |

Hardware is **not** connected merely by constructing a driver. Connections are explicit and owned by the application/session, which keeps experiment code testable and makes failure handling visible.

## TUI at a glance

| Page | Purpose |
| --- | --- |
| <code>c</code> **Connect** | Connect and disconnect individual instruments |
| <code>s</code> **Sweep** | Run bandwidth or frequency sweeps with live progress and traces |
| <code>o</code> **Scope** | Synchronize scope settings, apply changes, acquire and save waveforms |
| <code>r</code> **Rb** | Inspect the rubidium D1/D2 hyperfine transition tables |
| <code>t</code> **Results** | Browse saved sweep/scope recordings and export scope plots |
| <code>i</code> **Console** | Use the live IPython session |
| <code>l</code> **Log** | Follow and inspect application logs |

### Command line

Inside the TUI, <code>:</code> opens a small command line. It is intentionally complementary to the navigation keys:

~~~text
:connect          :sweep          :scope
:results          :console        :log
:connect all      :disconnect scope
:status           :help           :quit
~~~

Page-specific commands are available where useful, for example <code>:run</code> on Sweep and <code>:acquire</code> on Scope. <code>:help</code> is the authoritative list.

### Keyboard

Outside text-entry widgets:

- <code>j</code> / <code>k</code> move focus.
- <code>Escape</code> leaves a text field.
- <code>Ctrl+\\</code> opens Textual's command palette.
- <code>Ctrl+Q</code> quits immediately; <code>q</code> quits after a second press.

The embedded console deliberately keeps normal terminal editing keys for IPython.

---

## The IPython console

Press <code>i</code> to work with the same connected instruments from a real IPython shell.

~~~python
lab.mx.set_center_freq(1.5e6)
lab.mx.set_rbw(24e3)
lab.mx.single_sweep_wait()

trace = lab.mx.get_trace_data(1)

lab.connected
lab.results[-1].traces["squeezing"]

lab.wavemeter.read_frequency(4)
~~~

Use ordinary IPython discovery:

~~~python
lab.scope.<TAB>
lab.mx.set_rbw?
help(lab.wavemeter)
~~~

<code>lab</code> reflects the application's current connection state rather than caching stale instrument references. Calls to the same physical instrument are serialized so console activity cannot overlap with a screen worker using that instrument.

The console is an in-process IPython terminal, not a second control API. Completion, history, magics, inspection, top-level <code>await</code>, and <code>%debug</code> are provided by IPython itself.

---

## Configuration

Instrument addresses and the data directory can be overridden without changing source code.

Environment variables:

~~~text
IYZEE_SCOPE_IP
IYZEE_NOISE_ANALYZER_IP
IYZEE_POWER_SUPPLY_IP
IYZEE_WAVEMETER_IP
IYZEE_DATA_DIR
~~~

Or use <code>config.toml</code> in the user config directory (or set <code>IYZEE_CONFIG</code>):

~~~toml
[addresses]
scope = "10.140.1.221"

[paths]
data = "/srv/iyzee-data"
~~~

A present but invalid configuration file is an error; iyzee does not silently fall back to defaults.

## Data

Measurements are written below <code>data/YYYY-MM/</code> by default, as a `.npz` (numbers) plus a `.json` manifest (meaning and provenance). The [Data & analysis](docs/chapters/data-and-analysis.typ) part describes every field.

Sweep results contain the acquired traces together with the analyzer/scan metadata needed to interpret each point. Scope acquisitions store numeric waveform data plus a JSON manifest containing the requested and read-back configuration, instrument identity, calibration and derived quantities.

The Results page can browse these recordings without reconnecting to the instrument that produced them.

---

## Documentation

The README is the **quick-start and orientation layer**. The technical guide goes deeper, is compiled to PDF in CI, and is wired to the code in both directions.

| Part | Read it to |
| --- | --- |
| [Architecture](docs/chapters/architecture.typ) | understand ownership, locking, layering, the scope transport and why the code is shaped this way |
| [MXA & measurements](docs/chapters/mxa-and-measurements.typ) | know what the analyzer is programmed to do: RBW/VBW, averaging, zero span, the two standard sweeps |
| [TUI & devices](docs/chapters/tui-and-devices.typ) | connect and debug instruments, learn the pages, keys and console |
| [Data & analysis](docs/chapters/data-and-analysis.typ) | know what every saved file contains, load it, and judge whether to trust it |
| [Rubidium physics](docs/chapters/rubidium-physics.typ) | follow the D1/D2 structure, spectroscopy and polarization self-rotation |
| [Code map](docs/chapters/code-map.typ) | find where any module is explained (generated from the code) |

**How the guide and the code stay in step.** The guide never hard-codes a line number, a default or a file layout. `scripts/docs_index.py` reads the code and writes an index that the guide queries while it is built: `#code("Lab.connect")` links to the exact lines of the commit being built, sweep presets and addresses come from the imported code, and the data dictionary is rendered from files written by the real save functions. A renamed function, an undocumented manifest field or an import that points up the layers fails the build instead of rotting. Module docstrings point back at the guide with `:guide:` labels, and `docs/examples/*.py`, the scripts the guide prints, are run by the test suite.

The compiled guide is published by CI as the <code>iyzee-documentation</code> artifact.

---

## Development

~~~sh
uv sync

scripts/ci.sh test        # pytest
scripts/ci.sh lint        # ruff
scripts/ci.sh typecheck   # mypy
scripts/ci.sh doc-links   # guide <-> code references still resolve
scripts/ci.sh docs        # build the code index and compile the Typst guide
scripts/ci.sh all         # run everything
~~~

GitHub Actions and GitLab use the same <code>scripts/ci.sh</code> targets.

The codebase is intentionally layered:

~~~text
src/iyzee/
├── devices/             hardware drivers
├── experiment/          measurement procedures + result handling
├── analysis.py          what a saved sweep or scope recording means
├── lab.py               connected-instrument session
├── scope_workflows.py   reusable scope operations
└── tui/                 interactive presentation/control
~~~

When adding functionality, prefer putting reusable instrument or measurement machinery below <code>tui/</code>. A screen should validate input, call the underlying operation, and display the result.

## Safety

This software controls real laboratory hardware.

- Treat instrument commands and setpoints as real hardware operations.
- Communication failures must never become plausible measurement values.
- The console has direct write access to connected instruments; use it like direct Python hardware control.
- Understand the current experiment state before changing a device setting.

---

**For the quick path:** <code>uv sync</code> → <code>uv run iyz</code> → **Connect** → **Sweep / Scope / Console**.
