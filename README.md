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
├── power.py                # power supply + optical shutter control
├── scope.py                # LeCroy oscilloscope communication
├── wavemeter_readout.py  # wavemeter / laser setpoint control
├── experiment/            # composable measurement procedures
│   ├── core.py             # Step protocol, ExperimentContext, StepResult, run_sequence()
│   ├── procedures.py      # AnalyzerConfig, prepare_analyzer(), acquire_trace(), BandwidthStep, FrequencyStep, run_*_sweep()
│   └── io.py                # create_dirs(), save_data(), save_step_results(), build_figure(), multiplot()
└── tui/                    # interactive terminal UI (`iyzee-tui`)
    ├── app.py              # IyzeeApp: nav rail, page switcher, key bindings, shared state, shutdown
    ├── app.tcss            # layout and responsive rules (width breakpoints)
    ├── instruments.py      # InstrumentSpec registry, LockedProxy
    ├── ipython.py          # the `lab` namespace (LabProxy), shell configuration, history file location
    ├── ipython_session.py  # IPython's terminal shell, running in-process on a virtual terminal
    ├── vterm.py            # terminal screen model (pyte) with scrollback
    ├── termkeys.py         # Textual key events -> terminal input bytes
    ├── terminal_view.py    # widget that shows the virtual terminal and types into it
    ├── plotting.py         # plotext drawing shared by the Sweep, Traces and Console pages
    ├── text.py             # showing externally produced text safely (markup-safe)
    ├── workers.py          # cross-thread message types (LastRun, StepProgress, ...)
    └── screens/            # the four pages
        ├── page.py         # Page: scrolling base class (content is never clipped)
        ├── connect.py      # ConnectScreen
        ├── sweep.py        # SweepScreen
        ├── traces.py       # TracesScreen
        └── console.py      # ConsoleScreen + IyzeeConsole: the page around IPython's terminal UI
```

## Running the TUI

```sh
uv sync
uv run iyzee-tui
```

Four pages cover the common tasks (the classes keep their `*Screen` names, but
they are plain container widgets inside one `ContentSwitcher`, not Textual
`Screen`s). Switch between them with `c` / `s` / `t` / `i`, `F1`–`F4`, or by
clicking the nav rail; `Ctrl+Q` quits (a running cell is interrupted and
connected instruments are disconnected on the way out):

- **Connect** (`c`) — one row per instrument (MXA, shutter/PSU, wavemeter,
  scope). Enter connects the selected row; on a connected row it asks for a
  second Enter to disconnect (and refuses while a sweep is running). The nav
  rail's instrument dots follow connections live.
- **Sweep** (`s`) — configure and run a bandwidth or frequency sweep, with
  a live progress bar and trace plot. Built directly on `Step`/
  `run_sequence()` — it doesn't duplicate anything from `experiment/`. A
  banner says which instrument still needs connecting, a bad field is marked
  and focused, and every point is saved to disk as it is measured, so an
  interrupted run keeps what it had.
- **Traces** (`t`) — browse previously recorded `.npz` runs on disk; the
  preview follows the highlighted run.
- **Console** (`i`) — IPython's own terminal UI, in the app process, with live
  access to connected instruments and the last sweep's results. See below.

**Keyboard.** Outside text-entry widgets, `j`/`k` move focus (Textual's own
`focus_next()`/`focus_previous()`) and `Escape` leaves a text field. `Ctrl+\`
opens Textual's built-in Command Palette, exposing the app's actions without a
second command parser (not `:` or `Ctrl+P` — IPython's history uses `Ctrl+P`).
Whether a key means "navigation" or "text entry" is decided by Textual itself:
a focused widget's own keys take priority over `IyzeeApp.BINDINGS`, so there is
no hand-maintained mode flag to drift out of sync — except for priority
bindings like the palette's, which are checked before the focus chain; see the
comment on `IyzeeApp.COMMAND_PALETTE_BINDING`. While the console's terminal has
focus it owns nearly every key: only `F1`–`F4`, `Ctrl+Q` and `Ctrl+\` reach the
app (see the console section).

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
the application, with IPython's own terminal UI (vi or emacs editing). Connected
instruments and the last sweep's results are reachable through a single `lab` object:

```python
lab.mx.set_center_freq(1.5e6)
lab.mx.set_rbw(24e3)
lab.mx.single_sweep_wait()
trace = lab.mx.get_trace_data(1)

lab.results[-1].traces["squeezing"]  # last completed sweep
lab.connected  # e.g. ("mx", "shutter")
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

Everything IPython offers comes from IPython itself rather than a
re-implementation: completion, inspection (`?`/`??`), magics, shell commands,
top-level `await`, `%debug`, and history — Up/Down and `Ctrl+R` recall commands,
and history-based auto-suggestions appear as you type. History is persistent
across runs for a real run of the app only (`iyzee-tui`'s entry point passes
`default_history_file()` as `IyzeeApp.console_history_file`); everything else —
every test in this codebase, and any direct construction — keeps it in
`:memory:`. That split matters: IPython's own default history file
(`~/.ipython/profile_default/history.sqlite`) is shared by every
`InteractiveShell` on the machine and caused a real test-suite hang, since a full
test run builds many shells that each register an `atexit` write against that one
ever-growing file. See the docstrings of `default_history_file` and
`shell_config` in `ipython.py`. Jedi completion is disabled: it does static
analysis and can't see through `lab`'s dynamic attribute lookup, so `lab.<Tab>`
would silently return nothing; IPython's own completer is instead allowed to look
through the proxies (`Completer.policy_overrides` in `shell_config`), so
`lab.mx.<Tab>` completes too.

**How it works.** The console page runs IPython's own terminal UI (prompt_toolkit's
prompt: editing, completion menu, history search, auto-suggestions, `%magics`,
`?` help, `%debug`) *inside the app process*, so `lab.mx` is the live instrument
rather than a copy across a process boundary. prompt_toolkit only needs a
"terminal" to read keystrokes from and write escape sequences to, so it is given
virtual ones (`tui/ipython_session.py`): keystrokes are encoded by
`tui/termkeys.py` and written to a pipe input, and its output is interpreted by a
terminal emulator (`tui/vterm.py`, built on `pyte`) and painted by
`tui/terminal_view.py`, with scrollback (mouse wheel or Shift+PageUp/Down). The
shell runs in its own thread; `print()` output is routed to the console by
thread, `input()` prompts on the virtual terminal, and Ctrl+C interrupts the
running cell. `IyzeeApp(console_editing_mode="vi" | "emacs")` (env
`IYZEE_EDITING_MODE`) chooses IPython's editing mode; the default is vi.

Because the terminal owns the keyboard, only the F-keys, Ctrl+Q and the command
palette (Ctrl+\) reach the app while it has focus — `c`/`s`/`t`/`i` are typing.
Everything else — Tab, Escape, Ctrl+P/N/R — is IPython's. Ctrl+Z is never
forwarded (IPython binds it to "suspend", which would stop the whole TUI).
Not supported: `getpass` (it opens `/dev/tty`), and output from threads the
cell itself starts goes to Textual's capture rather than the terminal.


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
   analyzer setup, scan parameters, sequencing.
3. **`experiment/{core,io}.py` — the machinery a procedure is built from:**
   the `Step` abstraction and execution (`core.py`), saving and plotting
   (`io.py`).
4. **`mxa.py` / `power.py` / `scope.py` / `wavemeter_readout.py` — how to
   control each instrument:** reusable, hardware-specific operations.
5. **`base.py` — shared infrastructure:** connection lifecycle, addresses,
   channel definitions.

As more procedures are added, split `procedures.py` further rather than
letting one file grow indefinitely. The same applies to `tui/screens/` as
more screens are added. Keyboard navigation itself doesn't need its own
module — it's Textual's native focus/binding-priority system end to end,
with nothing app-specific to maintain there.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests      # advisory in CI (the job is allowed to fail)
```

CI (`.github/workflows/ci.yml`) runs the tests, ruff and mypy, and compiles the
Typst guides; `.gitlab-ci.yml` compiles the guides too.

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
