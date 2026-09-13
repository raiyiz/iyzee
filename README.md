# iyzee

Small Python control and measurement toolkit for a lab setup, centered on
automated noise measurements with a Keysight MXA. Two ways to run a
measurement: a scripted CLI entry point (`iyzee`), and an interactive
terminal UI with a live console (`iyzee-tui`).

## Layout

```text
src/iyzee/
├── main.py               # CLI entry point: own resources, run a procedure, plot, save
├── base.py                # shared VISA lifecycle, instrument IPs, PSU channels
├── mxa.py                 # Keysight MXA SCPI/VISA driver
├── power.py               # power supply + optical shutter control
├── scope.py               # LeCroy oscilloscope communication
├── wavemeter_readout.py  # wavemeter / laser setpoint control
├── experiment/            # composable measurement procedures
│   ├── step.py            # Step protocol, ExperimentContext, StepResult
│   ├── runner.py          # run_sequence(): executes a list of Steps, with progress callback
│   ├── config.py          # AnalyzerConfig, prepare_analyzer(), acquire_trace()
│   ├── procedures.py      # BandwidthStep, FrequencyStep, run_*_sweep()
│   ├── persistence.py     # create_dirs(), save_data(), save_step_results()
│   └── plotting.py        # build_figure(), multiplot()
└── tui/                    # interactive terminal UI (`iyzee-tui`)
    ├── app.py              # IyzeeApp: screens, key dispatch, shared app state
    ├── instruments.py      # InstrumentSpec registry, LockedProxy
    ├── ipython.py          # embedded IPython shell, LabProxy
    ├── workers.py          # cross-thread message types (LastRun, StepProgress, ...)
    ├── navigation/         # NavigationPolicy: is a focused widget editable right now?
    ├── screens/            # ConnectScreen, SweepScreen, TracesScreen, ConsoleScreen
    └── widgets/            # IyzeeConsole, the console's Vim-mode text input
```

## Running the TUI

```sh
uv sync
uv run iyzee-tui
```

Four screens cover the common tasks; switch between them with `c` / `s` /
`t` / `i` (or click), or `q` / `Ctrl+Q` to quit:

- **Connect** (`c`) — one row per instrument (MXA, shutter/PSU, wavemeter,
  scope). Enter connects or disconnects the selected row.
- **Sweep** (`s`) — configure and run a bandwidth or frequency sweep, with
  a live progress bar and trace plot. Built directly on `Step`/
  `run_sequence()` — it doesn't duplicate anything from `experiment/`.
- **Traces** (`t`) — browse previously recorded `.npz` runs on disk.
- **Console** (`i`) — an embedded IPython shell with live access to
  connected instruments and the last sweep's results. See below.

Outside editable widgets, `j`/`k` move focus (Textual's own focus
traversal) and `:` opens Textual's built-in Command Palette — the app's
existing actions are exposed there without a second command parser or a
hand-rolled modal-state machine. A widget that's actually accepting text
input (an `Input`, the console's text area) keeps ownership of its own
keys, checked via `NavigationPolicy.is_insert()` rather than a separately
maintained "mode" that could drift out of sync with what's really
focused.

## How a measurement runs

A measurement is a sequence of `Step`s run against a shared, already-connected
`ExperimentContext`. The important ownership boundary is explicit — this is
what both the CLI script and the TUI's Sweep screen build on:

1. Something creates the required hardware objects and owns their lifecycle.
   `main.py` (the script path) enters `KeysightMXA` as a context manager, so
   the VISA resource is opened there and closed when the procedure finishes
   or raises. The TUI's Connect screen does the equivalent for interactive
   use — connect/disconnect is explicit there too, just user-driven instead
   of a `with` block.
2. A `run_*` procedure in `procedures.py` (or, for the TUI, `SweepScreen`
   composing the same lower-level pieces directly) receives those
   already-owned devices and configures them via `prepare_analyzer()`. It
   does not create, connect, or disconnect hardware.
3. A list of `Step`s (e.g. `BandwidthStep`, `FrequencyStep`) is built —
   each one describes a single reproducible measurement point.
4. `run_sequence()` runs each step in order, logging progress and either
   stopping on the first failure (`on_error="raise"`, the default) or
   skipping a bad point and continuing (`on_error="skip"`). An optional
   `on_step` callback fires after every step, success or failure — the TUI's
   live progress bar and trace plot are the only thing hooked into it;
   `run_sequence()` itself stays UI-agnostic and works exactly as before
   with no callback at all.
5. Each step returns a `StepResult`: the scan coordinate, its unit, the
   acquired traces, and metadata needed to reproduce that point (RBW/VBW,
   laser setpoint, etc.).
6. After hardware has been released, the results get plotted —
   `build_figure()` returns a `matplotlib` `Figure` without displaying it
   (what the TUI would use to export one), and `multiplot()` wraps that with
   `plt.show()` for the script path — and `save_step_results()` writes them
   to a compressed `.npz` archive with per-point metadata embedded alongside
   the data.

Constructing a device does not connect it. `BaseDevice` opens the VISA resource
when `connect()` is called, normally through the explicit `with device:`
boundary in the application entry point. This keeps hardware access out of
experiment construction and makes procedure tests independent of real
instruments.

Adding a new experiment means adding a new `Step` subclass and a factory
function in `procedures.py`, not writing a new hand-rolled loop. That's also
why `SweepScreen` doesn't need to change when a new sweep type is added — it
already just picks a `Step` list and runs it.

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

## Interactive IPython console

The Console screen (`i`) embeds a real IPython shell in the same process as
the application, with a Vim-mode text editor for input. Connected instruments
and the last sweep's results are reachable through a single `lab` object:

```python
lab.mx.set_center_freq(1.5e6)
lab.mx.set_rbw(24e3)
lab.mx.single_sweep_wait()
trace = lab.mx.get_trace_data(1)

lab.results[-1].traces["squeezing"]   # last completed sweep
lab.connected                          # e.g. ("mx", "shutter")
```

`lab` does a fresh lookup against the app's actual state on every attribute
access — nothing is copied into the console when an instrument connects, and
nothing needs cleaning up when it disconnects, because nothing is ever
cached. Ask for `lab.mx` after disconnecting the MXA and you get an
immediate, clear `AttributeError`, not a stale reference that fails
confusingly later. This also means a user's own `mx = 5` for a scratch
calculation never collides with anything — `lab` owns exactly one name in
the shell, not one per instrument.

Every instrument call made through `lab` (and every one a screen's own
background worker makes) is serialized per-instrument, so the console and,
say, a running sweep can't issue overlapping commands to the same physical
device from two threads at once.

The console uses IPython's own execution engine rather than a custom Python
parser — completion, inspection (`?` / `??`), magic commands, history, shell
commands, and top-level `await` all come from IPython itself. History is
scoped to the current console session (not IPython's normal cross-session
persistent history, which would mix in commands from an unrelated earlier
visit to this same long-running app — see `IyzeeIPython.history`'s
docstring for why that's a deliberate choice, not an oversight). Jedi
completion is disabled: it does static analysis and can't see through
`lab`'s dynamic attribute lookup, so `lab.<Tab>` would otherwise silently
return nothing.

Input editing uses [`textual-vim-textarea`](https://pypi.org/project/textual-vim-textarea/)
(pinned to an exact version — young, single-maintainer package) for real Vim
motions, operators, counts, and registers, on top of which the console adds
exactly two things: history on Up/Down at the top/bottom of a multiline cell
(only in INSERT mode — NORMAL mode's Up/Down stay pure cursor motion, so the
two mental models don't bleed into each other), and a two-stage Escape:

- **1st Escape** (from INSERT) — handled by the Vim editor itself,
  transitioning to NORMAL mode while staying focused on the console.
- **2nd Escape** (already NORMAL) — leaves the console entirely, handing
  focus back to the app's own navigation.

This mirrors how other tools resolve a modal editor nested inside modal
navigation (e.g. Neovim's terminal mode needs its own escape *out* of
terminal input before window/pane navigation applies) — a single Escape
can't mean both "leave insert mode" and "leave the widget" without breaking
one of them. Shift+Enter executes the current cell, Tab completes, and
Ctrl+P/Ctrl+N remain available as explicit history shortcuts alongside the
Up/Down-at-boundary behavior above.

Outside editable widgets, `j`/`k` use Textual's own focus traversal
(`focus_next()`/`focus_previous()` — not a hand-rolled traversal order) and
`:` opens Textual's built-in Command Palette, exposing the app's existing
actions without a second command parser or a separately maintained modal
state machine. Whether a key is "global navigation" or "text editing" is
decided by `NavigationPolicy.is_insert()` — a pure check of what's currently
focused, not a mode flag that has to be kept in sync with reality.

## Safety notes

This is laboratory/instrument-control software; a few rules matter more than
in typical application code:

- Never turn a hardware communication failure into a plausible measurement
  value (see `WavemeterReadoutError`, `KeysightMXA.wait_opc()`).
- Don't change instrument setpoints or SCPI behavior without understanding
  and testing the change — these drive real hardware.
- The interactive console intentionally has direct write access to connected
  devices through `lab`; use it with the same care as writing Python against
  the instrument drivers directly. Instrument calls are serialized against
  concurrent screen activity (see `instruments.LockedProxy`), but nothing
  stops you from issuing a command that's simply wrong for the current
  experiment state.

## Documentation

Two technical guides, both Typst source compiled to PDF in CI:

- The **MXA and measurement guide** connects the measurement physics to the
  analyzer state, SCPI commands, and Python implementation — the measurement
  chain, RBW/VBW, detector and averaging semantics, ENBW, synchronization,
  trace transfer, noise density and band power, analyzer noise cancellation,
  trigger timing, and the squeezing/shot-noise workflow.
- The **TUI and device interaction guide** covers how `iyzee-tui` and the
  `iyzee` script both build on the same `experiment/` layer, connection
  details and command/endpoint references for every instrument (including
  the PSU/shutter, scope, and wavemeter — not just the MXA), and how the
  TUI itself is put together: screens, the instrument registry and its
  per-instrument locking, the navigation model, and the embedded IPython
  console's `lab` object.

- [MXA and measurement guide](docs/mxa-and-measurements.typ) — source
- [TUI and device interaction guide](docs/tui-and-devices.typ) — source
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

1. **`main.py` / `tui/app.py` — application boundaries:** resource ownership
   and composition, for the script and interactive paths respectively. Both
   sit on top of the same `experiment/` and driver layers below rather than
   duplicating anything from them.
2. **`experiment/procedures.py` — what to measure:** concrete procedures,
   scan parameters, sequencing.
3. **`experiment/{step,runner,config,persistence,plotting}.py` — the
   machinery a procedure is built from:** the `Step` abstraction, execution,
   analyzer setup, saving, and plotting.
4. **`mxa.py` / `power.py` / `scope.py` / `wavemeter_readout.py` — how to
   control each instrument:** reusable, hardware-specific operations.
5. **`base.py` — shared infrastructure:** connection lifecycle, addresses,
   channel definitions.

As more procedures are added, split `procedures.py` further rather than
letting one file grow indefinitely. The same applies to `tui/screens/` as
more screens are added, and `tui/navigation/` if app-wide keyboard
navigation grows beyond what `NavigationPolicy` currently covers.

## Known gaps

- `scope.py`'s `LeCroy` driver is not integrated with `BaseDevice`'s
  connection lifecycle (no context-manager support, no injectable transport
  beyond the low-level socket helpers already covered by tests). The TUI's
  Connect screen and `lab.scope` both expose it regardless, but no `Step`
  type drives it yet — adding one is the natural next slice once it's
  `BaseDevice`-integrated.
- Aborting a running sweep (Sweep screen) stops after the current step
  finishes, not mid-step — fine for the MXA's quick per-point calls today,
  but worth revisiting if a future `Step` type has a long blocking call.
