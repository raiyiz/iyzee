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
│ Power   │  supply channels, global output and shutter  │
│ Rb      │  browse rubidium D1/D2 transitions           │
│ Results │  inspect and export saved measurements       │
│ Console │  work directly with the live Python objects  │
│ Log     │  inspect application logs                    │
└─────────┴──────────────────────────────────────────────┘
~~~

Navigate with the letter keys (<code>c s o p r t i l</code>) or the navigation rail. <code>F1</code>–<code>F4</code> provide quick access to Connect, Sweep, Results, and Console.

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
| **Power supply (HMP4040)** | VISA (raw socket) | channel setpoints and outputs |
| **Shutter** | a channel of the supply | the optical shutter, opened around squeezing acquisitions |
| **Wavemeter** | HTTP | frequency readout and PID setpoints |

Hardware is **not** connected merely by constructing a driver. Connections are explicit and owned by the application/session, which keeps experiment code testable and makes failure handling visible.

## TUI at a glance

| Page | Purpose |
| --- | --- |
| <code>c</code> **Connect** | Connect and disconnect individual instruments |
| <code>s</code> **Sweep** | Run bandwidth or frequency sweeps with live progress and traces |
| <code>o</code> **Scope** | Synchronize scope settings, apply changes, acquire and save waveforms |
| <code>p</code> **Power** | Read the power supply and shutter; switch global output and channels, set voltage and current limit |
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

The console editor is Vim-modal and starts in NORMAL mode. Press <code>i</code> to insert code and <code>Escape</code> to return to NORMAL; <code>v</code>/<code>V</code> select characterwise/linewise visual regions. It supports common Vim motions and operators, text objects, counts, the single unnamed register, undo and redo. This is not full Vim: named registers, macros, marks, <code>:s///</code> substitution, folds and splits are not implemented. In INSERT mode, <code>Tab</code> requests IPython completion and, with no candidates, inserts spaces to the next four-column tab stop. <code>Shift+Enter</code> runs the cell; <code>Ctrl+Enter</code> is an alternate, <code>Ctrl+Space</code> explicitly requests completion, and <code>Ctrl+P</code>/<code>Ctrl+N</code> recalls executed cells. <code>Ctrl+C</code> is a best-effort interrupt.

---

## The IPython console

Press <code>i</code> to work with the same connected instruments in an in-process IPython session.

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
# Type lab.scope. in the editor and press Ctrl+Space to complete its members.
lab.mx.set_rbw?
help(lab.wavemeter)
~~~

Press <code>Ctrl+Space</code> to request IPython completion; when several matches exist they are listed in the output panel.

<code>lab</code> reflects the application's current connection state rather than caching stale instrument references. Calls to the same physical instrument are serialized so console activity cannot overlap with a screen worker using that instrument.

The console is an in-process IPython shell, not a second control API. Python state, magics, inspection and top-level <code>await</code> remain backed by IPython. <code>input()</code>, direct reads from <code>sys.stdin</code>, and interactive <code>%debug</code>/<code>%pdb</code> prompts fail fast because the cell editor has no terminal input stream.

Ctrl+C cannot reliably interrupt a Python call blocked in native code or instrument I/O. Driver calls use finite timeouts: the MXA VISA default is 5 s (its <code>*OPC?</code> wait can use a 30 s per-call timeout), the PSU and LeCroy scope VISA defaults are 10 s, and wavemeter HTTP reads/setpoint writes use 1 s/3 s. The driver constructors and per-call timeout parameters are the points to change those limits. On normal quit, iyzee requests an interrupt and waits up to 12 s for the cell. If it is still active, the quit is cancelled and the app stays open rather than disconnecting an instrument the cell may still be using. If the process is killed externally, hardware outputs may remain in their last commanded state; this is not a safe-shutdown guarantee.

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
| [Data & analysis](docs/chapters/data-and-analysis.typ) | understand recordings, compare runs in the notebook workbench, and assess data integrity |
| [Rubidium physics](docs/chapters/rubidium-physics.typ) | follow the D1/D2 structure, spectroscopy and polarization self-rotation |
| [Code map](docs/chapters/code-map.typ) | find where any module is explained (generated from the code) |

**How the guide and the code stay in step.** The guide never hard-codes a line number, a default or a file layout. `scripts/docs_index.py` reads the code and writes an index that the guide queries while it is built: `#code("Lab.connect")` links to the exact lines of the commit being built, sweep presets and addresses come from the imported code, and the data dictionary is rendered from files written by the real save functions. A renamed function, an undocumented manifest field or an import that points up the layers fails the build instead of rotting. Module docstrings point back at the guide with `:guide:` labels, and `docs/examples/*.py`, the scripts the guide prints, are run by the test suite.

The compiled guide is published by CI as the <code>iyzee-documentation</code> artifact.

---

## Development

~~~sh
uv sync

scripts/ci.sh test        # pytest (no instruments are touched)
scripts/ci.sh hardware    # tests/hardware against the real instruments, read-only
scripts/ci.sh lint        # ruff
scripts/ci.sh typecheck   # mypy
scripts/ci.sh doc-links   # guide <-> code references still resolve
scripts/ci.sh docs        # build the code index and compile the Typst guide
scripts/ci.sh all         # run everything
~~~

GitHub Actions and GitLab use the same <code>scripts/ci.sh</code> targets. <code>hardware</code> is deliberately not part of <code>all</code> or CI.

The notebook tools use the optional <code>notebooks</code> dependency group. <code>workbench.py</code> is the read-only analysis front end. It indexes JSON sidecars first and gives every recording a stable `#ref`. You filter the list (type, status, origin, date range, text), tick rows to preview them, and add them to a **working set** that stays visible in the sidebar and at the top of every section; every plot gives a run the same colour. From that set it provides a sweep scoreboard, standard sweep/scope comparisons, a point inspector (squeezing vs shot noise), raw arrays overlaid across runs, comparison against a reference run, a plot builder (colour, line style, panels, log axes, PNG/SVG/PDF export), groups with mean ± spread bands and an A-vs-B difference with standard errors, SHA-256 verification, and CSV exports. Working sets can be saved by name (in your iyzee config folder, never in the data directory) and reloaded with their groups. The same working set is an ordinary Python object (<code>notebooks/workset.py</code>), so `Workset.open("demo-data", kind="frequency", latest=3).scoreboard()` works in a script or IPython; <code>sweep_scope_analysis.py</code> is a short code-first example. <code>reproduce_frequency_sweep.py</code> is a separate hardware diagnostic that replays the frequency sweep with explicit confirmation; it changes the laser setpoint, opens/closes the shutter, and reconfigures the MXA. Close the TUI before running it, and use it only when you intend to operate the bench. The shared Polars analysis helpers live in <code>notebooks/toolbox.py</code>.

~~~sh
uv run --group notebooks marimo edit notebooks/workbench.py

# Hardware-driving diagnostic only; close the TUI first
uv run --group notebooks marimo edit notebooks/reproduce_frequency_sweep.py
~~~

Try the whole workflow without connecting to hardware:

~~~sh
uv run scripts/demo_data.py demo-data
IYZEE_DATA_DIR=demo-data uv run --group notebooks marimo edit notebooks/workbench.py
~~~

Synthetic runs are marked in the catalog; their signal shapes are for learning the interface, not scientific results. <code>scripts/ci.sh notebooks</code> runs the notebook-specific lint and analysis tests in the optional environment.

### Hardware tests

<code>tests/hardware/</code> checks the drivers and procedures against the real instruments. A plain <code>pytest</code> never runs them: they are skipped unless you pass <code>--hardware</code>, and everything in that directory is marked <code>hardware</code> automatically. Addresses come from the same <code>IYZEE_*_IP</code> / <code>config.toml</code> the app uses, and the first line of a run lists them.

~~~sh
pytest --hardware                          # read-only: identity, error queues, settings, readings
pytest --hardware --hardware-actuate       # also: configure and sweep the MXA, open the shutter, acquire on the scope
pytest --hardware -m "not scope"           # leave out an instrument that is not on the bench
pytest --hardware -m "mxa and not actuates"
~~~

- Tests that change instrument state are marked <code>actuates</code> and need <code>--hardware-actuate</code> as well. Each one restores what it changed (MXA settings, scope trigger mode) and the shutter is closed afterwards and confirmed closed by the supply. A shutter that is already open is left alone and its tests are skipped.
- An instrument that cannot be reached fails its tests; it is not skipped. Use <code>-m "not mxa"</code> (or <code>scope</code>, <code>shutter</code>, <code>wavemeter</code>) to leave one out.
- Opening the shutter lets light through. The wavemeter setpoint test also needs <code>IYZEE_TEST_LASER_SETPOINT_THZ</code> (a frequency that is safe to lock to, ideally where the laser already is); see <code>tests/hardware/test_hw_wavemeter.py</code> for the other knobs.

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
