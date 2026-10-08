// iyzee architecture guide
#import "requirements.typ": *

// Whole-file link; a symbol link (`#sym`) is preferred because its lines can never go stale.
#let source(path, label) = underline(link(blob(path))[#label], stroke: hairline)

#let node(title, detail, width: 100%) = box(
  width: width,
  fill: blue-soft,
  stroke: 0.8pt + navy,
  radius: 5pt,
  inset: 8pt,
)[
  #align(center)[#text(weight: "bold", fill: navy)[#title]]
  #v(0.25em)
  #align(center)[#text(size: 8.5pt, fill: blue-ink)[#detail]]
]

#let arrow(dir: "→") = align(center)[
  #text(size: 16pt, weight: "bold", fill: navy)[#dir]
]

#part(
  "Architecture",
  "Architecture",
  "How the system operates, and why it is shaped this way.",
  id: "part-architecture",
)

#callout(
  "Status",
  [
    This part describes the current implementation. It is an architecture
    guide, not a generic instrument-control tutorial and not a complete API
    reference. The source code remains authoritative; when code, tests, and
    prose disagree, the implementation and its verified behavior win.
  ],
)

= What `iyzee` is

`iyzee` is a small Python control and measurement toolkit for a laboratory
setup in which several real instruments participate in one experiment. Its
center of gravity is automated noise measurement, but the architectural
problem is broader: the same physical instruments must be usable from scripts,
an interactive terminal UI, and an embedded Python console without creating
multiple incompatible copies of the control logic.

There are therefore several different ways to *use* the program, but not
several implementations of the underlying measurement machinery.

#figure(
  grid(
    columns: (1fr, 18pt, 1fr, 18pt, 1fr),
    gutter: 5pt,
    align: center,
    node("iyzee script", "reusable experiment and device code"),
    arrow(),
    node("shared machinery", "experiments and workflows"),
    arrow(),
    node("live session", "Lab and instrument handles"),
    node("iyzee-tui", "interaction and presentation"),
    arrow(dir: "↓"),
    node("same implementation", "no second hardware-control path"),
    arrow(dir: "↓"),
    node("same resources", "console and TUI share live state"),
  ),
  caption: [Multiple entry points converge on one reusable machinery layer and one live session state.],
)

The important architectural distinction is between *composition* and
*machinery*. The TUI decides what the user sees, what is enabled, and when an
operation starts. The experiment and workflow layers decide how a useful
laboratory operation is performed. Drivers know how to speak to a particular
instrument. Transports know how bytes move. Persistence records what actually
happened.

That separation is not an academic layering exercise. It exists because this
project has two simultaneous requirements that are easy to violate:

1. hardware operations need to be reusable outside the UI, and
2. the UI must remain responsive even when hardware or processing is slow.

The resulting structure lets the project keep one set of physical operations
while presenting them through multiple interfaces.

== What `iyzee` is not

It is deliberately *not* a generic instrument framework trying to erase all
device differences. A LeCroy VXI-11 scope, a Keysight SCPI analyzer, and a
stateless HTTP wavemeter do not have the same protocol semantics, and pretending
otherwise would move useful complexity into a fake abstraction.

It is also not a distributed-control system. The TUI and console run in the
same process and operate on the same session state. There is no remote service
whose job is to mirror instrument state back into a client.

The TUI is not the application kernel. A page is allowed to be opinionated
about interaction, layout, validation, and presentation; it is not the place to
encode the only copy of an instrument operation.

Finally, `Lab` is not a "super object" containing every possible setting for the
laboratory. It is principally the authoritative inventory of the instruments
currently participating in the session, together with the lifecycle and
synchronization boundary for those live resources.


= Architectural principles

The architecture is easier to maintain when its rules are stated explicitly.
These are not abstract software-engineering preferences; each one follows from
a concrete property of laboratory control software: physical devices are slow,
stateful, fallible, shared between callers, and expensive to reason about after
the fact.

#grid(
  columns: (1fr, 1fr),
  gutter: 8pt,
  [
    #box(
      fill: blue-pale,
      stroke: 0.7pt + hairline,
      radius: 5pt,
      inset: 9pt,
    )[
      *1. Explicit resource ownership*

      Construction and connection are separate operations. The code that decides
      to use a resource for a session owns its lifetime; creating a Python object
      must not silently open a physical link.

      #code("BaseDevice.connect") and #code("Lab.connect") make that
      boundary concrete.
    ]
  ],
  [
    #box(
      fill: blue-pale,
      stroke: 0.7pt + hairline,
      radius: 5pt,
      inset: 9pt,
    )[
      *2. The TUI is not the hardware API*

      Screens compose and present operations. They should not become the only
      place where a scope acquisition, analyzer operation, or persistence rule
      can be executed.

      #code("ScopeScreen._acquire") hands the actual work to reusable functions such as
      #code("acquire_scope_recording") in #file("src/iyzee/scope_workflows.py", label: "scope_workflows.py").
    ]
  ],
  [
    #box(
      fill: blue-pale,
      stroke: 0.7pt + hairline,
      radius: 5pt,
      inset: 9pt,
    )[
      *3. Drivers express instrument semantics*

      Higher layers should ask for meaningful operations such as
      `set_rbw()` or waveform acquisition. SCPI strings, binary-block framing, I/O
      timeouts, and response decoding belong below that boundary.

      See #source("src/iyzee/devices/mxa.py", [devices/mxa.py]) and
      #source("src/iyzee/devices/scope.py", [devices/scope.py]).
    ]
  ],
  [
    #box(
      fill: blue-pale,
      stroke: 0.7pt + hairline,
      radius: 5pt,
      inset: 9pt,
    )[
      *4. Workflows express laboratory operations*

      A workflow composes instrument capabilities into a useful laboratory
      action. It can encode sequencing, verification, error policy, and
      persistence without knowing anything about Textual widgets.

      #source("src/iyzee/scope_workflows.py", [scope_workflows.py]) is the
      clearest example.
    ]
  ],
  [
    #box(
      fill: blue-pale,
      stroke: 0.7pt + hairline,
      radius: 5pt,
      inset: 9pt,
    )[
      *5. Synchronization follows the resource*

      A physical instrument can have several simultaneous callers: a TUI
      worker, the embedded console, and ordinary Python code. The lock therefore
      belongs to the handle or transport guarding that resource, not to one UI.

      #code("LockedProxy") is one
      half of this boundary; the other is the handle's shared lock contract.
    ]
  ],
  [
    #box(
      fill: blue-pale,
      stroke: 0.7pt + hairline,
      radius: 5pt,
      inset: 9pt,
    )[
      *6. Persistence is part of the measurement model*

      A useful measurement has to remain interpretable after the application
      exits. Numerical arrays, configuration, identity, timing, and relevant
      error state should therefore travel with the recording rather than being
      reconstructed from a filename or a UI snapshot.

      See #code("save_numeric_recording") and #code("save_step_results").
    ]
  ],
)

== Consequences of the principles

These rules impose useful constraints on where new code can go.

#figure(
  stack(
    spacing: 4pt,
    node("User intent", "button press, script call, or console expression"),
    arrow(dir: "↓"),
    node("Application operation", "workflow or experiment-level composition"),
    arrow(dir: "↓"),
    node("Instrument semantics", "driver/client operation"),
    arrow(dir: "↓"),
    node("Resource mechanics", "VISA (VXI-11, raw socket), HTTP"),
    arrow(dir: "↓"),
    node("Physical state", "the instrument actually responds or changes"),
  ),
  caption: [A feature should descend through the smallest number of boundaries necessary; each layer adds meaning, not merely indirection.],
)

A good new abstraction is one that introduces a real ownership, semantic, or
concurrency boundary. A weak abstraction merely renames a lower-level function
and forces contributors to navigate one more file.

This gives a useful test for design proposals: *what invariant becomes easier to
state or enforce because this new boundary exists?* If the answer is "none",
the project probably does not need another layer.

== One resource, one authoritative state

A related principle is that connected hardware state should not be copied into
multiple mutable registries.

#figure(
  grid(
    columns: (1.2fr, 18pt, 1fr, 18pt, 1.2fr),
    gutter: 4pt,
    align: center,
    node("physical device", "real instrument"),
    arrow(),
    node("driver + handle", "live object and lock"),
    arrow(),
    node("Lab.handles", "one session inventory"),
  ),
  caption: [User interfaces observe the session inventory; they do not create competing inventories of their own.],
)

The same idea appears in the console. `LabProxy` keeps one stable shell name but
resolves its instrument attributes against current application state. That
keeps connection changes authoritative instead of requiring every page or
namespace to perform synchronization work.

== What the principles deliberately leave open

The architecture does *not* prescribe one universal driver base class, one
universal transport, or one universal state container. The common handle
contract is intentionally narrower than the devices themselves.

A heterogeneous laboratory benefits from common lifecycle and concurrency rules
while retaining device-specific semantics. The abstraction should therefore
stop at the point where forcing more uniformity would hide meaningful differences
between instruments.

= The architectural shape

At the highest level, the system can be read as a dependency graph rather than
as a pile of files.

#figure(
  stack(
    spacing: 5pt,
    node("TUI / script / IPython", "user interaction and application composition"),
    arrow(dir: "↓"),
    node("workflows / experiment", "laboratory operations and measurement sequencing"),
    arrow(dir: "↓"),
    node("device drivers", "instrument-specific semantics"),
    arrow(dir: "↓"),
    node("transport / client", "VISA (VXI-11, raw socket), HTTP"),
    arrow(dir: "↓"),
    node("hardware", "the physical instrument"),
  ),
  caption: [Normal responsibility direction: higher layers request work from lower layers; lower layers do not know which interface called them.],
)

A useful way to read this is from the bottom upward.

The *transport* layer moves bytes or requests. The *driver* layer assigns
meaning to those operations for one instrument. The *workflow/experiment*
layer composes driver operations into laboratory actions. The *TUI* and
*console* layers expose those actions to a human. `Lab` and its handles bind
all of that to the actual connected resources of one running session.

The arrows are intentionally one-way in responsibility. A VISA session does
not know that a received block is an oscilloscope waveform. The oscilloscope
driver does not know whether it was called by a Textual button or a Python
script. A scope workflow does not know how a Textual widget represents a dirty
field.

= Ownership and lifecycle <arch-ownership>

One of the most important invariants is simple:

#pull[Constructing a device is not the same thing as connecting to it.]

This makes ownership explicit. The caller that decides to use a resource for a
session also decides when that resource should be opened and closed.

The application-level operation is implemented by
#code("Lab.connect"). In simplified form:

```python
lab = Lab()
lab.connect("mxa")
mx = lab.device("mxa")
trace = mx.get_trace_data(1)
```

The important part is the ownership boundary: the application opens and closes
resources; the measurement procedure uses resources that are already live.

#figure(
  grid(
    columns: (1fr, 18pt, 1fr, 18pt, 1.1fr, 18pt, 1.25fr, 18pt, 1.35fr),
    gutter: 3pt,
    align: center,
    node("InstrumentSpec", "name and factory"),
    arrow(),
    node("handle", "adapter and lock"),
    arrow(),
    node("connect", "open the resource"),
    arrow(),
    node("probe", "prove it answers"),
    arrow(),
    node("Lab.handles", "authoritative live inventory"),
  ),
  caption: [Connection becomes session state only after the resource has opened and passed an active probe.],
)

The insertion into `Lab.handles` happens only after connection and probing have
succeeded. If opening or probing fails, the half-open resource is explicitly
closed and nothing is registered. The important consequence is that membership
in `Lab.handles` means more than "an object exists": it means "the application
currently considers this instrument connected and usable".

Disconnect is the inverse operation. The handle is removed from the live
inventory and its resource is closed under the same synchronization boundary
used by ordinary hardware access.

== Why the lifecycle is explicit

There are several reasons not to hide connection in object construction.

First, constructing a driver becomes safe in tests that have no instrument at
all. Second, a procedure can be built without accidentally opening hardware.
Third, an interactive application can explicitly represent connection state to
the user. Fourth, scripts can choose their own ownership boundary, including a
Python context manager for the VISA-backed devices.

This also prevents an especially confusing class of bugs in which a reference
looks valid in Python but its underlying resource was never intentionally opened
or has already been closed by some other owner.

= `Lab`: authoritative session state <arch-lab>

`src/iyzee/lab.py` contains a deliberately small object with one central job:
represent the instruments of the current session and own their lifecycle.

The registry of `InstrumentSpec` objects answers, "How do I construct the
handle for this named instrument?" `Lab.handles` answers, "Which instruments
are actually connected right now?"

The conceptual runtime object is therefore:

#block(
  fill: blue-pale,
  stroke: 0.5pt + hairline,
  inset: 8pt,
  radius: 3pt,
  width: 100%,
)[
```text
IyzeeApp
│
├── Lab
│    ├── mx → InstrumentHandle
│    ├── scope → InstrumentHandle
│    ├── shutter → InstrumentHandle
│    └── wavemeter → InstrumentHandle
│
├── last_run
│
└── mounted TUI pages```
]

The TUI does not need its own shadow dictionary of connected devices, and the
console does not need another shadow dictionary either. Both observe the same
`Lab` state.

This is one of the project's most valuable simplifications: there is one
answer to "is the scope connected?" inside a process, rather than one answer in
the Connect page, another in the console namespace, and another in each worker.

= Handles: one application contract, many real devices <arch-handles>

A physical laboratory setup is heterogeneous. `iyzee` embraces that fact while
still giving higher layers a small common contract.

The #code("InstrumentHandle") protocol supplies:

- `connect()` and `disconnect()` for lifecycle,
- `probe()` for an active post-connect sanity check,
- `device` for access to the live driver or client,
- `alive` for cheap link-state observation where the driver can provide it,
- `lock` for synchronization of access to the physical resource.

The abstraction is deliberately about *resource management*, not about
forcing every device into one command model.

#table(
  columns: (1.35fr, 2fr, 2.3fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  align: (left, left, left),
  [*Instrument*], [*Client/driver*], [*Transport*],
  [Keysight MXA], [`KeysightMXA`], [VISA / SCPI],
  [PSU / shutter], [`PSU` / `ShutterControl`], [VISA raw socket, port 5025],
  [LeCroy scope], [`LeCroy`], [VISA / VXI-11 (`TCPIP0::<ip>::inst0::INSTR`)],
  [Wavemeter], [`Wavemeter`], [HTTP],
)

The `_VisaHandle`, `ShutterHandle`, `WavemeterHandle`, and `ScopeHandle`
implement this common boundary without pretending that the underlying clients
are interchangeable.

== Why locking lives on the handle

The physical resource, not the UI, is what needs serialization.

Consider a sweep worker and a console cell that both address the MXA at the
same time. They must not interleave commands arbitrarily. Putting the lock in
the application would make synchronization a property of one caller. Putting
it on the instrument handle makes synchronization a property of the resource
itself.

#figure(
  grid(
    columns: (1fr, 18pt, 1fr, 18pt, 1fr),
    gutter: 4pt,
    align: center,
    node("Sweep worker", "long-running hardware calls"),
    arrow(),
    node("shared RLock", "owned by the resource handle"),
    arrow(),
    node("MXA", "one physical device"),
  ),
  caption: [Synchronization follows the physical resource, allowing workers and the console to coordinate through the same lock.],
)

The re-entrant lock matters because a workflow may already hold the resource
lock and then call through a proxy that acquires the same lock again. A plain
`Lock` would deadlock in that situation.

The scope is slightly different in implementation detail. Its handle returns
the LeCroy driver's transaction lock rather than introducing a second unrelated
lock. This is important because a waveform download is itself a compound
transaction (set format, set byte order, request the block, read it raw, then
several `INSPECT?` queries for the scaling); the same transaction boundary
therefore protects both driver-level operations and higher-level callers.

= Drivers and workflows are different layers

The distinction between #source("src/iyzee/devices/scope.py", [devices/scope.py]) and #source("src/iyzee/scope_workflows.py", [scope_workflows.py]) is a good
example of the architecture.

The driver asks:

#pull[How does this instrument expose channels, trigger settings, and waveform data?]

The workflow asks:

#pull[What useful laboratory operation should the application perform using those capabilities?]

A driver should therefore contain instrument semantics such as a command to
set time-per-division or download a waveform block. A workflow can compose
those operations into read/modify/apply/read-back or acquire-and-save logic.

That division is what allows a workflow to be called from a TUI worker, a test,
or the console without making the workflow import Textual or know which widget
contains a value.

= Measurement execution model <arch-execution>

The experiment layer, centered on #code("run_sequence"), uses one general execution pattern rather than a separate
hand-written loop for every sweep.

#figure(
  grid(
    columns: (1fr, 18pt, 1fr, 18pt, 1fr, 18pt, 1fr),
    gutter: 3pt,
    align: center,
    node("AnalyzerConfig", "experiment settings"),
    arrow(),
    node("prepare_analyzer", "configure hardware"),
    arrow(),
    node("Step array", "reproducible points"),
    arrow(),
    node("run_sequence", "ordered execution"),
  ),
  caption: [The TUI composes an existing sequence model rather than reimplementing measurement loops.],
)

`AnalyzerConfig` collects experiment-level analyzer state. `prepare_analyzer()`
turns that into concrete instrument setup. A `Step` represents one reproducible
measurement point, such as one RBW value in a bandwidth sweep or one frequency
coordinate in a frequency sweep.

`ExperimentContext` carries the already-connected hardware and the metadata
shared by all steps in one run. This is a deliberate boundary: a step may use
the MXA, the shutter, and run metadata, but it does not decide who owns those
resources.

== `StepResult` is the measurement contract

Every successful point becomes a #code("StepResult") containing:

- an `x_value` and `x_unit`,
- named traces rather than a fixed set of two special arrays,
- metadata describing settings or state relevant to that point.

This turns "a measurement" into something more useful than a NumPy array. A
trace is only interpretable when the reader also knows what was configured when
it was acquired.

== `run_sequence()` is intentionally UI-agnostic

`run_sequence()` executes the steps in order, records failures, and optionally
calls `on_step` after each point. It knows nothing about progress bars, plots,
widgets, focus, or Textual events.

The two basic failure policies are explicit:

- `on_error="raise"` stops on the first failing step.
- `on_error="skip"` records the failed point and continues.

That choice is part of experiment semantics. The framework should not silently
turn every hardware failure into either a fatal stop or a missing point.

The TUI uses the callback to observe progress, while the script path can run the
same code without supplying one at all.

= Run state and interrupted experiments

A measurement can fail in more ways than "the function raised and therefore
nothing exists". The process can be interrupted after several successful
points, the user can abort a run, or setup can fail after a run record has been
created.

`RunRecord` exists to keep that history explicit. Its status distinguishes
`running`, `completed`, `completed_with_errors`, `aborted`, and `failed`.

The subtle but important rule is that a record is first written as `running`.
A file that still says `running` after a process disappears is therefore
meaningful: it says the program never reached its normal finalization path.

This is more honest than inferring completion from the mere existence of a
short array.

= Scope architecture: the complete case study <arch-scope-case>

The scope is the best example of how the project's layers cooperate because it
combines a real instrument protocol, editable state, verification, acquisition,
persistence, and background work.

#figure(
  stack(
    spacing: 4pt,
    node("ScopeScreen", "form state, validation, interaction"),
    arrow(dir: "↓"),
    node("scope_workflows", "read, apply, acquire, save"),
    arrow(dir: "↓"),
    node("LeCroy", "instrument commands and waveform semantics"),
    arrow(dir: "↓"),
    node("PyVISA session", "VXI-11 transport, timeouts"),
    arrow(dir: "↓"),
    node("TCP/IP", "bytes on the wire"),
  ),
  caption: [The scope path crosses clear responsibility boundaries; the page owns presentation state while the lower layers own hardware semantics and transport.],
)

The page owns the human-facing state. The workflow owns the semantics of
reading, applying, acquiring, and saving. The driver owns LeCroy
commands and validates every binary block. PyVISA owns the session.

== Dirty state is a statement about knowledge

A useful way to understand the scope form is not as a configuration editor but
as a small state machine.

The page can know:

- the last value the scope reported,
- what the user has currently typed or selected,
- whether those two values differ,
- whether the current hardware state is trustworthy enough for acquisition.

That is why field-level dirty indicators are useful. They do not merely make
the interface prettier; they communicate a difference between *what the user
wants* and *what the software last verified on the instrument*.

== Apply means write, then verify

The scope workflow deliberately does not treat a successful write as proof that
the instrument now holds exactly the requested value.

A hardware setter can be normalized, quantized, rejected, or affected by
instrument-side constraints. Therefore the apply path is conceptually:

#figure(
  stack(
    spacing: 4pt,
    node("known baseline", "last verified instrument state"),
    arrow(dir: "↓"),
    node("compare", "identify changed fields"),
    arrow(dir: "↓"),
    node("write", "send only necessary changes"),
    arrow(dir: "↓"),
    node("read back", "observe what the scope accepted"),
    arrow(dir: "↓"),
    node("new baseline", "only verified state is trusted"),
  ),
  caption: [Scope configuration changes are verified state transitions, not blind assignments.],
)

Only the read-back result is promoted to the next trusted baseline. If readback
fails, the application must not manufacture a clean state merely because the
write call returned.

This is especially important for an acquisition button: the page should not
allow a recording to proceed merely because its own widgets look internally
consistent. The relevant criterion is that the hardware state is synchronized
and trustworthy.

== The first failure raises <arch-first-failure>

The scope workflows deliberately do not try to be clever about partial success.
#code("apply_channel_settings") and #code("read_channel_settings") stop at the first
failure; writes that already happened stay applied, and the exception tells the
caller exactly where it stopped. #code("acquire_scope_recording") raises on the first
channel that fails to download, restoring the trigger mode first (best effort), and
no recording is saved: a capture in which one channel is missing is not a
capture of the experiment.

What *is* preserved is knowledge about the capture. Non-fatal problems (the scope
would not freeze, the trigger mode could not be restored, `*IDN?` did not answer)
are collected in `ScopeAcquisition.warnings` and written to the manifest, so a
recording says honestly how trustworthy it is.

#tested-by("test_acquire_raises_on_the_first_failed_channel_and_restores_trigger_mode", "test_apply_channel_settings_raises_on_the_first_failure_and_skips_the_rest", "test_failure_to_restore_is_reported_as_a_warning")

= The scope transport: VISA over VXI-11 <arch-scope-transport>

The LeCroy stack is split into three layers.

#figure(
  stack(
    spacing: 4pt,
    node("LeCroy semantics", "waveform, trigger, channel and acquisition meaning"),
    arrow(dir: "↓"),
    node("LeCroy driver", "commands, block validation, error translation"),
    arrow(dir: "↓"),
    node("PyVISA session", "VXI-11 transport, I/O timeout"),
    arrow(dir: "↓"),
    node("TCP/IP", "byte stream"),
  ),
  caption: [The driver sits on a VISA session; framing and sockets belong to PyVISA, meaning belongs to the driver.],
)

#code("LeCroy") subclasses #code("BaseDevice"), so it shares the lifecycle of the
other VISA instruments: it opens `TCPIP0::<ip>::inst0::INSTR`, and construction does
not connect. The scope must have its remote control set to *LXI / VXI-11* (not
VICP). Two settings differ from the analyzer's, and both matter for binary data:
no read termination is configured, because waveform bytes may legitimately contain
`0x0A` and VXI-11 marks the end of a reply itself; and the VISA read chunk is raised to
#fact("drivers.scope_chunk_size") bytes because the default is far too small for long records.

== Every exchange goes through one door

All I/O passes through #code("LeCroy._io"), a context manager that holds the
driver's re-entrant #code("LeCroy.transaction_lock") for one VISA exchange. If PyVISA
raises, the connection is dropped before the error propagates.

This is the central integrity rule of the scope path:

#pull[A timed-out exchange leaves the reply stream in an unknown state; a late answer would be handed to the next query. So after any I/O failure the driver closes the connection and the caller must reconnect.]

A timeout is translated to #code("LeCroyTimeoutError") (a `TimeoutError`, so existing
`except TimeoutError` / `except OSError` handlers still work). Any other VISA error
is re-raised unchanged after the drop. A single query may be given a wider timeout
(`query(..., timeout=)`) without changing the steady-state value of
#fact-ms("drivers.scope_timeout_ms"); the first reply after connecting gets
#fact("drivers.scope_first_response_s") s (see @arch-link-monitoring).

== A complete reply that is the wrong shape does not kill the stream

There is an important converse. A binary waveform is a definite-length block
(`#9` followed by a nine-digit byte count). #code("devices.scope._definite_block") validates it
against the *declared* count: a missing header, a malformed count, a truncated body or
unexpected trailing bytes raise #code("LeCroyProtocolError"). That error is raised
*after* the whole reply was read, so nothing is left unread and the connection stays
usable.

#pull[Lose the connection when stream alignment is uncertain; do not tear down a healthy stream merely because a fully received reply contains bad content.]

Two further guards sit at the driver boundary. Channel and block names that are
interpolated into commands pass through #code("devices.scope._ident"), so a "channel" such as
`C1:VOLT_DIV 1;C2` is rejected rather than sent; and numeric setters pass through
#code("devices.scope._finite"), so `NaN` and infinities never reach the instrument. Format and byte
order (`CFMT DEF9,WORD,BIN`, `CORD LO`) are written *before* each waveform request,
because the scope encodes the reply when it executes `WF?`.

#anchors("LeCroy", "LeCroy._io", "LeCroy._read_words", "devices.scope._definite_block", "LeCroy.getDataFloatsDetailed")
#tested-by("test_a_silent_scope_raises_lecroy_timeout_and_drops_the_connection", "test_malformed_waveform_blocks_are_rejected_but_keep_the_link", "test_a_waveform_read_timeout_drops_the_connection", "test_free_form_channel_names_must_be_plain_identifiers", "test_numeric_setters_reject_non_finite_values")

= Transport state and link monitoring <arch-link-monitoring>

The scope driver's state is simple: #code("LeCroy.connected") is true while a VISA
resource is open, and #code("LeCroy._drop") clears it after a failure.
#code("ScopeHandle.alive") delegates to it, which is how the application notices a
lost scope without issuing a normal measurement command.

The application polls every #fact("tui.link_check_interval_s") s
(#code("IyzeeApp._check_links")). #code("Lab.drop_dead_links") removes a dead handle from the
authoritative inventory immediately, so the nav rail, the Connect page and the
console all agree, and releases what is left of the link on a background thread
(its disconnect needs the instrument lock). #code("ScopeHandle.probe") proves
the scope *answers* (`*IDN?`), not just that the resource opened; a scope held by
another client is reported as exactly that, after
#fact("drivers.scope_first_response_s") s.

= The TUI is an orchestration and presentation layer

The TUI has one particularly important design constraint: the Textual event
loop must not become the place where slow hardware work happens.

The role of the app and pages is therefore best understood as:

```text
presentation + interaction + orchestration
                       ↓
                 reusable machinery
```

`IyzeeApp` owns application-wide concerns such as page selection, session state,
connection-state refresh, commands, and shutdown. `Page` provides shared form,
validation, readiness, and worker-to-UI plumbing. Concrete pages specialize
that for Connect, Sweep, Scope, Results, Console, and Log.

A page is allowed to *start* an operation. It should not become the only place
where the operation exists.

== Why pages are not hardware drivers

Putting SCPI directly into a widget method seems convenient at first:
the button is right there, and the command can be issued immediately.

The cost is duplication and isolation. A second caller then needs a second copy
of the same logic, tests need to instantiate UI objects to exercise hardware
semantics, and the console cannot call the operation without reaching into UI
implementation details.

With the current architecture, a page can instead do something conceptually
like:

#block(
  fill: blue-pale,
  stroke: 0.5pt + hairline,
  inset: 8pt,
  radius: 3pt,
  width: 100%,
)[
```text
read form state
      ↓
check preconditions
      ↓
start worker
      ↓
worker calls reusable workflow
      ↓
workflow talks to driver
      ↓
worker prepares a UI-sized result
      ↓
_ui(...) on the Textual thread```
]

The UI remains thin enough that the same underlying operation can exist without
Textual.

= Worker boundaries and UI responsiveness

Hardware latency is unbounded from the perspective of the event loop. A VISA
query can wait. A scope transfer can time out. A wavemeter request can stall.
Waveform conversion and plotting can also be expensive even after the hardware
has responded.

The project therefore treats the worker boundary as a concurrency boundary:

#figure(
  grid(
    columns: (1.2fr, 20pt, 1.2fr, 20pt, 1.2fr),
    gutter: 4pt,
    align: center,
    node("worker", "hardware I/O and expensive processing"),
    arrow(),
    node("UI handoff", "explicit thread boundary"),
    arrow(),
    node("Textual", "widget mutation on the UI thread"),
  ),
  caption: [Blocking work stays out of the Textual event loop; only the UI-facing outcome crosses back.],
)

The `_ui` helper in `Page` exists to make the final hop explicit. Widgets are
not casually mutated from worker threads; the worker computes a result and
schedules the presentation update onto Textual's thread.

For live plots, this also motivates reducing data before it reaches the UI.
The purpose of a progress update is not to make the UI own a full scientific
array when a compact preview is sufficient for the screen.

= Application commands and keyboard ownership

The TUI uses normal Textual bindings for page navigation and widgets' own input
handling for text entry. The application does not maintain a second hidden
"mode" flag just to guess whether a key is meant for navigation or input.

The command line beginning with `:` is a separate command layer. Parsing and
completion live in `tui/commands.py`, while each page declares the commands it
supports. This keeps the command grammar free from Textual and makes command
semantics easy to test.

The design also matters for the embedded console: while the terminal owns the
keyboard, most keys must continue to mean what IPython expects rather than what
the surrounding TUI expects.

= The embedded IPython console

The console is not a second application. It is IPython running inside the same
process and looking at the same live instruments.

The input path is:

#figure(
  stack(
    spacing: 4pt,
    node("keyboard event", "Textual receives the physical key"),
    arrow(dir: "↓"),
    node("termkeys", "key event to terminal bytes"),
    arrow(dir: "↓"),
    node("IPython / prompt_toolkit", "real Python terminal UI"),
    arrow(dir: "↓"),
    node("LabProxy", "fresh session lookup"),
    arrow(dir: "↓"),
    node("LockedProxy", "shared resource lock"),
    arrow(dir: "↓"),
    node("live instrument", "same handle used by TUI workers"),
  ),
  caption: [The console is an in-process terminal frontend to the same live resource graph.],
)

The output path is the reverse integration problem:

#block(
  fill: blue-pale,
  stroke: 0.5pt + hairline,
  inset: 8pt,
  radius: 3pt,
  width: 100%,
)[
```text
IPython output
     ↓
virtual terminal
     ↓
pyte
     ↓
Vterm
     ↓
TerminalView
     ↓
Textual rendering```
]

This arrangement is valuable because it allows IPython and prompt-toolkit to
retain their own editing, completion, history, inspection, magics, and
interrupt behavior. `iyzee` only supplies the virtual terminal boundary needed
to place that existing terminal UI inside Textual.

== Why in-process matters

If the console were a separate process, the application would need a second
mechanism to communicate with connected devices and return results. That would
introduce serialization, duplicated state, and another lifecycle boundary.

Instead, the console can execute:

#block(
  fill: blue-pale,
  stroke: 0.5pt + hairline,
  inset: 8pt,
  radius: 3pt,
  width: 100%,
)[
```text
lab.mx.set_rbw(24e3)
lab.mx.single_sweep_wait()
trace = lab.mx.get_trace_data(1)```
]

against the same live MXA used by a screen worker. The handle's lock serializes
these calls with the other users of the physical device.

The result is a powerful property for exploratory laboratory work: the UI is a
convenience layer, not a wall around the underlying Python machinery.

= `LabProxy`: dynamic state without stale console globals <arch-labproxy>

The console exposes one name, `lab`, rather than copying every current device
into the IPython namespace.

`LabProxy.__getattr__()` resolves against the application's current handles on
each access. Thus `lab.mx` means "the MXA that is connected *now*", not "the
object that happened to be connected when the console was initialized".

#block(
  fill: blue-pale,
  stroke: 0.5pt + hairline,
  inset: 8pt,
  radius: 3pt,
  width: 100%,
)[
```text
Connect MXA
    ↓
lab.mx        → live MXA proxy

Disconnect MXA
    ↓
lab.mx        → immediate AttributeError```
]

That design avoids a refresh problem. A namespace-copying implementation would
have to notice every connection and disconnection and mutate several shell
variables correctly. The live proxy has no copies to refresh and therefore no
stale device reference to clean up.

It also avoids name collisions. The application owns one shell name, `lab`.
The user remains free to create a local variable called `mx` for any unrelated
scratch computation.

== Completion is part of the architecture

Dynamic attribute lookup is useful but can defeat static completion tools such
as Jedi. The project therefore configures IPython's completer to use the proxy's
actual `__dir__()` information and to allow nested proxy lookup.

This is a small example of an important rule: abstractions should preserve the
interactive ergonomics of the lower-level tool rather than making the user pay
for the abstraction.

== History is deliberately owned by the application

The shell configuration defaults to in-memory history for direct construction
and tests. The real `iyzee-tui` entry point opts into a persistent application
history file.

The distinction matters because many tests create many independent IPython
shells. Sharing IPython's global default history database across all of them
caused real shutdown contention. Using a private in-memory history for test
shells keeps the test boundary local, while a single real application can still
have persistent history across runs.

= Data architecture <arch-data>

The persistence path is intentionally separated from display:

#figure(
  stack(
    spacing: 4pt,
    node("measurement", "full acquisition"),
    arrow(dir: "↓"),
    node("in-memory result", "StepResult or ScopeAcquisition"),
    arrow(dir: "↓"),
    node("numeric payload + metadata", "scientific record"),
    arrow(dir: "↓"),
    node("NPZ + JSON manifest", "durable persistence"),
    arrow(dir: "↓"),
    node("ResultsScreen / analysis", "display and offline derivation"),
  ),
  caption: [Scientific data is persisted independently of the interactive display representation.],
)

A plot is an interpretation of the data, not the data itself. The screen may
reduce, rescale, or otherwise transform an array for readable rendering. The
persisted record should preserve the numerical payload and the metadata needed
to interpret it later.

== Sweep persistence

Sweep results are saved incrementally. A successful point is not merely held in
RAM until the entire sweep finishes; the on-disk run is updated as the sequence
progresses.

This means an interrupted run can still be useful. The remaining question for
a reader is then "which points succeeded and how did the run end?", not "did the
application happen to reach its final save call?"

== Scope persistence

Scope acquisition extends the same principle to waveform recordings. The
numeric archive can contain per-channel time arrays, calibrated values, and the
raw signed 16-bit samples when the real driver supplies them. The JSON manifest
stores the surrounding context.

That context includes, where available:

- measurement identity and timestamps,
- software/runtime information,
- transport (`VISA (VXI-11)`), address and timeout,
- instrument identity,
- requested and applied configuration,
- acquisition/freeze/restore state,
- waveform descriptions and statistics,
- a checksum of the completed NPZ file.

The manifest can therefore tell a future reader both what the data is and which
non-fatal problems (`acquisition.warnings`) accompanied it.

== Raw versus calibrated versus display data

The distinction matters especially for the scope. Raw signed ADC codes are not
the same object as calibrated engineering values, and neither one is identical
to the downsampled data sent to a terminal plot.

The architecture keeps these meanings separate:

#table(
  columns: (1.35fr, 3.4fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  [*Representation*], [*Role*],
  [Raw acquisition], [The numerical samples as returned by the instrument, when preserved],
  [Calibrated data], [Engineering values derived using instrument-reported scaling],
  [Derived data], [Quantities computed for analysis, such as differences or statistics],
  [Display preview], [A reduced or transformed representation suitable for interactive rendering],
)

The governing rule is:

#pull[Display representations are disposable; recorded measurement data is not.]

= Configuration and connection discovery <arch-config>

Instrument addresses and the data root are configurable through environment
variables and `config.toml`. The important architectural point is that this
configuration answers *where the hardware is* and *where data belongs*; it is
not a runtime container that every component mutates.

The flow is roughly:

#block(
  fill: blue-pale,
  stroke: 0.5pt + hairline,
  inset: 8pt,
  radius: 3pt,
  width: 100%,
)[
```text
configuration sources
       ↓
   config.py
       ↓
 InstrumentSpec
       ↓
 handle construction
       ↓
 explicit connect + probe```
]

This keeps construction reproducible and makes it possible to test most of the
code without a live laboratory.

A configuration file that exists but does not parse is treated as an error.
Silently falling back would make a typo in a laboratory configuration look like
successful use of a completely different instrument setup.

= Shutdown and failure containment

Shutdown is a resource-management problem too. Connected instruments may have
blocking disconnect operations, and a worker may currently hold an instrument
lock.

The application's shutdown path first closes the embedded console, then closes
instruments outside the Textual UI thread. `Lab.close_all()` takes each
instrument's lock before disconnecting it and handles instruments independently.

The essential behavior is:

#figure(
  stack(
    spacing: 4pt,
    node("shutdown request", "user or application"),
    arrow(dir: "↓"),
    node("close / interrupt console", "release console work"),
    arrow(dir: "↓"),
    node("Lab.close_all", "collect connected handles"),
    arrow(dir: "↓"),
    node("per-device cleanup", "respect resource locks"),
    arrow(dir: "↓"),
    node("deadline", "one wedged device cannot stall exit"),
  ),
  caption: [Shutdown is bounded resource cleanup, not an assumption that every physical link will respond promptly.],
)

A timeout is therefore an exit containment mechanism, not a claim that the
hardware necessarily completed graceful shutdown.

The same principle appears in dead-link cleanup: remove a dead handle from the
authoritative session inventory so the application can offer a clean reconnect,
then release the underlying object without blocking the main UI path.

= Error philosophy

The architecture has a few rules that are more important in laboratory
software than in an ordinary GUI.

== Never manufacture a measurement

A communication failure must not quietly become a plausible number. Explicit
exceptions such as wavemeter readout errors, VISA failures, or scope timeouts are
part of the measurement's truth.

== Hardware truth beats UI optimism

A form field is an intention. A verified readback is evidence. The scope UI is
therefore based on synchronization against an instrument-reported baseline,
not only on whether its own fields are internally valid.

== Stream integrity is an error property

An I/O failure that leaves the reply stream in an unknown state (a timeout) is
fundamentally different from a semantic error on a fully received reply (a
malformed block). The first drops the connection; the second does not. This
keeps recovery conservative without making every bad payload fatal.

== Fail at the first problem, record the doubtful ones

A multi-channel operation stops at its first failure (see @arch-first-failure) rather
than returning a mixture of results and half-results. Problems that do not
invalidate the data are recorded instead of raised: `acquisition.warnings` in a scope
manifest, `run_metadata.failed_steps` and `status` in a sweep. Downstream analysis can
therefore tell a clean record from a qualified one.

== Errors should cross boundaries explicitly

Drivers raise driver/protocol errors. Workflows preserve or contextualize them.
Workers turn them into UI-facing outcomes. Persistence records them when they
matter to the interpretation of the run.

No layer should simply swallow an error because it is inconvenient for the next
layer.

= A complete sweep execution trace

The following trace connects the conceptual layers to the implementation names.

#figure(
  stack(
    spacing: 3pt,
    node("user", "measurement intent"),
    arrow(dir: "↓"),
    node("SweepScreen", "compose settings and present progress"),
    arrow(dir: "↓"),
    node("Textual worker", "keep blocking I/O off the UI thread"),
    arrow(dir: "↓"),
    node("run_sequence", "ordered Step execution"),
    arrow(dir: "↓"),
    node("BandwidthStep / FrequencyStep", "instrument-level measurement point"),
    arrow(dir: "↓"),
    node("KeysightMXA → PyVISA → MXA", "hardware acquisition"),
    arrow(dir: "↓"),
    node("StepResult → persistence", "record data and metadata"),
    arrow(dir: "↓"),
    node("UI preview", "reduced interactive representation"),
  ),
  caption: [An interactive sweep adds orchestration around the same experiment primitives used by scripts; the measurement loop itself remains UI-agnostic.],
)

The crucial observation is that `SweepScreen` does not own the sweep algorithm.
It composes existing experiment primitives, supplies a callback for live
feedback, and presents the result.

= A complete scope-acquisition trace

The analogous scope path is:

#figure(
  stack(
    spacing: 3pt,
    node("user presses Acquire", "requested recording"),
    arrow(dir: "↓"),
    node("ScopeScreen", "require synchronized, clean state"),
    arrow(dir: "↓"),
    node("scope worker", "blocking acquisition and persistence"),
    arrow(dir: "↓"),
    node("acquire_scope_recording", "freeze, download, decode, scale"),
    arrow(dir: "↓"),
    node("LeCroy → PyVISA → VXI-11", "instrument and transport"),
    arrow(dir: "↓"),
    node("ScopeAcquisition", "waveforms, provenance, warnings"),
    arrow(dir: "↓"),
    node("save_scope_acquisition", "NPZ + JSON manifest"),
    arrow(dir: "↓"),
    node("ResultsScreen / preview", "render and analyze"),
  ),
  caption: [Scope acquisition is a measurement workflow first and a plotting operation second.],
)

This is why the scope code is larger than a single widget method might suggest.
The operation spans UI preconditions, device semantics, protocol mechanics, data
conversion, persistence, and presentation. The architecture gives each part a
place instead of making one giant method responsible for all of them.

= "Why is it like this?"

The architecture becomes clearer when the main decisions are read as answers
to concrete failure modes.

#table(
  columns: (2.25fr, 3.2fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  [*Decision*], [*Reason*],
  [Devices do not connect during construction], [Clear ownership, deterministic tests, explicit lifecycle],
  [`Lab` owns connected handles], [One authoritative session inventory],
  [Locks live with handles or transports], [Synchronization follows the physical resource],
  [TUI delegates reusable operations], [Hardware semantics remain testable and callable without Textual],
  [Scope workflows are plain functions], [Console and scripts can reuse the same laboratory operations],
  [Blocking I/O runs in workers], [The UI event loop must remain responsive],
  [The scope driver drops the connection after any I/O failure], [A timed-out exchange leaves the reply stream in an unknown state; a late answer would be handed to the next query],
  [Scope settings are read back], [The instrument may quantize, normalize, reject, or otherwise alter a write],
  [Results are persisted during acquisition], [Interrupted runs retain successful information],
  [IPython is embedded], [Console and TUI share the same live resources and locks],
  [`LabProxy` resolves dynamically], [Avoid stale disconnected references and namespace refresh machinery],
  [Data is reduced before UI rendering], [Interactive rendering should not carry the whole acquisition unnecessarily],
)

None of these is a universal rule for all software. They are local answers to
actual properties of this project: real hardware, long-running measurements,
interactive experimentation, and multiple callers of one physical device.

= Testing the architecture

The test suite is most valuable when it protects the *contracts between layers*
rather than merely asserting implementation trivia.

The main contract groups are:

#table(
  columns: (1.8fr, 3.4fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  [*Area*], [*What the test should protect*],
  [Lifecycle], [Construction does not unexpectedly connect; connect/probe failures leave no registered half-open handle; close is safe],
  [Handles], [Shared synchronization and the common adapter contract],
  [`Lab`], [Connected inventory, dead-link removal, and bounded shutdown],
  [Experiment core], [Step ordering, callbacks, failure policies, and explicit run status],
  [Scope driver], [DEF9 block validation, timeout translation, connection invalidation, command-injection guards],
  [Scope workflows], [Readback invariants, clean/dirty state, partial results, and acquisition preconditions],
  [Persistence], [Atomic files, numeric-only archives, manifests, status, and checksums],
  [Workers], [Blocking operations remain off the UI thread and results return through the UI boundary],
  [Console], [Live lookup, completion, history isolation, and shared locking],
  [TUI], [User-visible navigation, command semantics, readiness, and interaction behavior],
)

A strong architectural test is one that would fail if someone accidentally
reintroduced the old wrong behavior. For example, a scope-driver test should not merely
show that a happy-path block parses; it should also prove that a short payload
or timeout cannot leave the driver pretending that its stream is valid.

Likewise, a scope test should be willing to fail when requested settings and
verified settings diverge. That is precisely the kind of bug the architecture
exists to make visible.

= Module and responsibility map

The repository is easier to maintain when each file can be described by the
question it answers.

#table(
  columns: (2.15fr, 3.2fr),
  stroke: 0.5pt + hairline,
  inset: 5pt,
  [*Source*], [*Responsibility*],
  [#source("src/iyzee/lab.py", [lab.py])], [Authoritative inventory and lifecycle of connected instruments],
  [#source("src/iyzee/devices/base.py", [devices/base.py])], [Shared VISA lifecycle and PSU channel infrastructure],
  [#source("src/iyzee/devices/handles.py", [devices/handles.py])], [Uniform resource adapters, shared locks, and `LockedProxy`],
  [#source("src/iyzee/devices/mxa.py", [devices/mxa.py])], [Keysight MXA SCPI/VISA semantics],
  [#source("src/iyzee/devices/power.py", [devices/power.py])], [PSU operations and optical shutter control],
  [#source("src/iyzee/devices/scope.py", [devices/scope.py])], [LeCroy semantics over VISA (VXI-11): commands, DEF9 blocks, timeout handling, stream integrity],
  [#source("src/iyzee/devices/wavemeter.py", [devices/wavemeter.py])], [Stateless wavemeter HTTP client],
  [#source("src/iyzee/experiment/core.py", [experiment/core.py])], [Step protocol, run context, step results, failure recording, and sequencing],
  [#source("src/iyzee/experiment/procedures.py", [experiment/procedures.py])], [Reusable analyzer configurations, steps, and sweep builders],
  [#source("src/iyzee/experiment/io.py", [experiment/io.py])], [Recording formats, atomic persistence, figures, and numeric helpers],
  [#source("src/iyzee/scope_workflows.py", [scope_workflows.py])], [Reusable scope read/apply/acquire/save operations],
  [#source("src/iyzee/tui/app.py", [tui/app.py])], [Application shell, page switching, shared state, commands, and shutdown],
  [#source("src/iyzee/tui/screens/page.py", [tui/screens/page.py])], [Common page mechanics, validation, readiness, and worker-to-UI handoff],
  [#source("src/iyzee/tui/screens/", [tui/screens/])], [Concrete user workflows and presentation],
  [#source("src/iyzee/tui/ipython.py", [tui/ipython.py])], [`LabProxy`, shell configuration, history selection, and native IPython introspection],
  [#source("src/iyzee/tui/ipython_session.py", [tui/ipython_session.py])], [In-process IPython terminal session and virtual streams],
  [#source("src/iyzee/tui/termkeys.py", [tui/termkeys.py])], [Keyboard event translation into terminal input bytes],
  [#source("src/iyzee/tui/vterm.py", [tui/vterm.py])], [Terminal-emulator state and scrollback via `pyte`],
  [#source("src/iyzee/tui/terminal_view.py", [tui/terminal_view.py])], [Rendering of the virtual terminal inside Textual],
  [#source("src/iyzee/tui/workers.py", [tui/workers.py])], [Small worker-facing result objects such as `LastRun`],
  [#source("src/iyzee/waveform_math.py", [waveform_math.py])], [Reusable numerical operations on waveform recordings],
)

This map is intentionally responsibility-oriented. It should help a reader
choose where new code belongs before opening an editor.

= How to extend the system

A new hardware capability should normally be added at the lowest layer that
knows enough to implement it correctly.

For a new instrument command, add a driver method rather than placing raw SCPI
into a screen. For a new laboratory operation composed from existing
driver calls, add or extend a workflow. For a reusable measurement pattern, use
a `Step` and the experiment layer. For a new way to present an existing
operation, extend the TUI without moving the operation upward into the page.

The practical decision tree is:

#figure(
  stack(
    spacing: 4pt,
    node("Does this describe bytes, framing or a session?", "yes → transport (VISA, HTTP)"),
    arrow(dir: "↓"),
    node("Does this describe one instrument's semantics?", "yes → device driver"),
    arrow(dir: "↓"),
    node("Does this compose device calls into a lab operation?", "yes → workflow / experiment"),
    arrow(dir: "↓"),
    node("Is it primarily interaction or presentation?", "yes → TUI"),
    arrow(dir: "↓"),
    node("None of the above", "re-check the boundary before adding an abstraction"),
  ),
  caption: [Put new behavior at the lowest layer that knows enough to implement it correctly.],
)

The last line is deliberate. The project benefits more from a small number of
strong boundaries than from a large number of classes that merely rename the
same concept.

= Existing documentation and document roles

This architecture guide is the conceptual entry point. It should explain the
system as a whole and then hand the reader to more specialized documents.

The other parts of this guide are its companions:

- @part-mxa covers MXA control, analyzer semantics and measurement quantities.
- @part-tui is the detailed guide to TUI/device interaction, connection behavior,
  command surfaces and direct instrument usage.
- @part-data is for the scientist: what is in each saved file, field by field, and how
  to load and check it.
- @part-rubidium is the physics.
- @part-code-map is generated from the code and shows which guide section cites which
  module, in both directions.

The architecture guide links to those parts rather than copying their command
references or measurement discussion.

The README remains the operational landing page: how to run the application,
what the pages do, and where to find the deeper technical documents.

= End-to-end mental model

A concise mental model for a contributor is:

#block(
  fill: blue-pale,
  stroke: 0.5pt + hairline,
  inset: 8pt,
  radius: 3pt,
  width: 100%,
)[
```text
User intent
    ↓
TUI / script / IPython
    ↓
application-level operation
    ↓
workflow / experiment
    ↓
instrument driver
    ↓
transport / client
    ↓
physical instrument
    ↓
verified result
    ↓
persisted data + metadata
    ↓
interactive or offline analysis```
]

At every boundary, ask two questions:

1. *Who owns this resource or piece of state?*
2. *What does the next layer need to know, and what should it not need to know?*

Most of the project's architecture follows from answering those questions
consistently.

= Maintenance rule

Keep this guide tied to the implementation. When a change alters ownership,
lifecycle, locking, worker boundaries, connection semantics, measurement
sequencing, persistence, TUI/console integration, or protocol behavior, update
the corresponding chapter in the same logical change.

Code links in this guide are written with the `code` helper and resolved against the
code at build time, so a rename or removal fails the build instead of leaving a stale
line number. Module docstrings point back with `:guide:`label``; the
checker (`scripts/check_doc_links.py`) validates both directions.

Do not preserve an explanation merely because it describes an older design. If
historical context is useful, explain it only insofar as it clarifies a current
invariant. Otherwise the architecture guide should describe the system that is
actually running today.

