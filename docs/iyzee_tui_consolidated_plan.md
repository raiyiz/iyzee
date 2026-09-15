# iyzee TUI Consolidation and Follow-up Plan

## 1. Goal

Simplify the `iyzee` terminal UI so that the code structure follows the logical architecture rather than Textual's implementation concepts.

Target:
- fewer nested packages,
- fewer plumbing modules,
- one obvious module per user-facing feature,
- app-wide state and concurrency owned by `IyzeeApp`,
- experiment logic independent of the TUI,
- hardware drivers independent of both,
- no behavior changes during structural consolidation.

The TUI should remain a thin application layer over:

1. instrument drivers,
2. the experiment/run layer,
3. Textual for interaction and concurrency.

---

## 2. Logical architecture

### Layer A — Hardware / device drivers

```text
src/iyzee/
├── base.py
├── mxa.py
├── power.py
├── scope.py
└── wavemeter_readout.py
```

Responsibilities:
- actual hardware communication,
- Python APIs rather than raw SCPI/protocol strings where appropriate,
- device-specific connection handling,
- no Textual UI logic.

Keep this layer separate from the TUI and experiment orchestration.

### Layer B — Experiment / measurement logic

```text
src/iyzee/experiment/
├── __init__.py
├── config.py
├── persistence.py
├── plotting.py
├── procedures.py
├── runner.py
└── step.py
```

Responsibilities:
- measurement steps,
- instrument preparation,
- sequences,
- `StepResult`,
- persistence,
- reusable procedures,
- experiment execution primitives.

This layer should stay UI-independent so experiments remain reusable outside the terminal application.

### Layer C — TUI

```text
src/iyzee/tui/
├── __init__.py
├── app.py
├── app.tcss
├── connect.py
├── console.py
├── instruments.py
├── sweep.py
└── traces.py
```

Responsibilities:
- `app.py`: application root, navigation, shared state, locks, run state
- `connect.py`: connection UI and connection/disconnection workers
- `sweep.py`: sweep configuration, execution, plotting, persistence
- `traces.py`: browsing saved `.npz` runs
- `console.py`: embedded IPython, input/editor behavior, completion/history/help
- `instruments.py`: TUI-facing instrument registry, adapters, and locking proxy
- `app.tcss`: styling

No additional `screens/`, `widgets/`, `navigation/`, `workers/`, or `ipython/` layers should be kept unless a future requirement gives one a genuinely independent reason to exist.

---

## 3. Consolidation decisions

### 3.1 Flatten the screen modules

Completed.

The previous screen hierarchy was flattened so feature modules sit directly under `iyzee.tui`:

```text
tui/connect.py
tui/console.py
tui/sweep.py
tui/traces.py
```

Why:
- these modules represent user-facing features,
- they are not generic Textual infrastructure,
- the flatter structure makes ownership obvious,
- imports become easier to discover.

Avoid recreating a `tui/screens/` hierarchy for the same features.

### 3.2 Consolidate the console

Completed.

The previous console implementation was split across multiple tightly coupled modules. It is now consolidated into:

```text
tui/console.py
```

It owns:
- `ConsoleScreen`
- `IyzeeConsole`
- `_ConsoleInput`
- `IyzeeIPython`
- `LabProxy`
- `ExecutionOutput`
- completion,
- history,
- inspection/help behavior,
- Vim-style input behavior,
- background execution,
- pager handling.

Why:
- these pieces form one coherent feature,
- splitting them created implementation-level boundaries rather than architectural ones,
- the console needs shared access to the same app state, IPython shell, and input widget.

### 3.3 Remove the navigation infrastructure

Completed.

Remove the old `tui/navigation/` abstraction and its policy tests.

Use Textual-native navigation:

```python
BINDINGS = [...]
MODES = {...}
DEFAULT_MODE = "connect"
```

Navigation responsibilities belong to `IyzeeApp`, not a separate policy object.

Keep both:
- ordinary convenience bindings (`c`, `s`, `t`, `i`),
- priority `F1`–`F4` bindings.

The priority function-key bindings are important because they remain usable even when the console/editor widget has focus.

### 3.4 Remove `workers.py`

Completed.

`workers.py` was not the actual thread dispatcher and did not provide a distinct reusable concurrency abstraction.

The real background execution is Textual-native:

```python
@work(thread=True, ...)
```

Use that directly in the owning feature:
- `ConnectScreen`
- `SweepScreen`
- `IyzeeConsole`

Obsolete result/progress wrappers were removed from the worker module.

Do not recreate a generic worker module unless a real cross-feature abstraction appears later.

---

## 4. Concurrency model

The intended concurrency model is:

### UI thread

Textual's event loop owns:
- widget updates,
- navigation,
- focus,
- reactive UI state,
- screen transitions.

No blocking hardware or long-running execution should happen on this thread.

### Background work

Blocking operations run through Textual workers:

```python
@work(thread=True, ...)
```

Current categories:
- connect/disconnect,
- sweep execution,
- IPython command execution.

Workers update UI state through:

```python
self.app.call_from_thread(...)
```

or equivalent Textual-safe callbacks.

### Instrument synchronization

`IyzeeApp` owns one shared lock per physical instrument name.

Those same locks are used by:
- connection operations,
- sweep execution,
- console access through `LockedProxy`.

This gives the whole TUI one synchronization policy.

### What not to add

Do not introduce:
- a custom thread dispatcher,
- a generic queue abstraction,
- a worker pool,
- a second concurrency layer,
- an app-wide background manager,

unless a future requirement actually needs one.

Consider dedicated infrastructure later only if the application gains requirements such as:
- multiple independent concurrent jobs,
- work queues,
- cancellation of several independent jobs,
- CPU-bound multiprocessing,
- cross-feature scheduling.

For the current application, Textual workers are sufficient.

---

## 5. App-level state ownership

`IyzeeApp` is the owner of shared TUI application state.

It owns:
- live instrument handles,
- instrument locks,
- the latest run metadata.

`LastRun` belongs in `app.py` because it describes application state, not worker infrastructure.

Conceptually:

```python
@dataclass(frozen=True)
class LastRun:
    kind: str
    results: list[StepResult]
    path: Path | None
```

The app stores the current `last_run` and features update/read it through the app.

This avoids worker modules becoming accidental state containers.

---

## 6. Instrument abstraction

Keep:

```text
tui/instruments.py
```

This module owns the TUI-facing abstraction layer:
- `InstrumentSpec`
- `InstrumentHandle`
- `_VisaHandle`
- `ShutterHandle`
- `ScopeHandle`
- `WavemeterHandle`
- `LockedProxy`
- `INSTRUMENTS`

The important distinction is:

```text
tui/instruments.py = definitions, adapters, registry, locking proxy
app.py             = live instances, application state, locks
```

Do not merge these casually.

The hardware driver classes should also remain outside this module. `tui/instruments.py` is an application-facing adapter/registry layer, not a replacement for the actual hardware drivers.

---

## 7. Console architecture

`tui/console.py` owns the complete embedded-console feature.

The exposed environment should continue to provide live lab-facing objects, such as:

```text
lab.mx
lab.shutter
lab.scope
lab.results
lab.last_run
lab.connected
```

`LabProxy` should read current app state rather than caching stale handles.

Conceptually:

```text
Textual app state
        │
        ▼
    LabProxy
        │
        ▼
 embedded IPython
```

This keeps the console synchronized with connection/sweep state.

The console module is also responsible for:
- command execution,
- completion,
- history,
- inspection,
- Vim-style editing,
- output presentation,
- focus behavior,
- pager safety.

---

## 8. Console help / pager bug

### Symptom

Typing `?` or invoking IPython inspection/help could cause terminal escape sequences, mouse-control sequences, or similar garbage to appear in the Textual interface, for example:

```text
113;;113u27u127u127...
[<35;...M
```

The behavior could also interfere with the terminal event loop.

### Cause

Embedded IPython's pager path assumes it owns a real terminal frontend.

That assumption is invalid inside a Textual application, where Textual already owns:
- keyboard input,
- mouse input,
- terminal rendering,
- raw terminal state.

### Fix

Force IPython's pager hook to render through IPython's display mechanism rather than trying to take over the terminal:

```python
from IPython.core.page import display_page

...

self.shell.set_hook(
    "show_in_pager",
    display_page,
    priority=1000,
)
```

This keeps help/inspection output inside the application.

### Rule going forward

Embedded IPython must never take over the real terminal for:
- paging,
- raw input,
- mouse control,
- terminal mode switching.

Anything that assumes a standalone interactive terminal needs to be adapted to the embedded frontend.

### Regression coverage

Test at least:

```text
?
??
object?
```

and verify that:
- output remains inside the TUI,
- no pager takes over,
- no ANSI/mouse garbage is rendered,
- normal input remains functional afterward.

---

## 9. Console input follow-up work

The next focused console task is robustness of the embedded input path.

### Inspection

Verify:
- `?`
- `??`
- `object?`
- inspection on representative objects.

### Input focus

Verify:
- returning to console automatically focuses the input,
- focus remains stable while output is appended,
- switching away and back restores the expected focus.

The current console screen uses:

```python
AUTO_FOCUS = "#console-input"
```

### Vim behavior

Preserve the existing two-stage Escape behavior:

1. first `Esc` changes INSERT → NORMAL and keeps focus,
2. second `Esc` in NORMAL blurs the editor and returns control to app-level navigation.

A single Escape should not unexpectedly abandon the console input.

### Navigation from focused editor

Verify `F1`–`F4` work while the input widget has focus.

Also keep ordinary:

```text
c
s
t
i
```

as convenience bindings where appropriate.

Do not reintroduce a global custom key parser simply to recover navigation behavior.

### Mouse behavior

Verify normal Textual mouse interactions do not get contaminated by embedded IPython behavior.

---

## 10. Traces and Sweep

### Traces

Keep:

```text
tui/traces.py
```

Responsibilities:
- browse saved runs,
- read existing `.npz` persistence,
- present results in the TUI.

Do not introduce a new storage format as part of architectural cleanup.

The traces screen should remain a consumer of the existing experiment persistence layer.

### Sweep

Keep:

```text
tui/sweep.py
```

Responsibilities:
- sweep form/configuration,
- validation,
- running the experiment in a background worker,
- progress/status presentation,
- plotting,
- abort handling,
- saving results,
- updating `app.last_run`.

The sweep module should use experiment-layer primitives such as:

```text
AnalyzerConfig
prepare_analyzer
ExperimentContext
run_sequence
save_step_results
```

The measurement algorithms themselves should remain in `experiment/`.

Do not move experiment logic into the TUI merely to reduce the number of files.

The correct boundary is:

```text
Sweep UI
    │
    ▼
experiment primitives
    │
    ▼
hardware drivers
```

---

## 11. Connect, documentation, testing, and completion criteria

### Connect

Keep:

```text
tui/connect.py
```

Responsibilities:
- connection UI,
- connection/disconnection workers,
- instrument table/status updates,
- shared instrument locks,
- error handling/reporting.

There is no need for a separate connection worker abstraction.

The screen owns the worker because the worker exists solely to implement this feature.

### Documentation cleanup

Update:

```text
docs/tui-and-devices.typ
```

It contains stale references to the old architecture.

Replace references to:

```text
tui/screens/
tui/widgets/
tui/navigation/
tui/ipython.py
tui/workers.py
NavigationPolicy
```

with the current flat structure and actual execution model.

The documentation should describe:
- Textual-native navigation,
- Textual `@work(thread=True, ...)`,
- shared instrument locks,
- app-owned `LastRun`,
- embedded IPython/pager handling.

Also search `README.md` and the repository for stale architectural references before considering cleanup complete.

Useful search targets:

```text
tui/screens
tui/widgets
tui/navigation
tui/workers
tui/ipython
NavigationPolicy
StepProgress
ConnectOutcome
```

### Testing strategy

App:

```text
tests/test_app.py
```

Cover:
- initial mode,
- switching between modes,
- F1–F4 navigation,
- console focus behavior.

Connect:

```text
tests/test_connect_screen.py
```

Cover connection state transitions, table/UI updates, and worker behavior where practical.

Console:

```text
tests/test_console_screen.py
tests/test_console_vim_input.py
tests/test_ipython.py
```

Cover screen integration, input widget behavior, Vim mode transitions, focus behavior, `LabProxy`, shell execution, completion/history, help/inspection, and pager safety.

Sweep:

```text
tests/test_sweep_screen.py
```

Cover form parsing, validation, worker lifecycle, results handoff, and persistence/plotting integration where appropriate.

Experiment tests remain independent of Textual.

The old navigation-policy tests stay removed because the abstraction they tested no longer exists.

### Validation / CI

Run:

```text
ruff check
ruff format --check
mypy
pytest
```

Then exercise the TUI integration points specifically:

```text
import iyzee.tui
```

Verify:
- all modes activate,
- F1–F4 work,
- console input gets focus when returning to console,
- first Escape enters NORMAL without blurring,
- second Escape blurs,
- F1–F4 still work while the console editor is focused,
- `?` does not invoke an external pager,
- help output stays in the TUI,
- background work does not block the UI loop,
- instrument access is serialized through shared locks,
- experiment tests remain green.

### Commit strategy

Keep structural and behavioral changes separated whenever practical.

Recommended grouping:

**A — Flatten feature modules**

Move:

```text
screens/* → tui/*.py
```

while preserving behavior.

**B — Consolidate the console**

Merge:

```text
screens/console.py
widgets/console.py
ipython.py
```

into:

```text
tui/console.py
```

**C — Remove obsolete infrastructure**

Remove:

```text
navigation/
workers.py
```

and move `LastRun` to app-owned state.

**D — Public import cleanup**

Update `tui/__init__.py` and internal imports.

**E — Pager/input robustness**

Fix embedded IPython terminal assumptions and add regression tests.

### Do not consolidate

Keep the following boundaries:

- `experiment/` remains structured because its modules represent meaningful domain responsibilities.
- Physical instrument drivers remain separate from the TUI.
- `tui/instruments.py` stays separate from `app.py`.
- Avoid generic `utils.py`, `helpers.py`, `common.py`, `worker.py`, or `navigation.py` modules unless there is a concrete repeated responsibility.

### Target mental model

```text
iyzee/
│
├── base.py
├── mxa.py
├── power.py
├── scope.py
├── wavemeter_readout.py
│
├── experiment/
│   ├── config.py
│   ├── persistence.py
│   ├── plotting.py
│   ├── procedures.py
│   ├── runner.py
│   └── step.py
│
└── tui/
    ├── app.py
    ├── connect.py
    ├── console.py
    ├── instruments.py
    ├── sweep.py
    └── traces.py
```

Ownership:

```text
hardware drivers
       │
       ▼
experiment layer
       │
       ▼
TUI feature modules
       │
       ▼
IyzeeApp coordinates:
- shared state
- navigation
- instrument locks
- last-run state
```

### Current status

Completed:
- native Textual navigation,
- F1–F4 priority navigation bindings,
- console focus restoration,
- screen-module flattening,
- console consolidation,
- removal of obsolete navigation infrastructure,
- removal of `workers.py`,
- `LastRun` moved to app-owned state,
- convenient public TUI exports,
- embedded IPython pager fix,
- native navigation/focus regression coverage.

Remaining:
- documentation cleanup,
- console-input robustness checks,
- final CI verification,
- repository-wide search for stale architectural references,
- keep unrelated feature work out of structural cleanup commits.

### Guiding principle

Use the smallest number of modules that each have a clear architectural reason to exist.

A module should survive when it represents at least one of:
- a meaningful domain boundary,
- a user-facing feature,
- a stable abstraction,
- genuinely reusable infrastructure.

A module should disappear when it exists only because of an earlier Textual implementation split.

For the current TUI, this:

```text
app.py
connect.py
console.py
instruments.py
sweep.py
traces.py
```

is preferable to:

```text
screens/
widgets/
navigation/
workers/
```

The next architectural priority is **terminal-safe, predictable console/input behavior** while preserving the flat ownership model and keeping the experiment/hardware boundaries intact.
