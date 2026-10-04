// iyzee TUI and device interaction guide

#import "requirements.typ": *

#set document(
  title: "iyzee TUI and device interaction guide",
  author: "iyzee",
)

#set page(
  margin: (x: 2.2cm, y: 2cm),
  header: context [
    #set text(size: 8pt)
    #smallcaps[iyzee]
    #h(1fr)
    TUI & devices
  ],
  footer: context [
    #set text(size: 8pt)
    #h(1fr)
    #counter(page).display("1 / 1", both: true)
  ],
)

#set par(justify: true, leading: 0.55em)
#set heading(numbering: "1.")
#set text(size: 10pt)

#hero(
  "Software architecture + operator reference",
  "TUI and device interaction",
  "How iyzee connects real instruments, exposes them to the terminal UI and console, and keeps hardware operations reusable outside Textual."
)

#v(0.65em)

#callout(
  "Design invariant",
  [
    The TUI organizes, displays and controls. Device and experiment machinery
    remains ordinary Python, callable from scripts and the embedded console.
    This separation is the main architectural boundary of iyzee.
  ],
  tone: "result",
)

#v(0.6em)

*Status:* documentation of the current implementation. This guide explains how the terminal UI, the experiment layer, and the instrument drivers fit together, and gives a working reference for talking to each instrument directly. For MXA-specific SCPI detail and the measurement physics, see the companion #link("mxa-and-measurements.typ")[MXA and measurement guide].

#align(center)[#outline(title: [Contents], indent: 1.2em)]

#pagebreak()

= One foundation, two entry points

The script and interactive application share the same lower layers:

#diagram(
  ```mermaidflowchart LR
  A["iyzee script"] --> C["experiment layer"]
  B["iyzee-tui"] --> C
  D["embedded IPython"] --> E["Lab / live handles"]
  E --> C
  C --> F["device drivers"]
  F --> G["physical instruments"]```.text,
  caption: [Interactive presentation sits above reusable experiment and device operations.],
  width: 94%,
)

= Two entry points, one foundation

`iyzee` (a script, `main.py`) and `iyzee-tui` (an interactive terminal app, `tui/app.py`) are both callers of the same `experiment/` layer described in the top-level README — neither one duplicates measurement logic. The difference is entirely about *who owns the hardware lifecycle and how progress is observed*:

#table(
  columns: (1fr, 1.7fr, 1.7fr),
  stroke: 0.5pt,
  inset: 6pt,
  align: (left, left, left),
  [*Concern*], [`iyzee` (script)], [`iyzee-tui` (interactive)],
  [Connect hardware], [`with KeysightMXA() as mx:`], [Connect screen, one row per instrument, Enter to connect],
  [Configure + run a sweep], [`run_bandwidth_sweep(mx)` in `procedures.py`], [`SweepScreen` composes `AnalyzerConfig` / `prepare_analyzer()` / `run_sequence()` directly],
  [Progress], [log lines only], [live progress bar + trace plot via `run_sequence(on_step=...)`],
  [After the run], [`multiplot()` (blocking `plt.show()`)], [trace stays on screen; `build_figure()` if a static image is wanted],
  [Ad hoc device access], [write a new script], [the embedded IPython console, live in the same process],
)

Both paths build a list of `Step` objects and hand them to `run_sequence()`; nothing about `experiment/` needed to change to support the TUI, and nothing about the TUI needed to know how a `BandwidthStep` or `FrequencyStep` actually talks to the MXA.

= Device layer

The device layer is where software semantics meet real protocols: VISA,
raw SCPI sockets, LeCroy VICP, and the WS-7 HTTP interface. Each adapter keeps
hardware-specific command syntax out of the experiment and TUI layers.


== Connection lifecycle

`BaseDevice` (`base.py`) is the shared contract every VISA-based driver builds on:

- `connect()` opens the VISA resource once; a second call is a no-op if already connected.
- `close()` closes it if open, and is safe to call more than once.
- `__enter__`/`__exit__` just call `connect()`/`close()`, so `with KeysightMXA() as mx:` is the same thing spelled as a context manager.
- Constructing a device does *not* connect it — `KeysightMXA()` alone opens no socket. This is what keeps experiment code and tests independent of real hardware: a `Step` can be constructed and unit-tested without ever calling `connect()`.

`KeysightMXA` and `PSU` both subclass `BaseDevice` directly, over `TCPIP0::<ip>::inst0::INSTR` (MXA) or a raw `TCPIP::<ip>::5025::SOCKET` (the R&S HMP4040 PSU, which doesn't speak the standard VISA `INSTR` resource string). `scope.py`'s `LeCroy` driver predates `BaseDevice` and manages its own raw TCP socket instead (see @sec-scope below) — this is a known, intentional gap, not an oversight; see the README's Known Gaps section.

== Instrument addresses

#table(
  columns: (1.3fr, 1fr, 2fr),
  stroke: 0.5pt,
  inset: 6pt,
  align: (left, left, left),
  [*`IP` member*], [*Address*], [*Instrument*],
  [`NOISE_ANALYZER`], [`10.140.1.40`], [Keysight MXA signal analyzer],
  [`POWER_SUPPLY`], [`10.140.1.15`], [Rohde & Schwarz HMP4040 (shutter driver)],
  [`SCOPE`], [`10.140.1.220`], [LeCroy oscilloscope],
  [`WAVEMETER`], [`10.140.1.215`], [WS-7 wavemeter HTTP switch server],
)

These are lab-network fixtures, not configuration — they live as a `StrEnum` in `base.py` (`IP`) precisely so a driver's constructor default (`ip: IP = IP.NOISE_ANALYZER`, etc.) is self-documenting about which physical box it talks to.

== Keysight MXA

The MXA is by far the most-used instrument and has its own dedicated guide — see #link("mxa-and-measurements.typ")[MXA and measurement guide] for the full SCPI command map, RBW/VBW/detector semantics, and the squeezing/shot-noise measurement workflow. The short version: `mxa.py` wraps every operation (frequency, bandwidth, sweep control, trace transfer, markers, triggering) as a plain Python method that writes or queries one SCPI command — application code never contains a raw SCPI string.

== Power supply and optical shutter

`power.py`'s `PSU` drives an R&S HMP4040 over its raw SCPI-over-socket interface (port 5025, no `INSTR` VISA resource string):

#table(
  columns: (1.5fr, 1.8fr, 2fr),
  stroke: 0.5pt,
  inset: 5pt,
  align: (left, left, left),
  [*Method*], [*SCPI written*], [*Note*],
  [`set_voltage(v, ch)`], [`INST:NSEL <ch>` then `VOLT <v>`], [selects the channel by number, then sets its voltage],
  [`set_current(i, ch)`], [`INST:NSEL <ch>` then `CURR <i>`], [same selection form as `set_voltage`],
  [`enable_output(ch)`], [`INST OUT<ch>` then `OUTP:SEL 1`], [selects the channel by *name* (`OUT1`..`OUT4`), a different form from `INST:NSEL` above — both are valid HMP4040 syntax; this is an existing quirk in the driver, not a bug to "fix" without testing against real hardware],
  [`disable_output(ch)`], [`INST OUT<ch>` then `OUTP:SEL 0`], [],
  [`enable_global_output()`], [`OUTP:GEN 1`], [master output switch, independent of per-channel `OUTP:SEL`],
  [`disable_global_output()`], [`OUTP:GEN 0`], [],
)

`ShutterControl` (also in `power.py`) is a thin, purpose-built wrapper: its `__init__` connects the PSU immediately and sets the shutter's trigger voltage/current (*1.7 V, 10 mA* — hardcoded, matching the physical shutter's rated trigger levels, not a tunable setting), so by the time a `ShutterControl` object exists it's ready to `open()`/`close()`. This is why the TUI's `ShutterHandle` (see @sec-instruments) treats *constructing* a `ShutterControl` as the "connect" step rather than a separate call.

== LeCroy oscilloscope <sec-scope>

`scope.py`'s `LeCroy` driver talks LeCroy's VICP protocol directly over a raw TCP socket (port 1861) — it predates PyVISA in this codebase and has never been migrated onto `BaseDevice`. Every exchange follows the same shape: an 8-byte header (a flag byte, 3 reserved bytes, and a big-endian-on-the-wire 4-byte length) followed by that many bytes of payload; `vicp.recv_exact()` loops until the full payload has actually arrived, since a single `socket.recv()` is not guaranteed to return everything at once. VICP has no request IDs, so after any mid-message failure (timeout, peer close, bad header, interrupted transfer) a late reply would be handed to the next caller as if it answered that caller's question. `vicp.VICPTransport` therefore invalidates itself in that case: the socket is closed, `LeCroy.connected` becomes `False`, and the caller must reconnect. Errors raised after a response was read completely through EOI (a wrong block length, a non-ASCII reply) leave the stream aligned, so the connection stays usable. A binary waveform block is validated against its declared byte count, and a trailing line terminator can never be counted as sample data.

Typical commands sent via `LeCroy.send()`:

#table(
  columns: (1.6fr, 2.2fr),
  stroke: 0.5pt,
  inset: 5pt,
  align: (left, left),
  [*Command*], [*Purpose*],
  [`CFMT DEF9,BYTE,BIN` / `...,WORD,BIN`], [select 8-bit or 16-bit binary waveform transfer format],
  [`<channel>:WF? <block>`], [request waveform data — `block` is `DAT1` (raw acquisition) or `DAT2` (a processing result: FFT, extrema, ...)],
  [`CORD LO`], [byte order for word transfers (`<LSB><MSB>`)],
  [`<channel>:INSPECT? "VERTICAL_OFFSET"` / `"VERTICAL_GAIN"`], [scaling needed to convert raw words to volts, used by `getDataFloats()`],
  [`<channel>:INSPECT? "HORUNIT"` / `"HORIZ_OFFSET"` / `"HORIZ_INTERVAL"`], [reconstruct the time axis: `t[i] = HORIZ_INTERVAL * i + HORIZ_OFFSET`],
)

Because this driver isn't `BaseDevice`-integrated, the TUI's `ScopeHandle` (@sec-instruments) calls `LeCroy.connect(ip)`/`.disconnect()` directly rather than going through the usual `connect()`/`close()` contract (`connect()` raises if already connected and `disconnect()` is idempotent, rather than returning status codes), and `ScopeHandle.probe()` can only report that the TCP handshake succeeded; the instrument's `*IDN?` reply is instead captured, best-effort, in each saved recording.

Beyond waveform download, `LeCroy` also exposes channel (vertical), trigger, and math-function control — `set_volts_per_div()`, `set_coupling()`, `set_trace_display()`, `set_trigger_mode()`/`set_trigger_source()`/`set_trigger_level()`/`set_trigger_slope()`/`set_trigger_coupling()`, the horizontal scale `set_time_per_div()` / `get_time_per_div()` (`TIME_DIV`), and `set_math_equation()` with `set_math_difference()`/`set_math_average()`/`set_math_fft()` convenience wrappers around it. These build the same header-path command strings as the table above (e.g. `C1:COUPLING D50`, `TRIG_SELECT EDGE,SR,C1`, `F1:DEFINE EQN,'C1-C2'`), routed through `LeCroy.send()`; a `query()` helper (`send()` + `readAll()`, trimmed) backs the handful of getters. `Channel`, `MathChannel`, `Coupling`, `TriggerCoupling`, `TriggerSlope`, and `TriggerMode` are `StrEnum`s for the values that are fixed across the LeCroy family this driver targets; bandwidth-limit and math-equation strings stay plain `str` parameters because those vocabularies are genuinely model dependent (see the docstrings for specifics and their sourcing).

== Wavemeter

`devices/wavemeter.py` talks to a WS-7 wavemeter switch's small HTTP API rather than SCPI — there's no persistent connection to open or close. `Wavemeter` is a thin client with one method per endpoint (the module-level `read_frequency()` / `set_pid_setpoint()` are the same calls on a default client), and `last_seen` records the UTC time of the last successful HTTP response (`None` before the first one). `lab.wavemeter.<Tab>` in the console completes the API and `lab.api("wavemeter")` prints it with the HTTP routes; `get(path)` / `post(path, **form)` reach routes it doesn't wrap yet:

#table(
  columns: (1.4fr, 2.4fr),
  stroke: 0.5pt,
  inset: 5pt,
  align: (left, left),
  [*Endpoint*], [*Used by*],
  [`GET /api/{channel}/`], [`read_frequency()` (and `single_readout()`, which subtracts an optional reference) — current frequency (THz) on that channel],
  [`POST /api/set_pid/` (body `freq_thz=...&channel=...`)], [`set_pid_setpoint()` — set the PID lock setpoint; regulation itself is left off by design and must be enabled manually],
)

A failed or unparseable HTTP response raises `WavemeterReadoutError` rather than silently returning a plausible-looking frequency — deliberately, per the project's own safety rule (see the README's Safety notes): a communication failure must never masquerade as a measurement.

#diagram(
  ```mermaidflowchart TD
  A["user action"] --> B["page / command"]
  B --> C["validation + translation"]
  C --> D["plain Python workflow"]
  D --> E["instrument handle"]
  E --> F["driver"]
  F --> G["hardware"]
  G --> E
  E --> D
  D --> B```.text,
  caption: [A page translates interaction into reusable operations; it does not become the device driver.],
  width: 84%,
)

= TUI architecture <sec-instruments>

== Screens

Seven pages cover the common tasks (`tui/screens/`) — `ConnectScreen`, `SweepScreen`, `ScopeScreen`, `RbScreen` (the static rubidium D1/D2 reference), `ResultsScreen`, `ConsoleScreen` and `LogScreen`, plain container widgets held by one `ContentSwitcher` (they keep the `*Screen` names from before the sidebar shell replaced Textual's per-page `Screen`s).

The shared `tui/screens/page.py` base owns page mechanics that are not domain-specific: scrolling/focus behavior, labeled-field construction, the finite/positive numeric validators, validation marking, the safe worker-to-UI callback, and the default no-op `refresh_readiness()` hook. `IyzeeApp.instruments_changed()` refreshes all mounted `Page` instances through that hook rather than naming each connection-dependent screen. Keeping that plumbing in one place lets each screen retain only its own controls, domain operations, and result presentation. `IyzeeApp` (`tui/app.py`) owns which one is currently visible plus the state that has to survive switching between them (`handles`, `last_run`). It does *not* own per-instrument locks any more — see @sec-locking below.

- *ConnectScreen* — a `DataTable`, one row per `InstrumentSpec`. Pressing Enter connects or disconnects the selected row in a background thread (`@work(thread=True)`), since every device call is blocking I/O and must never run on the UI thread. Disconnecting asks for a second Enter (and is refused while a sweep runs), and a row that is still connecting ignores Enter, so a device is never opened twice.
- *SweepScreen* — picks a bandwidth or frequency sweep, builds the `Step` list and `AnalyzerConfig` from the on-screen fields, and calls `run_sequence(steps, ctx, on_step=...)` in a background thread, updating a progress bar and a live `textual-plotext` trace as each step completes. Every point is also written to disk as it arrives (one archive, overwritten atomically), so an interrupted run keeps what it had, and the page shows which instrument still needs connecting.
- *ScopeScreen* — form, settings synchronization, and durable capture: one panel per analog channel, a "Trigger & timebase" section (trigger fields plus time/div, which is acquisition-wide and so lives in `TriggerSettings.time_per_div`), a "Retrieve current settings" action, Apply actions, and an "Acquire & save" button. A newly connected scope is read once automatically; the page exposes synchronization state and highlights fields that differ from the known baseline. Apply actions only write changed fields, and a default UI value cannot overwrite an unrelated instrument setting. After a channel apply the scope is read back: the values it reports become the new baseline (so a V/div the scope rounded is shown as rounded, and logged), a setting the scope ignored is reported as an error with the user's edit kept, and only verified values are recorded as "applied". Acquisition is enabled only when the scope is fully synchronized and the form has no pending edits. The reusable read/apply/acquire/persistence operations live in `iyzee.scope_workflows`; the screen contains only TUI state handling, plotting, and reporting. See @sec-convention.
- *ResultsScreen* — lists and previews persisted Sweep and Scope recordings (one browser for both; scope runs add channel selection, derived waveforms and PNG export) from `create_dirs()`'s output directory. Sweep records use the existing `save_step_results()` schema; Scope records use `save_scope_acquisition()` with the same numeric-NPZ + JSON-manifest storage primitive.
- *ConsoleScreen* — hosts IPython's own terminal UI; see @sec-console.
- *LogScreen* — the app's own logging (`tui/logging_support.py`), live by default, with a level filter and a way to browse older rotated log files.

== Instrument registry and locking <sec-locking>

`devices/handles.py` is the single place that knows how to build a uniform `InstrumentHandle` (`connect()` / `disconnect()` / `probe()` / `.device`) around each heterogeneous driver — VISA (`_VisaHandle`, wrapping `KeysightMXA`), the PSU-backed shutter (`ShutterHandle`), the scope's raw socket (`ScopeHandle`), and the wavemeter's stateless HTTP calls (`WavemeterHandle`). `lab.py` holds the `InstrumentSpec` registry and `Lab`, which owns the connected handles (connect, disconnect, dead-link detection, close-all) for the TUI, the console and scripts alike; adding a new instrument means one handle and one `InstrumentSpec` — no screen code changes. Addresses and the data directory come from `config.py` (environment variables or a `config.toml`).

Each handle inherits `_LockedHandle`, which owns one re-entrant `threading.RLock` exposed by the `InstrumentHandle` protocol (`ScopeHandle` overrides it to return the `LeCroy` driver's own transaction lock, so the handle, the console proxy, workflow batches and the driver's multi-command transfers all share a single lock). The lock therefore stays on the handle rather than `IyzeeApp` keeping a separate `dict[str, threading.Lock]` alongside `handles`. `LockedProxy`, in the same module, wraps a live device so that *every method call* acquires a given lock for its duration — `ConnectScreen` and `SweepScreen` pass `handle.lock` when they hold it around their own hardware calls, and the console's `LabProxy` passes the same one. This is what stops a console command and a running sweep from issuing overlapping commands to the same physical instrument from two different threads at once. It's a coarse, call-level lock, not a queue — a long-running call (e.g. a slow sweep step) will make a concurrent caller wait for the whole call, not just contend briefly.

Because the lock lives on the handle, not on `IyzeeApp`, a handle built and connected entirely outside a running app — a script's own `ScopeHandle`, say — gets the same synchronization guarantee for free. This is what makes @sec-convention below possible: the extracted operation functions take the lock as an optional argument rather than reaching for an app-owned table that may not exist. Because the lock is re-entrant, passing it through a `LockedProxy` over the same handle does not deadlock, and scope batches also enter the driver's own `transaction_lock` so a batch is atomic even against a thread that never saw the explicit lock.

Handles that expose a live instrument use the same `.device` property regardless of whether the underlying driver is VISA, a PSU-backed shutter, or the scope socket. Console and screen code therefore use one adapter-facing property instead of reaching through instrument-specific aliases.

== Design convention: screens display and control, they don't implement <sec-convention>

A page's job is the form, the buttons, the plot, and reporting a result — not the operation itself. `iyzee.scope_workflows` is the template: it holds `ChannelSettings`/`TriggerSettings` (plain frozen dataclasses), read/apply helpers (including `apply_and_verify_channel_settings()`, which reads back and reports what the scope holds), `acquire_scope_recording()`, and `save_scope_acquisition()` (plain functions taking the driver directly, with no Textual import and an optional `lock=` parameter). When a baseline is supplied, the apply helpers are intentionally field-granular: channel writes compare each V/div, offset, coupling, and trace-display value; trigger writes compare mode, source, slope, coupling, level, and time/div, and the trigger mode is always written last so an arming mode never fires against a half-written configuration. If the connection is lost mid-batch the loops stop and mark the remaining channels as not attempted rather than reading a stale reply as their answer. Without a baseline, the legacy full-write behavior remains available to explicit standalone callers. `ScopeScreen` reads and validates the form, then calls these operations; the acquisition record is built first and persisted before the completed result is reported in the UI.

== Navigation

Key handling relies entirely on Textual's own focus and binding-priority system, with no app-specific policy layer on top: a focused widget's own bindings (an `Input`'s text-entry keys, the console terminal's own keys) are offered the key first, and it only falls through to `IyzeeApp.BINDINGS` if the widget doesn't handle it. Those are `c`/`s`/`o`/`r`/`t`/`i`/`l` (switch page; the page you are already on is not offered) and `F1`–`F4`, which reach only Connect/Sweep/Results/Console — Textual's own key handling reserves those four specifically to escape the console's embedded terminal, so Scope, Rb and Log are letter-only (`o`, `r`, `l`) rather than extending that set — plus `:` (vim-style command mode: page switching, `:connect scope`, and page-specific commands such as `:tdiv 2u`; see `tui/commands.py`), `Ctrl+Q` (quit at once) and `q` (quit on a second press within a few seconds, so a stray key can't close an app holding live instruments), `j`/`k` (move focus) and `Escape` (leave a text field). There is no hand-maintained "am I in insert mode" flag to keep in sync with what's actually focused — Textual's dispatch order is the single source of truth for that. `Ctrl+\` opens Textual's built-in Command Palette rather than a hand-rolled command bar or parser; it is a priority binding, checked before the focus chain, and is deliberately not `Ctrl+P`, which IPython's history recall uses.

#callout(
  "Concurrency invariant",
  [
    One physical instrument has one serialization boundary. Console calls,
    screen workers and workflow batches therefore cannot interleave protocol
    commands accidentally. Long-running hardware calls still occupy that
    boundary for their full duration.
  ],
  tone: "result",
)

= The console in practice <sec-console>

The Console screen (`i`) runs IPython's own terminal UI — prompt_toolkit's prompt, with its editing modes (vi by default; emacs via `IyzeeApp(console_editing_mode=...)` or `IYZEE_EDITING_MODE`), completion menu, history search, auto-suggestions, `%magics`, `?` help and `%debug` — inside the application process, so `lab.mx` is the live instrument rather than a copy across a process boundary. prompt_toolkit is a terminal application, so it is given virtual terminals (`tui/ipython_session.py`): keystrokes are encoded by `tui/termkeys.py` into a pipe input, and its output is interpreted by a `pyte`-based screen (`tui/vterm.py`) that `tui/terminal_view.py` paints, with scrollback. The shell runs in its own thread, so a slow cell can't freeze the UI: `print()` is routed to the console by thread, `input()` prompts on the virtual terminal, and Ctrl+C interrupts the running cell (or clears the line at a prompt). While the terminal has focus only `F1`–`F4`, `Ctrl+Q` and `Ctrl+\` reach the app; Ctrl+Z is never forwarded, because IPython binds it to "suspend", which would stop the whole TUI. See the top-level README for the known limits.

Connected instruments and the last sweep are reachable through one object, `lab` (`tui/ipython.py`'s `LabProxy`), set once when the console is created and never refreshed — every attribute access re-reads the app's actual current state:

```python
# connect the MXA and shutter on the Connect screen first, then:
lab.mx.set_center_freq(1.5e6)
lab.mx.set_rbw(24e3)
lab.mx.single_sweep_wait()
trace = lab.mx.get_trace_data(1)

lab.shutter.open()
lab.results[-1].traces["squeezing"]   # last completed sweep, if any
lab.connected                          # e.g. ("mx", "shutter")
```

`lab.mx` calls go through the same `LockedProxy` the Connect/Sweep screens use, so they're serialized against a running sweep automatically. Disconnect the MXA on the Connect screen and the very next `lab.mx` access raises a clear `AttributeError` — there is nothing cached to go stale, because nothing was ever copied out of `app.handles` in the first place.

= Scope waveform recording

The Scope page's *Acquire & save* is a measurement-recording operation, not just a plotting convenience. One click acquires the selected analog channels from the scope's `DAT1` waveform block and creates a new file pair under `data/YYYY-MM/` (a fresh random suffix means a normal acquisition never overwrites an earlier one):

- the `.npz` contains numeric arrays only — one `time_<channel>` array, one calibrated `value_<channel>` array, and, for the real LeCroy driver, the exact signed 16-bit `raw_<channel>` samples returned by the scope;
- the `.json` manifest identifies the recording (`measurement_id`, UTC start/end timestamps, software/Python versions, VICP address/port/timeout, and the instrument's `*IDN?` identity), records the requested TUI configuration and the configuration the scope reported after the last read-back, and describes every returned waveform; `schema_version` is 2;
- an `acquisition` block states whether a running acquisition (trigger mode AUTO or NORMAL) was stopped for the download and restored afterwards (`freeze=True`, the default of `acquire_scope_recording()`), the prior trigger mode, and any non-fatal warnings (could not freeze or restore, identity unavailable), so channels can be trusted to come from one capture; SINGLE and STOP are left alone, and `freeze=False` opts out;
- per-channel metadata (each channel's own timebase is read, not channel 1's) includes the scope-reported engineering unit, horizontal offset and sample interval, vertical gain and vertical offset, sample/finite counts, minimum/maximum value and sample/time index, peak-to-peak, RMS, standard deviation and maximum absolute value;
- partial acquisition is preserved: channels that fail are listed under `errors` while successfully returned channels are still saved;
- the NPZ is SHA-256 hashed after writing and the digest is recorded in the manifest, so a copied or archived data file can be checked for accidental modification.

The calibrated values are derived from the scope's own reported `VERTICAL_GAIN` and `VERTICAL_OFFSET` (equivalently, `values = gain * raw_codes - offset`); the TUI's V/div and offset fields are recorded as configuration provenance, not silently treated as instrument readback. The raw signed 16-bit arrays are the preserved sample data when available; each waveform also records its own horizontal setup (`time_unit`, `time_offset` = first sample relative to the trigger, `time_interval`), and the requested/applied trigger settings carry `time_per_div`; the Results page shows time/div, the time window, sample count, interval and sample rate, and both the Scope and Results plots scale the time axis to a readable unit (ns/µs/ms). The scalar statistics in the manifest are convenience summaries derived from those calibrated arrays. This distinction matters because the legacy LeCroy driver cannot currently verify every vertical/trigger setting after it is written.

The storage layer is shared with experiment sweeps through `experiment.io.save_numeric_recording()`, so atomic `.part` replacement, pickle-free numeric archives, JSON manifests and checksum handling remain one implementation rather than two persistence systems. The SHA-256 digest covers the completed NPZ itself; it lets a later analysis/archive step verify that the numeric payload has not changed since it was written.

= Typical session, start to finish

1. `uv run iyzee-tui`. The Connect screen is shown first.
2. Move to the MXA row, press Enter. `ConnectScreen._connect()` builds an `InstrumentSpec`'s handle in a background thread, calls `handle.connect()` (opens the VISA resource) then `handle.probe()` (`*IDN?`), and on success stores the handle in `app.handles["mxa"]`.
3. Press `s` for the Sweep screen. Pick bandwidth or frequency, adjust the RBW range (or laser offsets), press *Run sweep*. `SweepScreen._run()` holds `app.handles["mxa"].lock` for the whole run, calls `prepare_analyzer()`, then `run_sequence(steps, ctx, on_step=...)` — each step's result updates the progress bar and the live trace plot.
4. As each point completes it is saved via `save_step_results()` (one archive, overwritten atomically), and on completion the run is stashed on `app.last_run`.
5. Press `o` for Scope. Apply the channel and trigger settings you intend to use, then press *Acquire & save*. The selected `DAT1` waveforms are captured, written as the NPZ/JSON recording pair, and then plotted; a partial acquisition is still preserved with per-channel errors (channel, exception type and message) in the manifest. A running acquisition is paused for the download and resumed afterwards.
6. Press `t` for Results to browse the saved Sweep or Scope recordings, or `i` for the Console to inspect live instruments and analysis results.

= References

- Repository implementation: `src/iyzee/devices/base.py`, `src/iyzee/devices/power.py`, `src/iyzee/devices/scope.py`, `src/iyzee/scope_workflows.py`, `src/iyzee/devices/wavemeter.py`, `src/iyzee/tui/`, and the associated tests.
- #link("mxa-and-measurements.typ")[MXA and measurement guide] — MXA SCPI reference, measurement physics, and the squeezing/shot-noise workflow.
- Rohde & Schwarz, *HMP Series Power Supply User Manual*: `INST:NSEL`, `INST OUTn`, `OUTP:SEL`, `OUTP:GEN` selection and output semantics.
- LeCroy, *Remote Control Manual*: VICP protocol framing, `WF?`/`INSPECT?` waveform transfer, and (for the channel/trigger/math control added on top of that) the `<channel>:VOLT_DIV`/`OFFSET`/`COUPLING`/`ATTENUATION`/`BANDWIDTH_LIMIT`/`TRACE`/`INVERT_SET`, `TRIG_SELECT`/`TRIG_LEVEL`/`TRIG_SLOPE`/`TRIG_COUPLING`/`TRIG_MODE`/`TRIG_DELAY`, and `DEFINE EQN` command families.
- PyVISA documentation, resource strings and `query_binary_values()`: #link("https://pyvisa.readthedocs.io/en/1.10.0/api/resources.html")[PyVISA resources]
- Textual documentation, workers and focus: #link("https://textual.textualize.io/guide/workers/")[Workers guide]
- prompt_toolkit and pyte, which host IPython's terminal UI in the console: #link("https://python-prompt-toolkit.readthedocs.io/")[prompt_toolkit documentation], #link("https://pyte.readthedocs.io/")[pyte documentation]

= Maintenance rule

Keep this guide tied to the implementation. When a driver's commands, the instrument registry, a screen's behavior, or the console's namespace changes, update the corresponding section here in the same change — don't let this fade into a description of an earlier version of the code. Avoid documenting historical designs that no longer exist (an earlier revision of this codebase copied live objects into the console's namespace and had to track and clean them up again on disconnect; `lab`'s live-lookup design replaced that entirely, and this guide should never again describe the older mechanism as current).

*Always strive for improvement, always be humble.*
