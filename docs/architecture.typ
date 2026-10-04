// iyzee architecture guide

#set document(
  title: "iyzee architecture guide",
  author: "iyzee",
)

#set page(
  margin: (x: 2.2cm, y: 2cm),
  header: context [
    #set text(size: 8pt)
    #smallcaps[iyzee]
    #h(1fr)
    Architecture
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

#align(center)[
  #text(size: 24pt, weight: "bold")[iyzee architecture]
  #v(0.4em)
  #text(size: 12pt)[How the system operates, and why it is shaped this way]
  #v(0.8em)
  #text(size: 10pt)[Technical architecture guide for the `flirr` implementation]
]

#v(1em)

#block(
  fill: luma(245),
  stroke: 0.5pt + luma(200),
  inset: 9pt,
  radius: 3pt,
)[
  *Status.* This document describes the current `flirr` implementation. It is
  an architecture guide, not a generic instrument-control tutorial and not a
  complete API reference. The source code remains authoritative; when code,
  tests, and prose disagree, the implementation and its verified behavior win.
]

#align(center)[#outline(title: [Contents], indent: 1.2em)]

#pagebreak()

= What `iyzee` is

`iyzee` is a small Python control and measurement toolkit for a laboratory
setup in which several real instruments participate in one experiment. Its
center of gravity is automated noise measurement, but the architectural
problem is broader: the same physical instruments must be usable from scripts,
an interactive terminal UI, and an embedded Python console without creating
multiple incompatible copies of the control logic.

There are therefore several different ways to *use* the program, but not
several implementations of the underlying measurement machinery.

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\niyzee script        → reusable experiment / device code

iyzee-tui           → TUI composition / interaction
                        ↓
                    shared live session
                        ↓
                    Lab + handles
                        ↓
             instrument drivers / clients
                        ↓
                VISA / VICP / HTTP
                        ↓
                    hardware```\n]

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
device differences. A LeCroy VICP scope, a Keysight VISA analyzer, and a
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

= The architectural shape

At the highest level, the system can be read as a dependency graph rather than
as a pile of files.

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\n                         ┌─────────────────────┐
                         │       User          │
                         └──────────┬──────────┘
                                    │
                    ┌───────────────┴──────────────┐
                    │                              │
               iyzee script                    iyzee-tui
                    │                              │
                    │                    ┌─────────┴─────────┐
                    │                    │                   │
                    └──────────────┐   TUI pages         IPython
                                   │                       │
                                   ▼                       ▼
                           experiments / workflows     LabProxy
                                   │                       │
                                   └──────────┬────────────┘
                                              │
                                         Lab / handles
                                              │
                              ┌───────────────┼───────────────┐
                              ▼               ▼               ▼
                             MXA           LeCroy         Wavemeter
                              │               │               │
                            VISA            VICP            HTTP
                              │               │               │
                              ▼               ▼               ▼
                           hardware        hardware        hardware```\n]

A useful way to read this is from the bottom upward.

The *transport* layer moves bytes or requests. The *driver* layer assigns
meaning to those operations for one instrument. The *workflow/experiment*
layer composes driver operations into laboratory actions. The *TUI* and
*console* layers expose those actions to a human. `Lab` and its handles bind
all of that to the actual connected resources of one running session.

The arrows are intentionally one-way in responsibility. A VICP transport does
not know that a received block is an oscilloscope waveform. The oscilloscope
driver does not know whether it was called by a Textual button or a Python
script. A scope workflow does not know how a Textual widget represents a dirty
field.

= Ownership and lifecycle

One of the most important invariants is simple:

> Constructing a device is not the same thing as connecting to it.

This makes ownership explicit. The caller that decides to use a resource for a
session also decides when that resource should be opened and closed.

The application-level path is:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nInstrumentSpec
    ↓
handle = spec.make()
    ↓
handle.connect()
    ↓
handle.probe()
    ↓
Lab.handles[key] = handle```\n]

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

= `Lab`: authoritative session state

`src/iyzee/lab.py` contains a deliberately small object with one central job:
represent the instruments of the current session and own their lifecycle.

The registry of `InstrumentSpec` objects answers, "How do I construct the
handle for this named instrument?" `Lab.handles` answers, "Which instruments
are actually connected right now?"

The conceptual runtime object is therefore:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nIyzeeApp
│
├── Lab
│    ├── mx → InstrumentHandle
│    ├── scope → InstrumentHandle
│    ├── shutter → InstrumentHandle
│    └── wavemeter → InstrumentHandle
│
├── last_run
│
└── mounted TUI pages```\n]

The TUI does not need its own shadow dictionary of connected devices, and the
console does not need another shadow dictionary either. Both observe the same
`Lab` state.

This is one of the project's most valuable simplifications: there is one
answer to "is the scope connected?" inside a process, rather than one answer in
the Connect page, another in the console namespace, and another in each worker.

= Handles: one application contract, many real devices

A physical laboratory setup is heterogeneous. `iyzee` embraces that fact while
still giving higher layers a small common contract.

The `InstrumentHandle` protocol supplies:

- `connect()` and `disconnect()` for lifecycle,
- `probe()` for an active post-connect sanity check,
- `device` for access to the live driver or client,
- `alive` for cheap link-state observation where the driver can provide it,
- `lock` for synchronization of access to the physical resource.

The abstraction is deliberately about *resource management*, not about
forcing every device into one command model.

#table(
  columns: (1.35fr, 2fr, 2.3fr),
  stroke: 0.5pt,
  inset: 5pt,
  align: (left, left, left),
  [*Instrument*], [*Client/driver*], [*Transport*],
  [Keysight MXA], [`KeysightMXA`], [VISA / SCPI],
  [PSU / shutter], [`PSU` / `ShutterControl`], [VISA / PSU commands],
  [LeCroy scope], [`LeCroy`], [VICP over TCP],
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

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\n                     ┌── Sweep worker ───┐
                     │                   │
Console ─────────────┼── LockedProxy ────┤
                     │                   │
                     └────────┬──────────┘
                              │
                             RLock
                              │
                       physical instrument```\n]

The re-entrant lock matters because a workflow may already hold the resource
lock and then call through a proxy that acquires the same lock again. A plain
`Lock` would deadlock in that situation.

The scope is slightly different in implementation detail. Its handle returns
the LeCroy driver's transaction lock rather than introducing a second unrelated
lock. This is important because a VICP waveform transfer is itself a compound
transaction consisting of multiple framed reads; the same transaction boundary
therefore protects both driver-level operations and higher-level callers.

= Drivers and workflows are different layers

The distinction between `devices/scope.py` and `scope_workflows.py` is a good
example of the architecture.

The driver asks:

> How does this instrument expose channels, trigger settings, and waveform
> data?

The workflow asks:

> What useful laboratory operation should the application perform using those
> capabilities?

A driver should therefore contain instrument semantics such as a command to
set time-per-division or download a waveform block. A workflow can compose
those operations into read/modify/apply/read-back or acquire-and-save logic.

That division is what allows a workflow to be called from a TUI worker, a test,
or the console without making the workflow import Textual or know which widget
contains a value.

= Measurement execution model

The experiment layer uses one general execution pattern rather than a separate
hand-written loop for every sweep.

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nAnalyzerConfig
      ↓
prepare_analyzer()
      ↓
Step[]
      ↓
run_sequence()
      ↓
StepResult[]
      ↓
save_step_results()
      ↓
NPZ + metadata```\n]

`AnalyzerConfig` collects experiment-level analyzer state. `prepare_analyzer()`
turns that into concrete instrument setup. A `Step` represents one reproducible
measurement point, such as one RBW value in a bandwidth sweep or one frequency
coordinate in a frequency sweep.

`ExperimentContext` carries the already-connected hardware and the metadata
shared by all steps in one run. This is a deliberate boundary: a step may use
the MXA, the shutter, and run metadata, but it does not decide who owns those
resources.

== `StepResult` is the measurement contract

Every successful point becomes a `StepResult` containing:

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

= Scope architecture: the complete case study

The scope is the best example of how the project's layers cooperate because it
combines a real instrument protocol, editable state, verification, acquisition,
persistence, and background work.

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nScopeScreen
    │ user edits
    ▼
UI state / dirty state
    │ Apply
    ▼
scope_workflows
    │
    ├── read
    ├── write
    ├── verify
    └── record
    │
    ▼
LeCroy
    │
    ▼
VICPTransport
    │
    ▼
TCP socket```\n]

The page owns the human-facing state. The workflow owns the semantics of
reading, applying, verifying, acquiring, and saving. The driver owns LeCroy
commands. The transport owns framing and socket state.

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

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nknown baseline
      ↓
compare with requested values
      ↓
write only changed fields
      ↓
read back instrument state
      ↓
verified state becomes new baseline```\n]

Only the read-back result is promoted to the next trusted baseline. If readback
fails, the application must not manufacture a clean state merely because the
write call returned.

This is especially important for an acquisition button: the page should not
allow a recording to proceed merely because its own widgets look internally
consistent. The relevant criterion is that the hardware state is synchronized
and trustworthy.

== Partial application and partial acquisition

The workflows retain useful information when a multi-channel operation is
partially successful. A channel that could be read or downloaded remains in the
result while a failing channel is explicitly represented in the error state.

The same idea applies to scope acquisition. A recording can contain successful
waveforms even when another channel failed. The persisted manifest records the
failure instead of deleting the successful data or pretending the acquisition
was complete.

This is a recurring design principle in `iyzee`: preserve physically valid
information while making the invalid or uncertain part impossible to confuse
with success.

= VICP: protocol → transport → instrument

The LeCroy stack is intentionally split into three layers.

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nLeCroy semantics
      ↓
LeCroy driver
      ↓
VICP transport
      ↓
TCP```\n]

The transport in `src/iyzee/devices/vicp.py` owns framing, socket lifetime,
timeouts, partial send/receive handling, and transport invalidation. The
oscilloscope driver owns things such as channel configuration and waveform
interpretation.

This prevents protocol details from leaking into every scope workflow.

== VICP frames are not `recv()` calls

TCP provides an ordered byte stream, not message boundaries. One call to
`socket.recv(n)` can return fewer than `n` bytes even when the peer has more data
on the way.

VICP therefore has a fixed-size header followed by a payload whose length is
specified in the header. `_recv_exact()` repeatedly receives until the exact
requested number of bytes has arrived or the connection fails.

The same principle applies to sends: `_send_all()` must account for partial
writes rather than assuming one call transfers the whole frame.

The transport's `_HEADER` is a network-order structure containing the VICP
flags/version/reserved bytes and a payload length. Keeping this parsing inside
the transport means the LeCroy driver receives complete protocol messages
rather than having to reason about TCP fragmentation.

== Why a failed partial message invalidates the connection

A transport timeout is not necessarily just a slow operation. If the program
has consumed only part of a frame, it no longer knows whether bytes arriving
later belong to that response or to something else at the application level.

There are no request IDs in the protocol that let a later response be matched
back to an earlier request. Retrying on the same stream can therefore create a
worse failure:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nrequest A ─────────────►
                         response A ──X── timeout

request B ─────────────►
                         late response A
                              │
                              └── could be mistaken for B```\n]

For this reason, a mid-frame I/O failure invalidates the VICP connection. A new
connection starts from a clean stream state instead of trying to guess where
it is inside the old one.

This rule is stronger than simply setting `connected = False`. The transport
actually clears its socket/address state and closes the socket so subsequent
callers cannot accidentally keep using the corrupted stream.

== Not every protocol error kills the stream

There is an important converse detail. If the transport has completely
consumed a frame or the complete definite-length payload, then a semantic
parsing error is different from an incomplete transfer.

For example, if a payload is syntactically invalid *after the full payload has
already been read*, the byte stream is still aligned for the next message. The
connection can remain usable.

That distinction is worth documenting because it is a general transport rule:

> Lose the connection when stream alignment is uncertain; do not tear down a
> healthy stream merely because a fully received payload contains bad content.

= Transport state and link monitoring

`VICPTransport` owns a transaction lock and a connection state. `connect()`
publishes the socket only after the TCP connection and socket tuning succeed.
This avoids exposing a partially initialized transport to other callers.

`check_link()` performs cheap link-state checking and invalidates the transport
when the peer is known to have disappeared.

`ScopeHandle.alive` delegates to that mechanism. The application can therefore
notice that a scope disappeared and remove its handle from the active inventory
without first issuing a normal measurement command from the UI.

That is also why the Connect page, the console, and the scope workflow can all
see the same underlying loss of connection instead of maintaining incompatible
beliefs about the device.

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

Putting SCPI or VICP directly into a widget method seems convenient at first:
the button is right there, and the command can be issued immediately.

The cost is duplication and isolation. A second caller then needs a second copy
of the same logic, tests need to instantiate UI objects to exercise hardware
semantics, and the console cannot call the operation without reaching into UI
implementation details.

With the current architecture, a page can instead do something conceptually
like:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nread form state
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
_ui(...) on the Textual thread```\n]

The UI remains thin enough that the same underlying operation can exist without
Textual.

= Worker boundaries and UI responsiveness

Hardware latency is unbounded from the perspective of the event loop. A VISA
query can wait. A VICP transfer can timeout. A wavemeter request can stall.
Waveform conversion and plotting can also be expensive even after the hardware
has responded.

The project therefore treats the worker boundary as a concurrency boundary:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nworker
  │
  ├── blocking hardware I/O
  ├── expensive processing
  └── persistence
        │
        ▼
      _ui(...)
        │
        ▼
 Textual UI thread```\n]

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

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nUser keyboard
     ↓
Textual terminal widget
     ↓
termkeys.py
     ↓
virtual input
     ↓
IPython / prompt_toolkit
     ↓
Python execution
     ↓
LabProxy
     ↓
LockedProxy
     ↓
live instrument```\n]

The output path is the reverse integration problem:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nIPython output
     ↓
virtual terminal
     ↓
pyte
     ↓
Vterm
     ↓
TerminalView
     ↓
Textual rendering```\n]

This arrangement is valuable because it allows IPython and prompt-toolkit to
retain their own editing, completion, history, inspection, magics, and
interrupt behavior. `iyzee` only supplies the virtual terminal boundary needed
to place that existing terminal UI inside Textual.

== Why in-process matters

If the console were a separate process, the application would need a second
mechanism to communicate with connected devices and return results. That would
introduce serialization, duplicated state, and another lifecycle boundary.

Instead, the console can execute:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nlab.mx.set_rbw(24e3)
lab.mx.single_sweep_wait()
trace = lab.mx.get_trace_data(1)```\n]

against the same live MXA used by a screen worker. The handle's lock serializes
these calls with the other users of the physical device.

The result is a powerful property for exploratory laboratory work: the UI is a
convenience layer, not a wall around the underlying Python machinery.

= `LabProxy`: dynamic state without stale console globals

The console exposes one name, `lab`, rather than copying every current device
into the IPython namespace.

`LabProxy.__getattr__()` resolves against the application's current handles on
each access. Thus `lab.mx` means "the MXA that is connected *now*", not "the
object that happened to be connected when the console was initialized".

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nConnect MXA
    ↓
lab.mx        → live MXA proxy

Disconnect MXA
    ↓
lab.mx        → immediate AttributeError```\n]

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

= Data architecture

The persistence path is intentionally separated from display:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nmeasurement
    ↓
in-memory result
    ↓
numeric arrays + metadata + configuration
    ↓
NPZ + JSON manifest
    ↓
ResultsScreen / offline analysis```\n]

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
- VICP address and timeout information,
- instrument identity,
- requested and applied configuration,
- acquisition/freeze/restore state,
- waveform descriptions and statistics,
- per-channel errors,
- a checksum of the completed NPZ file.

The manifest can therefore tell a future reader both what the data is and what
went wrong, if something went wrong.

== Raw versus calibrated versus display data

The distinction matters especially for the scope. Raw signed ADC codes are not
the same object as calibrated engineering values, and neither one is identical
to the downsampled data sent to a terminal plot.

The architecture keeps these meanings separate:

#table(
  columns: (1.35fr, 3.4fr),
  stroke: 0.5pt,
  inset: 5pt,
  [*Representation*], [*Role*],
  [Raw acquisition], [The numerical samples as returned by the instrument, when preserved],
  [Calibrated data], [Engineering values derived using instrument-reported scaling],
  [Derived data], [Quantities computed for analysis, such as differences or statistics],
  [Display preview], [A reduced or transformed representation suitable for interactive rendering],
)

The governing rule is:

> Display representations are disposable; recorded measurement data is not.

= Configuration and connection discovery

Instrument addresses and the data root are configurable through environment
variables and `config.toml`. The important architectural point is that this
configuration answers *where the hardware is* and *where data belongs*; it is
not a runtime container that every component mutates.

The flow is roughly:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nconfiguration sources
       ↓
   config.py
       ↓
 InstrumentSpec
       ↓
 handle construction
       ↓
 explicit connect + probe```\n]

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

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nshutdown request
      ↓
stop / interrupt console work
      ↓
collect connected handles
      ↓
parallel per-instrument cleanup
      ↓
respect each instrument lock
      ↓
one wedged device must not block all cleanup forever```\n]

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
exceptions such as wavemeter readout errors, VISA failures, or VICP timeouts are
part of the measurement's truth.

== Hardware truth beats UI optimism

A form field is an intention. A verified readback is evidence. The scope UI is
therefore based on synchronization against an instrument-reported baseline,
not only on whether its own fields are internally valid.

== Stream integrity is an error property

A transport error that leaves the stream partially consumed is fundamentally
different from a semantic error on a fully consumed response. This distinction
keeps recovery behavior conservative without making every bad payload fatal.

== Partial success can still be useful

A successful channel should not disappear merely because another channel
failed. The manifest records the error so downstream analysis can use the valid
part without confusing it with a complete acquisition.

== Errors should cross boundaries explicitly

Drivers raise driver/protocol errors. Workflows preserve or contextualize them.
Workers turn them into UI-facing outcomes. Persistence records them when they
matter to the interpretation of the run.

No layer should simply swallow an error because it is inconvenient for the next
layer.

= A complete sweep execution trace

The following trace connects the conceptual layers to the implementation names.

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nuser
 ↓
SweepScreen
 ↓
AnalyzerConfig
 ↓
Step[]
 ↓
Textual worker
 ↓
run_sequence()
 ↓
BandwidthStep / FrequencyStep
 ↓
handle.lock / LockedProxy boundary
 ↓
KeysightMXA
 ↓
PyVISA
 ↓
MXA
 ↓
StepResult
 ↓
save_step_results()
 ↓
on_step()
 ↓
reduced preview / progress update
 ↓
Textual UI```\n]

The crucial observation is that `SweepScreen` does not own the sweep algorithm.
It composes existing experiment primitives, supplies a callback for live
feedback, and presents the result.

= A complete scope-acquisition trace

The analogous scope path is:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nuser presses Acquire
 ↓
ScopeScreen validates synchronized state
 ↓
snapshot relevant configuration
 ↓
worker
 ↓
acquire_scope_recording()
 ↓
LeCroy driver
 ↓
VICPTransport
 ↓
TCP socket
 ↓
waveform frame(s)
 ↓
decode + scale
 ↓
ScopeAcquisition
 ↓
save_scope_acquisition()
 ↓
reduced preview
 ↓
Textual UI / ResultsScreen```\n]

This is why the scope code is larger than a single widget method might suggest.
The operation spans UI preconditions, device semantics, protocol mechanics, data
conversion, persistence, and presentation. The architecture gives each part a
place instead of making one giant method responsible for all of them.

= "Why is it like this?"

The architecture becomes clearer when the main decisions are read as answers
to concrete failure modes.

#table(
  columns: (2.25fr, 3.2fr),
  stroke: 0.5pt,
  inset: 5pt,
  [*Decision*], [*Reason*],
  [Devices do not connect during construction], [Clear ownership, deterministic tests, explicit lifecycle],
  [`Lab` owns connected handles], [One authoritative session inventory],
  [Locks live with handles or transports], [Synchronization follows the physical resource],
  [TUI delegates reusable operations], [Hardware semantics remain testable and callable without Textual],
  [Scope workflows are plain functions], [Console and scripts can reuse the same laboratory operations],
  [Blocking I/O runs in workers], [The UI event loop must remain responsive],
  [VICP drops the connection after mid-stream failure], [No request IDs means retrying a corrupted stream risks misinterpreting a late response],
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
  stroke: 0.5pt,
  inset: 5pt,
  [*Area*], [*What the test should protect*],
  [Lifecycle], [Construction does not unexpectedly connect; connect/probe failures leave no registered half-open handle; close is safe],
  [Handles], [Shared synchronization and the common adapter contract],
  [`Lab`], [Connected inventory, dead-link removal, and bounded shutdown],
  [Experiment core], [Step ordering, callbacks, failure policies, and explicit run status],
  [VICP], [Exact framing, partial reads/writes, timeout behavior, and connection invalidation],
  [Scope workflows], [Readback invariants, clean/dirty state, partial results, and acquisition preconditions],
  [Persistence], [Atomic files, numeric-only archives, manifests, status, and checksums],
  [Workers], [Blocking operations remain off the UI thread and results return through the UI boundary],
  [Console], [Live lookup, completion, history isolation, and shared locking],
  [TUI], [User-visible navigation, command semantics, readiness, and interaction behavior],
)

A strong architectural test is one that would fail if someone accidentally
reintroduced the old wrong behavior. For example, a VICP test should not merely
show that a happy-path frame parses; it should also prove that a short payload
or timeout cannot leave the transport pretending that its stream is valid.

Likewise, a scope test should be willing to fail when requested settings and
verified settings diverge. That is precisely the kind of bug the architecture
exists to make visible.

= Module and responsibility map

The repository is easier to maintain when each file can be described by the
question it answers.

#table(
  columns: (2.15fr, 3.2fr),
  stroke: 0.5pt,
  inset: 5pt,
  [*Source*], [*Responsibility*],
  [`lab.py`], [Authoritative inventory and lifecycle of connected instruments],
  [`devices/base.py`], [Shared VISA lifecycle and PSU channel infrastructure],
  [`devices/handles.py`], [Uniform resource adapters, shared locks, and `LockedProxy`],
  [`devices/mxa.py`], [Keysight MXA SCPI/VISA semantics],
  [`devices/power.py`], [PSU operations and optical shutter control],
  [`devices/scope.py`], [LeCroy instrument semantics over VICP],
  [`devices/vicp.py`], [Thread-safe VICP framing, socket lifecycle, and stream integrity],
  [`devices/wavemeter.py`], [Stateless wavemeter HTTP client],
  [`experiment/core.py`], [Step protocol, run context, step results, failure recording, and sequencing],
  [`experiment/procedures.py`], [Reusable analyzer configurations, steps, and sweep builders],
  [`experiment/io.py`], [Recording formats, atomic persistence, figures, and numeric helpers],
  [`scope_workflows.py`], [Reusable scope read/apply/verify/acquire/save operations],
  [`tui/app.py`], [Application shell, page switching, shared state, commands, and shutdown],
  [`tui/screens/page.py`], [Common page mechanics, validation, readiness, and worker-to-UI handoff],
  [`tui/screens/`], [Concrete user workflows and presentation],
  [`tui/ipython.py`], [`LabProxy`, shell configuration, history selection, and API introspection],
  [`tui/ipython_session.py`], [In-process IPython terminal session and virtual streams],
  [`tui/termkeys.py`], [Keyboard event translation into terminal input bytes],
  [`tui/vterm.py`], [Terminal-emulator state and scrollback via `pyte`],
  [`tui/terminal_view.py`], [Rendering of the virtual terminal inside Textual],
  [`tui/workers.py`], [Small worker-facing result objects such as `LastRun`],
  [`waveform_math.py`], [Reusable numerical operations on waveform recordings],
)

This map is intentionally responsibility-oriented. It should help a reader
choose where new code belongs before opening an editor.

= How to extend the system

A new hardware capability should normally be added at the lowest layer that
knows enough to implement it correctly.

For a new instrument command, add a driver method rather than placing raw SCPI
or VICP into a screen. For a new laboratory operation composed from existing
driver calls, add or extend a workflow. For a reusable measurement pattern, use
a `Step` and the experiment layer. For a new way to present an existing
operation, extend the TUI without moving the operation upward into the page.

The practical decision tree is:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nDoes this describe bytes / framing?
        │ yes → transport
        │
        no
        ↓
Does this describe one instrument's semantics?
        │ yes → device driver
        │
        no
        ↓
Does this compose device calls into a lab operation?
        │ yes → workflow / experiment
        │
        no
        ↓
Is it primarily interaction or presentation?
        │ yes → TUI
        │
        no
        ↓
Re-evaluate the boundary before adding another abstraction.```\n]

The last line is deliberate. The project benefits more from a small number of
strong boundaries than from a large number of classes that merely rename the
same concept.

= Existing documentation and document roles

This architecture guide is the conceptual entry point. It should explain the
system as a whole and then hand the reader to more specialized documents.

The current documentation has two useful companions:

- `docs/tui-and-devices.typ` is the detailed guide to TUI/device interaction,
  connection behavior, command surfaces, and direct instrument usage.
- `docs/mxa-and-measurements.typ` covers MXA control, analyzer semantics,
  measurement quantities, and the associated physics.

The architecture guide should link to those documents rather than copying their
full command references or measurement discussion.

The README remains the operational landing page: how to run the application,
what the pages do, and where to find the deeper technical documents.

= End-to-end mental model

A concise mental model for a contributor is:

#block(\n  fill: luma(245),\n  stroke: 0.5pt + luma(205),\n  inset: 8pt,\n  radius: 3pt,\n  width: 100%,\n)[\n```text\nUser intent
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
interactive or offline analysis```\n]

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

Do not preserve an explanation merely because it describes an older design. If
historical context is useful, explain it only insofar as it clarifies a current
invariant. Otherwise the architecture guide should describe the system that is
actually running today.

*Always strive for improvement, always be humble.*
