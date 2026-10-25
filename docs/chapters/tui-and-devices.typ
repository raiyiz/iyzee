// iyzee TUI and device interaction guide
#import "requirements.typ": *

#part(
  "Software architecture + operator reference",
  "TUI and device interaction",
  "How iyzee connects real instruments, exposes them to the terminal UI and console, and keeps hardware operations reusable outside Textual.",
  id: "part-tui",
)

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

*Status:* documentation of the current implementation. This guide explains how the terminal UI, the experiment layer, and the instrument drivers fit together, and gives a working reference for talking to each instrument directly. For MXA-specific SCPI detail and the measurement physics, see the companion #link(<part-mxa>)[MXA and measurement guide].

= One foundation, two ways to work <sec-two-ways>

There is one command, `iyz`, which starts the terminal UI. Everything else is the package itself: you import it from your own script or notebook. Both ways use the same lower layers:

#diagram(
  ```mermaid
flowchart LR
  A["your script / notebook"] --> C["experiment layer"]
  B["iyz (TUI)"] --> C
  D["embedded IPython"] --> E["Lab / live handles"]
  E --> C
  C --> F["device drivers"]
  F --> G["physical instruments"]```.text,
  caption: [Interactive presentation sits above reusable experiment and device operations; a script uses the same operations directly.],
  width: 94%,
)

The TUI (`tui/app.py`) and a script that imports the library are both callers of the same `experiment/` layer, so neither duplicates measurement logic. The difference is entirely about *who owns the hardware lifecycle and how progress is observed*:

#table(
  columns: (1fr, 1.7fr, 1.7fr),
  stroke: 0.5pt + hairline,
  inset: 6pt,
  align: (left, left, left),
  [*Concern*], [TUI (`iyz`)], [Library (your script or notebook)],
  [Connect hardware], [Connect page, one row per instrument, Enter to connect], [`with KeysightMXA() as mx:`, or a #code("Lab") for several instruments],
  [Configure + run a sweep], [#code("SweepScreen") composes `AnalyzerConfig` / #code("prepare_analyzer") / #code("run_sequence") directly], [#code("run_bandwidth_sweep")`(mx)`, or build `Step` objects and call #code("run_sequence") yourself],
  [Progress], [live progress bar and trace plot via `run_sequence(on_step=...)`], [your own `on_step` callback, or none],
  [After the run], [trace stays on screen; the Results page browses it later], [#code("save_step_results"); #code("build_figure") for a static image or #code("multiplot") to show it],
  [Ad hoc device access], [the embedded IPython console, live in the same process], [your own Python session],
)

Both paths build the same sweep plan (`build_bandwidth_sweep()` / `build_frequency_sweep()`) and hand its steps to `run_sequence()`; the Sweep page asks the step type which instruments it `requires` rather than branching on the sweep's name; nothing about `experiment/` needed to change to support the TUI, and nothing about the TUI needed to know how a `BandwidthStep` or `FrequencyStep` actually talks to the MXA.

= Device layer <sec-devices>

The device layer is where software semantics meet real protocols. Four transports
are in use, and each adapter keeps hardware-specific syntax out of the experiment and
TUI layers.

#table(
  columns: (1.2fr, 1.5fr, 2.3fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  align: (left, left, left),
  [*Instrument*], [*Driver*], [*Transport*],
  [Keysight MXA], [#code("KeysightMXA")], [VISA, `TCPIP0::<ip>::inst0::INSTR`, SCPI],
  [R&S HMP4040 PSU], [#code("PSU")], [VISA raw socket, `TCPIP::<ip>::5025::SOCKET`],
  [Optical shutter (PSU channel 3)], [#code("ShutterControl")], [none of its own: a channel of the PSU],
  [LeCroy scope], [#code("LeCroy")], [VISA, `TCPIP0::<ip>::inst0::INSTR` (VXI-11)],
  [WS-7 wavemeter], [#code("Wavemeter")], [HTTP, port #fact("drivers.wavemeter_port")],
)

== Connection lifecycle <sec-lifecycle>

#code("BaseDevice") is the contract every VISA driver (MXA, PSU, scope) builds on:

- #code("BaseDevice.connect") opens the VISA resource once; a second call is a no-op.
- #code("BaseDevice.close") closes it if open and is safe to call twice.
- `__enter__`/`__exit__` call those two, so `with KeysightMXA() as mx:` is the same thing as a context manager.
- Constructing a device does *not* connect it. `KeysightMXA()` alone opens nothing, which keeps experiment code and tests independent of hardware.

The scope driver is a #code("BaseDevice") like the others (it used to carry its own raw
socket). It differs only where it must: no read termination, a larger read chunk, and
its own lock and error translation (@arch-scope-transport).

#anchors("BaseDevice", "BaseDevice.connect", "BaseDevice.close")

== Instrument addresses <sec-addresses>

Addresses are defaults that can be overridden without editing code; the precedence is
environment variable, then `config.toml`, then the default below (#code("config.address")). A config
file that exists but cannot be parsed raises #code("ConfigError") instead of being ignored.

#table(
  columns: (1.3fr, 1fr, 1.7fr, 2fr),
  stroke: 0.5pt + hairline,
  inset: 6pt,
  align: (left, left, left, left),
  [*`IP` member*], [*Default*], [*Environment variable*], [*Instrument*],
  [`NOISE_ANALYZER`], [#fact("addresses.noise_analyzer.default")], [#raw(fact("addresses.noise_analyzer.env"))], [Keysight MXA],
  [`POWER_SUPPLY`], [#fact("addresses.power_supply.default")], [#raw(fact("addresses.power_supply.env"))], [R&S HMP4040 (shutter)],
  [`SCOPE`], [#fact("addresses.scope.default")], [#raw(fact("addresses.scope.env"))], [LeCroy oscilloscope],
  [`WAVEMETER`], [#fact("addresses.wavemeter.default")], [#raw(fact("addresses.wavemeter.env"))], [WS-7 wavemeter server],
)

The data directory follows the same rule: #raw(fact("config.env_data_dir")), then `[paths] data`
in the config file, then `<checkout>/data` for a source checkout or a per-user data
directory for an installed package (#code("data_root")). `IYZEE_CONFIG` names an alternative
config file.

== Keysight MXA <sec-mxa-driver>

The MXA is the most-used instrument and has its own part (@part-mxa) with the SCPI map,
RBW/VBW/detector semantics and the measurement workflow. In short: #code("KeysightMXA") wraps
every operation as a plain method that writes or queries one SCPI command, with a
#fact-ms("drivers.mxa_timeout_ms") default timeout; application code contains no raw SCPI string.

== Power supply and optical shutter <sec-psu>

#code("PSU") drives an R&S HMP4040 over its raw SCPI-over-socket interface (port 5025):

#table(
  columns: (1.5fr, 1.8fr, 2fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  align: (left, left, left),
  [*Method*], [*SCPI written*], [*Note*],
  [`set_voltage(v, ch)`], [`INST:NSEL <ch>` then `VOLT <v>`], [selects the channel by number, then sets its voltage],
  [`set_current(i, ch)`], [`INST:NSEL <ch>` then `CURR <i>`], [same selection form as `set_voltage`],
  [`enable_output(ch)`], [`INST OUT<ch>` then `OUTP:SEL 1`], [selects the channel by *name* (`OUT1`..`OUT4`), a different form from `INST:NSEL`; both are valid HMP4040 syntax, and this quirk should not be "fixed" without testing on the instrument],
  [`disable_output(ch)`], [`INST OUT<ch>` then `OUTP:SEL 0`], [],
  [`enable_global_output()`], [`OUTP:GEN 1`], [master output switch, independent of per-channel `OUTP:SEL`],
  [`disable_global_output()`], [`OUTP:GEN 0`], [],
  [`retrieve_state(channels)`], [`OUTP:GEN?`, then per channel `INST:NSEL <ch>`, `VOLT?`, `CURR?`, `MEAS:VOLT?`, `MEAS:CURR?`, `OUTP:SEL?`], [the supply's own report as a #code("PSUState"): setpoints, measurements, what is armed, and the master output. Puts the channel selection back afterwards. A reply that is not a finite number (or 0/1 for a switch) raises `ValueError` naming the command],
)

A channel is powered only when it is armed (`OUTP:SEL`) *and* the master output is on, which is why
#code("PSUState") carries both and #code("PSUState.is_live") combines them. The socket has no message
framing, so #code("PSU.open") sets a read termination; without it a query would wait for the VISA timeout.

The PSU is the instrument; #code("ShutterControl") is a part of it: it *borrows* a #code("PSU") and uses one channel (3 by default,
`SHUTTER_CHANNEL_DEFAULT`). It neither opens nor closes the supply's connection and has no lock of its own. Like every other device
it sends nothing when constructed: #code("ShutterControl.configure") sets the channel to 1.7 V and 10 mA (the shutter's trigger
levels, hard-coded rather than configurable) and refuses if the PSU is not connected. #code("ShutterControl.is_open") asks the supply
whether the shutter's channel is powered instead of trusting what we last sent. From a script:

```python
with PSU() as psu:                 # connects on entry, closes on exit
    shutter = ShutterControl(psu)  # a channel of that PSU; nothing sent yet
    shutter.configure()
    shutter.open()
    ...
    shutter.close()                # close it yourself: leaving the block only closes the socket
```

In the lab session the same relationship is expressed in the registry (@sec-locking): the `shutter` spec names `psu` as its
`parent`.

== LeCroy oscilloscope <sec-scope>

#code("LeCroy") controls WaveSurfer/WaveAce/X-Stream scopes over *VISA (VXI-11)*. The scope's
remote control must be set to LXI/VXI-11, not VICP.

#callout(
  "Migration note",
  [
    Earlier versions spoke LeCroy's VICP protocol over a raw socket (port 1861). That driver
    and its transport are gone. If a scope that used to work now times out on connect,
    check its remote-control setting first; a scope still set to VICP will not answer VXI-11.
  ],
  tone: "warning",
)

The driver forwards LeCroy's header-path dialect; anything it does not wrap is one
#code("LeCroy.send") or #code("LeCroy.query") away. The commands it issues:

#table(
  columns: (1.7fr, 2.2fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  align: (left, left),
  [*Command*], [*Purpose*],
  [`CFMT DEF9,WORD,BIN`], [16-bit binary transfer in definite-length (`#9`) blocks; written before every `WF?`],
  [`CORD LO`], [little-endian word order; also written before every `WF?`],
  [`<channel>:WF? DAT1`], [request the waveform block (`DAT1` = the acquisition)],
  [`<channel>:INSPECT? "VERTICAL_GAIN"` / `"VERTICAL_OFFSET"` / `"VERTUNIT"`], [scaling and unit: `value = gain × code − offset`],
  [`<channel>:INSPECT? "HORUNIT"` / `"HORIZ_OFFSET"` / `"HORIZ_INTERVAL"`], [time axis: `t[i] = HORIZ_OFFSET + i × HORIZ_INTERVAL`],
  [`<channel>:VOLT_DIV`, `OFFSET`, `COUPLING`, `TRACE`], [vertical setup (setters and `?` getters)],
  [`TRIG_SELECT EDGE,SR,<source>`, `TRIG_MODE`, `<source>:TRIG_LEVEL` / `TRIG_SLOPE` / `TRIG_COUPLING`], [edge trigger on one source; the mode is written last (@sec-convention)],
  [`TIME_DIV`], [horizontal scale; acquisition-wide, so it lives with the trigger settings],
)

Only the Edge trigger type is wrapped; other types are a `scope.send("TRIG_SELECT ...")` away.
Enumerations cover the values fixed across the family: #code("Channel") (#fact-raw("scope_enums.Channel").values().join(", ")),
#code("Coupling") (#fact-raw("scope_enums.Coupling").values().join(", ")), #code("TriggerCoupling"), #code("TriggerSlope") and #code("TriggerMode")
(#fact-raw("scope_enums.TriggerMode").values().join(", ")). Getters return the instrument's raw reply; parsing
(`5.00E-01V` into volts, `TDIV 5.00E-06 S` into seconds) happens one layer up, in the workflows.

#code("ScopeHandle") adapts the driver to the common handle contract. Its `probe()` asks `*IDN?` with a
#fact("drivers.scope_first_response_s") s allowance, because the first reply after connecting can be slow and a
timeout drops the connection; if the scope accepts the connection but stays silent, the error says another client may be holding it.
Its lock *is* the driver's transaction lock, so there is only one lock to reason about.

#anchors("LeCroy", "LeCroy.connect", "LeCroy.query", "LeCroy.getDataFloatsDetailed", "ScopeHandle", "TriggerMode")

== Wavemeter <sec-wavemeter>

#code("Wavemeter") talks to the WS-7 switch server's small HTTP API. It is stateless: constructing it
connects to nothing, and every call is one `requests` call, so it can be used from the console
before the Connect page has probed it. Every request is bounded (#fact("drivers.wavemeter_read_timeout_s") s for reads,
#fact("drivers.wavemeter_setpoint_timeout_s") s for setpoints) because a sweep calls it while holding instrument
locks, and an unanswered request would freeze the run.

#table(
  columns: (1.4fr, 2.4fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  align: (left, left),
  [*Request*], [*Purpose*],
  [`GET /api/{channel}/`], [#code("Wavemeter.read_frequency") reads the frequency in THz (default channel #fact("drivers.wavemeter_default_channel"))],
  [`POST /api/set_pid/`], [#code("Wavemeter.set_pid_setpoint") sends `freq_thz` and `channel` as form data],
)

A failed HTTP response raises the exception from `raise_for_status()`, and an unparseable reply raises the
`ValueError` from `float()`. Nothing is translated, so the real failure stays visible; in a sweep a failed
wavemeter call fails that step instead of recording a plausible number. The reference transition table
(`Rb_transitions`, from Steck) lives in the same module and also feeds the Rb page and the physics part.

#diagram(
  ```mermaid
flowchart TD
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
  width: 94%,
)

= TUI architecture <sec-instruments>

== Pages <sec-pages>

Seven pages cover the common tasks. They are plain container widgets held by one `ContentSwitcher`, so the nav rail
can persist across switches.

#table(
  columns: (auto, auto, 1fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  [*Key*], [*Page*], [*Purpose*],
  [`c` / F1], [#code("ConnectScreen")], [one row per instrument; Enter connects or disconnects (in a worker thread)],
  [`s` / F2], [#code("SweepScreen")], [bandwidth or frequency sweep with live progress and per-point checkpointing; also "Capture trace"],
  [`o`], [#code("ScopeScreen")], [channel and trigger form, Retrieve / Apply / Acquire & save],
  [`p`], [#code("PowerScreen")], [the supply's four channels and the shutter, read back from the supply; global/channel/shutter switches and voltage/current setpoints],
  [`r`], [#code("RbScreen")], [static rubidium D1/D2 reference from `Rb_transitions`],
  [`t` / F3], [#code("ResultsScreen")], [browse saved sweep and scope recordings; derived waveforms and PNG export],
  [`i` / F4], [#code("ConsoleScreen")], [embedded IPython (@sec-console)],
  [`l`], [#code("LogScreen")], [live application log, level filter, rotated history],
)

The registry is #code("PAGE_SPECS"); the nav rail, the content switcher and the `:` commands all derive from it, so adding a page cannot leave them out of step. F1–F4 are global shortcuts for Connect/Sweep/Results/Console; Scope, Power, Rb and Log are reached through their letter bindings or the nav rail.

The shared #code("Page") base owns mechanics that are not domain-specific: scrolling without stealing initial focus, labeled fields, the finite/positive
validators, validation marking, the safe worker-to-UI callback #code("Page._ui"), and the no-op
#code("Page.refresh_readiness") hook that #code("IyzeeApp.instruments_changed") calls on every page.

*Sweep.* #code("SweepScreen") builds the `Step` list and `AnalyzerConfig` from its form, then runs #code("run_sequence") in a worker holding the MXA's lock
(and the shutter's, for a frequency sweep) for the whole run. It deliberately composes the building blocks instead of calling
`run_bandwidth_sweep`, because those wrappers take no progress callback. Every recorded point is written to disk immediately (one file pair,
replaced atomically), so an interrupted run keeps what it had; an abort takes effect at the next step boundary, since a step is one
averaging run and cannot be interrupted. Point counts are capped at #fact("tui.max_points") so a stray extra zero cannot freeze the UI.

*Scope.* #code("ScopeScreen") has three background operations. *Retrieve* reads the scope into the form (it also runs once on connect). *Apply* writes only what
differs from the last-reported state and then refreshes the form from the read-back, so a value the scope rounded or ignored is shown as it really is.
*Acquire & save* needs a successful Retrieve and a form with no pending edits, and writes the recording (@sec-scope-recording). The operations themselves live in
`scope_workflows` (@sec-convention).

== Instrument registry and locking <sec-locking>

#code("INSTRUMENTS") is the registry of #code("InstrumentSpec") rows:

#table(
  columns: (auto, 1fr, auto),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  [*Key*], [*Label*], [*Short*],
  ..range(5).map(i => ([`#fact("instruments." + str(i) + ".key")`], [#fact("instruments." + str(i) + ".label")], [#fact("instruments." + str(i) + ".short")])).flatten(),
)

#code("Lab") holds the connected handles and implements connect (build, open, probe, register; nothing is registered on failure and the half-open link is closed),
disconnect, dead-link detection and bounded close-all for the TUI, the console and scripts alike. Adding an instrument means one handle and one #code("InstrumentSpec"); no screen changes.

An instrument that is really a part of another names its `parent` in the spec; the shutter, a channel of the PSU, is the one case. Its handle is built from the
parent's handle and shares its link, its lock and its liveness, so everything that talks to the supply, a sweep holding the shutter included, is serialized on one lock.
Connecting the shutter connects the PSU first if it is not already (never twice: one socket per supply); disconnecting the PSU disconnects the shutter first, which closes it;
and #code("Lab.close_all") closes parts before parents, so the supply going away at exit never leaves the shutter open. Disconnecting only the shutter closes it and leaves the PSU connected.
The Power page's switches for the shutter's channel are disabled while the shutter is connected: the channel is the shutter's.

Each handle inherits #code("_LockedHandle"), which owns one re-entrant `threading.RLock`; `ScopeHandle` instead returns the driver's own transaction lock, so the handle, the console proxy, workflow batches and the driver's multi-command transfers share
a single lock. #code("LockedProxy") wraps a live device so every method call takes that lock; `ConnectScreen`, `SweepScreen` and the console's #code("LabProxy") all use the handle's lock. A concurrent console command and a running sweep therefore cannot interleave on one instrument. It is a coarse call-level lock, not a queue: a long call makes a concurrent caller wait for the whole call.

Because the lock lives on the handle, a handle built entirely outside the app (a script's own `ScopeHandle`) gets the same guarantee. Because it is re-entrant, passing a `LockedProxy` over a handle whose lock is already held does not deadlock.

#tested-by("test_locked_proxy_serializes_calls_across_threads", "test_drop_dead_links_unregisters_only_the_dead_ones_and_releases_them", "test_close_all_never_waits_longer_than_its_timeout_for_a_busy_instrument", "test_a_failed_connect_closes_the_half_open_link_and_registers_nothing", "test_scope_handle_identifies_the_scope_reports_a_silent_one_and_follows_its_state")

== Design convention: screens display and control, they don't implement <sec-convention>

A page's job is the form, the buttons, the plot and the report. `iyzee.scope_workflows` is the template: plain frozen dataclasses (#code("ChannelSettings"), #code("TriggerSettings")) and plain functions that take the driver directly and import no Textual:

#table(
  columns: (1.7fr, 3fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  [*Function*], [*What it guarantees*],
  [#code("read_channel_settings"), #code("read_trigger_settings")], [turn the scope's current state into the same dataclasses the apply functions take, so a caller can read, change one field and apply],
  [#code("apply_channel_settings")], [writes (only changed fields when given a baseline), then *returns the read-back*, which, not the request, is what was applied; refuses before writing if a channel has no baseline],
  [#code("apply_trigger_settings")], [field-granular against a baseline; the mode is written last so an arming mode never fires against a half-written configuration],
  [#code("acquire_scope_recording")], [freezes a running acquisition for a consistent multi-channel capture and restores it; raises on the first failure],
  [#code("save_scope_acquisition")], [persists the arrays and manifest through the shared recording primitive],
)

Every batch holds the driver's `transaction_lock`, so a screen worker, the console and a script can call them at the same time. `ScopeScreen` reads and validates the form, calls these, and persists the recording before reporting success. The parsing of replies (`_parse_volts`, `_parse_seconds`) follows LeCroy's remote-control manuals but, as the module docstring says, has not been exercised against every model; verify against your instrument before relying on it for anything safety-critical.

== Navigation and commands <sec-navigation>

Key handling relies on Textual's own focus and binding priority, with no app-specific "mode" flag: a focused widget's own bindings see the key first and it falls through to the app's bindings only if the widget does not handle it.

#table(
  columns: (auto, 1fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  [*Key*], [*Action*],
  [`c s o r t i l`], [switch page (the page you are on is not offered)],
  [`F1`–`F4`], [Connect / Sweep / Results / Console, even from inside the console],
  [`:`], [vim-style command line; `:help` lists what the current page offers],
  [`Ctrl+Q`], [request safe shutdown; cancel the quit if an active cell does not stop within 12 s],
  [`q`], [quit on a second press within #fact("tui.quit_confirm_s") s, so a stray key cannot close an app holding live instruments],
  [`j` / `k`, `Escape`], [move focus; Escape leaves ordinary form fields, while VimTextArea uses it to return to NORMAL],
  [`Ctrl+\\`], [Textual's command palette],
)

Commands (#code("CommandRegistry")) are parsed without Textual: an exact name or alias wins, otherwise a unique prefix does (`:conn`); a page's commands shadow the global ones. Values accept SI suffixes (`:tdiv 2u`; #code("parse_si")).

The layout reflows at #fact("tui.breakpoints.1.0") and #fact("tui.breakpoints.2.0") columns (#code("IyzeeApp.HORIZONTAL_BREAKPOINTS")), declaratively in `app.tcss`.

#callout(
  "Concurrency invariant",
  [
    One physical instrument has one serialization boundary. Console calls, screen workers and workflow batches cannot interleave protocol commands accidentally. Long-running calls still occupy that boundary for their full duration.
  ],
  tone: "result",
)

= The console in practice <sec-console>

The Console page presents a persistent in-process IPython shell through the package's VimTextArea editor and a Textual output log. It starts in Vim NORMAL mode; press `i` to type in INSERT mode, `Escape` to return to NORMAL, and `v` / `V` for characterwise / linewise visual selection. Common motions, operators, text objects, counts, the single unnamed register, undo and redo are provided by the existing VimTextArea dependency. It is intentionally not full Vim: named registers, macros, marks, `:s///` regex substitution, folds and splits are not implemented.

In INSERT mode, `Tab` requests IPython completion, applying a single candidate over IPython's exact source range or listing multiple matches. If there are no candidates, Tab inserts spaces to the next four-column tab stop. `Shift+Enter` runs the current cell; `Ctrl+Enter` is an alternate, `Ctrl+Space` explicitly requests completion, and `Ctrl+P` / `Ctrl+N` browse executed cells. Completion works against the dynamic `lab` proxy as well as ordinary Python names. The session executes cells sequentially on a worker thread, preserving Python state without freezing the UI. The same `lab` proxy exposes live instruments and the latest sweep results. Matplotlib figures created in the console are shown in the plot panel.

`input()`, text or binary reads from `sys.stdin`, worker binary stdout/stderr access, and interactive `%debug` / `%pdb` prompts fail immediately because there is no console terminal stream. `Ctrl+C` is best-effort; Python cannot reliably interrupt native code or a driver call blocked in I/O. Current driver timeout defaults are MXA VISA 5 s, PSU VISA 10 s, LeCroy VISA 10 s (with supported per-query overrides), and wavemeter HTTP 1 s for reads / 3 s for setpoint writes. The MXA `wait_opc()` / `single_sweep_wait()` operation can use a 30 s per-call timeout.

A normal quit requests a cell interrupt and waits up to 12 s. If the worker remains active, the quit is cancelled and the application stays open with instruments connected; this avoids disconnecting hardware underneath a cell, but does not mean the hardware is safe to leave at its present output state. Wait for the call or its I/O timeout to finish before retrying. There is no safe force-quit path; external process termination can leave the PSU output or shutter at its last commanded state.

#callout(
  "Two different things are called lab",
  [
    In the console, `lab` is a #code("LabProxy") (a live, read-only view for typing at). In code, `Lab` is the session object (#code("Lab")) that owns the handles. The proxy reads the session's handles on every access; it never caches a device.
  ],
)

```python
# connect the MXA and the shutter (which connects the PSU) on the Connect page first, then:
lab.mx.set_center_freq(1.5e6)
lab.mx.set_rbw(24e3)
lab.mx.single_sweep_wait()
trace = lab.mx.get_trace_data(1)

lab.shutter.open()
lab.results[-1].traces["squeezing"]   # last completed sweep, if any
lab.psu.set_voltage(5.0, 1)            # the supply itself; the shutter is one channel of it
lab.connected                          # e.g. ("mx", "psu", "shutter")
lab.wavemeter.read_frequency(4)        # works before (or without) Connect: stateless HTTP
```

The names are `mx`, `psu`, `shutter`, `scope` and `wavemeter` (the proxy maps `mx` to the `mxa` handle); `results`, `last_run`, `handles` and `connected` complete the set. Device calls go through the same #code("LockedProxy") the pages use. Disconnect the MXA and the next `lab.mx` raises a clear `AttributeError`; nothing is cached to go stale. The console is a full-power Python session: it writes to real instruments, so treat it like direct hardware control.

= Scope waveform recording <sec-scope-recording>

*Acquire & save* is a measurement-recording operation, not a plotting convenience. One click acquires the enabled channels' `DAT1` block and creates a new file pair under `data/YYYY-MM/` (a fresh random suffix means a normal acquisition never overwrites an earlier one). The complete field-by-field description, rendered from the real files, is in @part-data; in summary:

- the `.npz` holds numbers only: `time_<ch>`, calibrated `value_<ch>` and the exact signed 16-bit `raw_<ch>` codes;
- the `.json` manifest identifies the recording (`measurement_id`, UTC start/end, software and Python versions, transport `VISA (VXI-11)`, address, timeout and the `*IDN?` identity), records the requested and the read-back configuration, and describes every waveform with its own timebase, gain, offset and statistics;
- an `acquisition` block says whether a running acquisition was stopped for the download (trigger mode AUTO or NORMAL; SINGLE and STOP are left alone), the prior mode, and non-fatal warnings;
- the NPZ is SHA-256 hashed after writing and the digest is stored in the manifest.

Calibrated values come from the scope's own `VERTICAL_GAIN`/`VERTICAL_OFFSET` (`value = gain × code − offset`); the form's V/div and offset are recorded as configuration provenance, never mistaken for readback. The time axis is `time_offset + index × time_interval`. Statistics are convenience summaries of the calibrated arrays, with non-finite samples excluded. The storage layer is shared with sweeps through #code("save_numeric_recording") (atomic `.part` replacement, pickle-free numeric archives, manifest, checksum), so there is one persistence implementation.

= Typical session, start to finish <sec-session>

1. `uv run iyz`. The Connect page is shown first.
2. Move to the MXA row and press Enter. #code("Lab.connect") builds the handle, calls `connect()` then `probe()` (`*IDN?`) in a worker, and registers it only on success.
3. Press `s`. Pick bandwidth or frequency, adjust the range, press *Run sweep*. #code("SweepScreen._run") holds the instrument locks for the whole run, calls #code("prepare_analyzer"), then #code("run_sequence") with a progress callback.
4. Each point is checkpointed through #code("save_step_results"); on completion the run is stored as `last_run` for the console, and `run_metadata.status` is rewritten from `running` to its final value.
5. Press `o`. Retrieve, edit, Apply (the form is refreshed from the read-back), then *Acquire & save*.
6. Press `t` to browse saved recordings, or `i` to inspect live instruments and the last sweep.

= References

- Repository implementation: #file("src/iyzee/devices/base.py"), #file("src/iyzee/devices/power.py"), #file("src/iyzee/devices/scope.py"), #file("src/iyzee/scope_workflows.py"), #file("src/iyzee/devices/wavemeter.py"), #file("src/iyzee/tui/app.py"), and the associated tests.
- #link(<part-mxa>)[MXA and measurement guide] — SCPI reference, measurement physics and the squeezing/shot-noise workflow.
- Rohde & Schwarz, *HMP Series Power Supply User Manual*: `INST:NSEL`, `INST OUTn`, `OUTP:SEL`, `OUTP:GEN`.
- Teledyne LeCroy, *Remote Control Manual* for your model family: `WF?`, `INSPECT?`, `CFMT`, `CORD`, `<channel>:VOLT_DIV`/`OFFSET`/`COUPLING`/`TRACE`, `TRIG_SELECT`/`TRIG_LEVEL`/`TRIG_SLOPE`/`TRIG_COUPLING`/`TRIG_MODE`, `TIME_DIV`.
- PyVISA documentation, resource strings and `query_binary_values()`: #link("https://pyvisa.readthedocs.io/en/1.10.0/api/resources.html")[PyVISA resources]
- Textual workers and focus: #link("https://textual.textualize.io/guide/workers/")[Workers guide]; the console uses Textual #link("https://textual.textualize.io/widgets/text_area/")[TextArea] and #link("https://textual.textualize.io/widgets/rich_log/")[RichLog], backed by IPython.

= Maintenance rule

Keep this guide tied to the implementation. Prefer `#sym` and `#fact` over prose that repeats a name, a line number or a value: the build checks them. When a driver's commands, the instrument registry, a screen's behavior or the console's namespace changes, update the section in the same change, and do not describe a design that no longer exists as current.
