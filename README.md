# iyzee

Small Python control and measurement toolkit for a lab setup, centered on
automated noise measurements with a Keysight MXA. Two ways to run a
measurement: a scripted CLI entry point (`iyzee`), and an interactive
terminal UI with a live console (`iyzee-tui`).

## Layout

```text
src/iyzee/
├── main.py               # CLI entry point: own resources, run a procedure, plot, save
├── config.py              # instrument addresses + data directory (env var / config.toml overrides)
├── lab.py                 # Lab: the connected instruments of a session (connect, disconnect, dead
│                          # links, close-all) + the INSTRUMENTS registry; no Textual, shared by TUI/console/scripts
├── devices/               # everything that talks to hardware
│   ├── base.py             # shared VISA lifecycle (BaseDevice), PSU channels
│   ├── mxa.py              # Keysight MXA SCPI/VISA driver
│   ├── power.py            # power supply + optical shutter control
│   ├── vicp.py             # VICP framing over TCP: thread-safe transport that drops the connection
│   │                       # after any mid-message failure (no request IDs, so a late reply would desync)
│   ├── scope.py            # LeCroy oscilloscope: waveform download + channel/trigger/math control
│   ├── wavemeter.py        # wavemeter HTTP client: readout, PID setpoint
│   └── handles.py          # uniform connect/disconnect/probe/lock adapter per device, LockedProxy
├── scope_workflows.py     # scope operations + durable waveform recordings — plain
│                          # functions/dataclasses on top of devices/scope.py, no Textual; see
│                          # "Design direction" below
├── experiment/            # composable measurement procedures
│   ├── core.py             # Step protocol, ExperimentContext, StepResult, run_sequence()
│   ├── procedures.py      # AnalyzerConfig, prepare_analyzer(), acquire_trace(), BandwidthStep, FrequencyStep, run_*_sweep()
│   └── io.py                # DATA_ROOT (<project root>/data), create_dirs(), save_step_results(), build_figure(), multiplot()
└── tui/                    # interactive terminal UI (`iyzee-tui`)
    ├── app.py              # IyzeeApp: nav rail, page switcher, key bindings, shared state, shutdown
    ├── app.tcss            # layout and responsive rules (width breakpoints)
    ├── ipython.py          # the `lab` namespace (LabProxy), shell configuration, history file location
    ├── ipython_session.py  # IPython's terminal shell, running in-process on a virtual terminal
    ├── logging_support.py  # captures the app's own logging: session buffer + rotating file history
    ├── vterm.py            # terminal screen model (pyte) with scrollback
    ├── termkeys.py         # Textual key events -> terminal input bytes
    ├── terminal_view.py    # widget that shows the virtual terminal and types into it
    ├── plotting.py         # plotext drawing shared by the Sweep, Scope, Results and Console pages
    ├── text.py             # showing externally produced text safely (markup-safe)
    ├── workers.py          # LastRun: the sweep result handed to the console
    └── screens/            # the seven pages
        ├── page.py         # shared page base, form helpers/validation, readiness hook, and worker→UI plumbing
        ├── connect.py      # ConnectScreen
        ├── sweep.py        # SweepScreen
        ├── scope.py        # ScopeScreen — form/plot only; operations live in scope_workflows.py
        ├── rb.py           # RbScreen: rubidium D1/D2 transitions (stick plots + table)
        ├── results.py      # ResultsScreen — browse saved Sweep/Scope runs, derive/export waveforms
        ├── console.py      # ConsoleScreen + IyzeeConsole: the page around IPython's terminal UI
        └── log.py          # LogScreen: the app's own logging, live and browsable
```

## Running the TUI

```sh
uv sync
uv run iyzee-tui
```

Seven pages cover the common tasks (the classes keep their `*Screen` names, but
they are plain container widgets inside one `ContentSwitcher`, not Textual
`Screen`s). Switch between them with `c` / `s` / `o` / `r` / `t` / `i` / `l`,
`F1`–`F4` (Connect/Sweep/Results/Console only — see below), or by clicking the
nav rail; `Ctrl+Q` quits at once, `q` quits after a second press (a running cell is
interrupted and connected instruments are disconnected on the way out):

**Command mode.** `:` opens a vim-style command line over the footer (not in
text fields or the console, where it is just a character); `Enter` runs,
`Esc` cancels, `Up`/`Down` recall history, `Tab` completes. Commands match by
unique prefix (`:conn` is `:connect`) and `:help` lists what the current page
offers:

| Command | Does |
| --- | --- |
| `:connect`, `:sweep`, `:scope`, `:results`, `:console`, `:log` | go to a page (`:c` `:s` `:o` `:t` `:i` `:l` for short) |
| `:connect scope mxa` / `:connect all`, `:disconnect scope` | connect or disconnect instruments |
| `:status`, `:help [cmd]`, `:quit` (`:q`) | connected instruments, command list, quit now |
| Scope page: `:retrieve`, `:apply [channels\|trigger]`, `:acquire`, `:tdiv 2u` | what its buttons do; `:tdiv` sets time/div (`2u`, `500n`, `1.5m`, `1e-6`) and applies it |
| Sweep page: `:run`, `:abort`, `:capture` | the sweep buttons |
| Results page: `:refresh`, `:latest`, `:width 40` | re-scan, select the newest run, list width in percent |

A command whose button is disabled (not connected, busy, nothing to apply) says
so instead of doing nothing. New commands are one `Command(...)` in the page's
`commands()` method (or `IyzeeApp._global_commands`); the parsing and
completion live in `tui/commands.py`, free of Textual.

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
- **Scope** (`o`) — connect to the LeCroy and the page automatically retrieves
  its current channel/trigger state once. The page shows whether the form is
  synchronized, highlights local edits, and enables each Apply action only when
  there is a delta to send. Apply writes only changed fields; *Retrieve current
  settings* re-syncs after a front-panel change. **Acquire & save** is available
  only for a synchronized, clean state and records enabled-channel waveforms
  under `data/YYYY-MM/` as a numeric `.npz` plus JSON manifest.
- **Rb** (`r`) — the rubidium D1/D2 hyperfine transitions that
  `devices.wavemeter.Rb_transitions` references the wavemeter to: one stick plot
  per D line (both isotopes, on a shared GHz axis) over a table of absolute
  frequencies. Highlighting a row marks that transition in the plots. Line
  positions only — no strengths or broadening — and no instrument is needed.
- **Results** (`t`) — browse previously recorded `.npz` runs on disk (sweeps
  and scope acquisitions); the preview follows the highlighted run, and scope
  runs add channel selection, waveform operations and PNG export.
- **Console** (`i`) — IPython's own terminal UI, in the app process, with live
  access to connected instruments and the last sweep's results. See below.
- **Log** (`l`) — the app's own logging, live by default, with a level
  filter and a way to browse older rotated log files.

`F1`–`F4` reach only Connect/Sweep/Results/Console — Textual's own key
handling reserves those four specifically to escape the console's embedded
terminal (see the comment on `IyzeeApp.BINDINGS`); Scope, Rb and Log are
letter-only (`o`, `r`, `l`) to avoid extending that.

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

## How a measurement flows

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

- **`devices/mxa.py`** — hardware abstraction for the Keysight MXA. New MXA
  capabilities should be implemented here as reusable SCPI/VISA methods;
  code in `experiment/` should call those methods rather than contain raw
  SCPI strings.
- **`devices/power.py`** — PSU control plus `ShutterControl`, a thin wrapper that
  drives the optical shutter through one PSU channel.
- **`devices/scope.py`** — LeCroy oscilloscope driver (VICP protocol over TCP, framing in
  `devices/vicp.py`, which drops the connection after any mid-message failure). Not
  yet unified with `BaseDevice`'s connection lifecycle; treat as a standalone
  legacy driver. `scope_workflows.py` holds the operations built on top
  (channel/trigger settings, waveform acquisition) — see "Design direction".
- **`devices/wavemeter.py`** — `Wavemeter`, a thin HTTP client (frequency
  readout, PID setpoint), with module-level `read_frequency()` /
  `set_pid_setpoint()` for scripts.
- **`devices/base.py`** — shared infrastructure: `BaseDevice` (VISA connect/close/
  context-manager lifecycle) and `CH` (PSU channel IDs). `KeysightMXA` and
  `PSU` both build on `BaseDevice`.
- **`config.py`** — where things are: `IP` holds the default instrument
  addresses and `address(IP.SCOPE)` resolves the one to use; `data_root()` is
  where recordings and logs go. Override either without touching code: set
  `IYZEE_SCOPE_IP` / `IYZEE_NOISE_ANALYZER_IP` / `IYZEE_POWER_SUPPLY_IP` /
  `IYZEE_WAVEMETER_IP` / `IYZEE_DATA_DIR`, or write `config.toml` in the user
  config directory (or wherever `IYZEE_CONFIG` points):

  ```toml
  [addresses]
  scope = "10.140.1.221"

  [paths]
  data = "/srv/iyzee-data"
  ```

  A config file that exists but doesn't parse is an error, not a silent fallback.
- **`lab.py`** — `Lab` owns the connected instruments of a session:
  `lab.connect("scope")`, `lab.device("scope")`, `lab.disconnect(...)`,
  `lab.close_all()`. The TUI (`app.lab`) and the console's `lab` use it, and so
  can a script.

## Interactive IPython console

The Console screen (`i`) embeds a real IPython shell in the same process as
the application, with IPython's own terminal UI (vi or emacs editing). Connected
instruments and the last sweep's results are reachable through a single `lab` object:

```python
lab.mx.set_center_freq(1.5e6)
lab.mx.set_rbw(24e3)
lab.mx.single_sweep_wait()
trace = lab.mx.get_trace_data(1)

from iyzee.scope_workflows import ChannelSettings, apply_channel_settings

apply_channel_settings(lab.scope, [ChannelSettings(Channel.C1, True, 0.5, 0.0, Coupling.DC_1M)])

lab.results[-1].traces["squeezing"]  # last completed sweep
lab.connected  # e.g. ("mx", "shutter")

lab.wavemeter.read_frequency(4)  # THz; stateless HTTP client, no connection to open
lab.wavemeter.last_seen  # UTC timestamp of the last successful HTTP response, or None
lab.api()  # what is connected and available
lab.api("scope", "trig")  # the scope's methods that mention "trig", with signatures
lab.api("wavemeter")  # method list, with the HTTP route each one calls
```

`Tab` completes methods on every instrument (`lab.scope.<Tab>`), and `?` shows a
method's signature and docstring (`lab.scope.set_time_per_div?`). `lab.api` is the
same information as one searchable listing, built by introspection so it is
never out of date.

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

Two technical guides, Typst source compiled to PDF in CI (GitHub Actions and
GitLab publish them as pipeline artifacts):

- [MXA and measurement guide](docs/mxa-and-measurements.typ): the measurement
  physics tied to analyzer state, SCPI commands and the Python implementation
  (RBW/VBW, detectors, ENBW, noise density, squeezing/shot-noise workflow).
- [TUI and device interaction guide](docs/tui-and-devices.typ): how `iyzee-tui`
  and the `iyzee` script share the `experiment/` layer, per-instrument
  connection details and command references, and how the TUI is built
  (screens, instrument registry and locking, navigation, the console's `lab`).

[`docs/adr_0001_tui_vs_devices_separation.md`](docs/adr_0001_tui_vs_devices_separation.md)
records the architecture decision behind the layering below.

For a reproducible measurement, the relevant instrument state travels with the
data. Sweep records carry frequency/range and analyzer settings in
`StepResult.meta` and the JSON sidecar. Scope records carry the requested
configuration, what the scope reported after a read-back (not what was
requested), scope calibration and per-channel timebase, instrument identity
(`*IDN?`), whether a running acquisition was paused for the capture, raw
waveform codes when available, and derived statistics beside the arrays.

## Design direction

Python first, TUI second: the TUI organizes, displays and controls; it does not
contain machinery a script could reasonably need.

1. **`main.py` / `tui/app.py`**: resource ownership and composition for the
   script and interactive paths. Both sit on the same layers below.
2. **`experiment/procedures.py`**: what to measure (analyzer setup, scan
   parameters, sequencing).
3. **`experiment/{core,io}.py`**: the machinery procedures are built from
   (`Step` execution, saving, plotting).
4. **`devices/*`**: reusable, hardware-specific instrument control.
5. **`devices/base.py`, `config.py`, `lab.py`**: connection lifecycle, addresses, and the connected-instrument session.

**Screens display and control; they don't implement.** `scope_workflows.py` is
the template: `ScopeScreen` reads and validates the form, then calls a plain
function (`apply_channel_settings`, `apply_and_verify_channel_settings`,
`acquire_scope_recording`) that takes the driver directly and has no Textual
import. The same call works from a script, or from the console as
`apply_channel_settings(lab.scope, [...])`. Pass an `InstrumentHandle.lock` as
`lock=` to serialize against concurrent access; a script with a private
connection can omit it. For the scope the lock is the driver's own re-entrant
transaction lock, so passing it, even through `lab.scope`, cannot deadlock.
Use `apply_and_verify_channel_settings` when you need to know what the scope
actually holds afterwards. A new screen with real device-orchestration logic
should follow this shape from the start, in a module beside the driver it
operates on. Split `procedures.py` (and `tui/screens/`) further as they grow.

## Development

```sh
uv sync
scripts/ci.sh test        # pytest
scripts/ci.sh lint        # ruff check + format --check
scripts/ci.sh typecheck   # mypy src tests
scripts/ci.sh docs        # compile the Typst guides (needs typst)
scripts/ci.sh all         # everything, as the dependency-update workflow does
```

GitHub Actions (`.github/workflows/ci.yml`) and GitLab (`.gitlab-ci.yml`) run
the same `scripts/ci.sh` targets. `.pre-commit-config.yaml` runs the lint
target on commit.
