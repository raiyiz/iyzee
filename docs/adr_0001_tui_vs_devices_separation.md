# iyzee architecture direction — refined plan

## Status of the core principle

> **Python first, TUI second: the TUI organizes, displays, and controls the
> machinery; it does not contain machinery a script could reasonably need.**

This is correct and worth stating explicitly as a standing rule. It is also
**already substantially true** in this codebase, which changes the shape of
the work from "build this" to "close the specific gaps and stop new ones
from opening":

| Layer | Status |
|---|---|
| Device I/O (`mxa.py`, `scope.py`, `shutter.py`, `wavemeter.py`, `power.py`) | Already pure — no Textual import anywhere. |
| Experiment workflow (`experiment/core.py`, `procedures.py`, `io.py`) | Already pure. `main.py` is a working, script-only path today: connect, run a sweep, plot, save — zero TUI. |
| Progress seam | Already exists: `run_sequence(..., on_step=callback)` (`StepCallback` in `core.py`). The TUI turns this into progress/plots; a script can use the same seam for a print statement. No new work needed here. |
| Console access to live instruments | Already the target pattern: `LabProxy`/`LockedProxy` (`ipython.py`, `instruments.py`) give `lab.scope`, `lab.mx`, etc., identically from console or (indirectly) TUI. |
| Scope screen (`tui/screens/scope.py`) | **Violates the principle.** Channel/trigger apply logic and waveform acquisition live as private methods on the Textual widget itself. This is the concrete gap to close. |
| Instrument locking (`instrument_locks`) | **Violates the principle, more fundamentally than the document states.** Owned by `IyzeeApp`, not by the device layer — see below. |

## Two disagreements with the original document, and why

**1. A pluggable persistence backend (`store.save`/`store.load`) is premature.**
There is one format (numeric `.npz` + JSON sidecar), used from about three
call sites. A formal interface for a single implementation is indirection
without payoff — it's easy to introduce later, when (if) a second backend is
a real requirement, not a hypothetical one. Building it now is speculative
generality this codebase doesn't currently need.

**2. The `devices`/`experiments` rename plus `Result` objects with
`.save()`/`.to_dataframe()`/`.plot()` methods is a bigger, different change
than "extract logic out of the TUI," and I'd keep it separate — or drop it.**

- It reverses the codebase's current, consistent convention: plain,
  frozen dataclasses (`StepResult`) plus free functions that operate on them
  (`save_step_results`, `build_figure`, `difference_series`). That style is
  already working, is easy to test in isolation, and composes over any
  number of results without needing a wrapper type. Methods-on-results is a
  different philosophy, not a refactor of the same one.
- It doesn't map onto the actual domain shape without first inventing a new
  concept: a run here is a **list** of `StepResult`s, not one object, so
  `result.save(...)` has no obvious referent until a `Run` wrapper exists —
  which is itself a real design decision, not incidental cleanup.
- The worked example in the source document
  (`experiments.bandwidth(mxa, center=10.5e9, span=100e6, points=201)`)
  doesn't correspond to how `BandwidthStep` actually works (it sweeps a list
  of RBW values — there's no center/span/points concept in it today). It
  reads as illustrative rather than a real target signature; worth being
  explicit about that so it doesn't quietly become a spec.

Net: keep the current module names (`iyzee.mxa`, `iyzee.scope`,
`iyzee.experiment`) and the current dataclass-plus-free-function style. If a
`devices`/`experiments` namespace and richer result objects turn out to be
genuinely wanted later, that's its own proposal, evaluated on its own,
not bundled into "get Scope out of the TUI."

`LastRun` → `ExperimentRun`/`SweepResult`: a legitimate but cosmetic
observation (it's already a plain, Textual-free dataclass; only *who
currently sets it* is TUI-flavored). Low priority — fold into a later pass
rather than treating as a standalone task.

## Where I'd go further: instrument locking is a live blocker, not a someday

Checked directly: `instrument_locks` is created and owned by `IyzeeApp`
(`app.py`), and `ScopeScreen`'s workers reach up to
`self.iyzee_app.instrument_locks["scope"]`. That means, **today**, a plain
script cannot get the same safe-concurrent-access guarantee a TUI action
gets — there is no app object to ask for a lock from. The document's own
diagram (sweep code and console/script sharing one instrument, needing
synchronization "below the TUI") already implies this must move; I'd treat
it as part of the *same* piece of work as extracting Scope's operations,
not a separate future step — extracting the operations without moving the
lock would just relocate the same problem one file over.

Concretely: each `InstrumentHandle` (`ScopeHandle`, etc., in
`instruments.py`) should own its own lock, or `LockedProxy` should, so that
constructing a handle — with or without a running `IyzeeApp` — gets correct
synchronization for free. `IyzeeApp.instrument_locks` becomes unnecessary
once this moves; `ConnectScreen`/`ScopeScreen`/`SweepScreen` stop reaching
into the app for a lock and just use the handle they already have.

## Revised target layering

Unchanged from the source document — this part is correct and worth
keeping as-is:

```text
                         ┌─────────────────────────┐
                         │           TUI            │
                         │ composition / controls   │
                         │ navigation / rendering    │
                         │ interaction / progress    │
                         └────────────┬─────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │   Python API / core       │
                         │ device operations         │
                         │ experiments                │
                         │ result models              │
                         │ persistence                │
                         │ synchronization (moves here)│
                         └────────────┬─────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │     device libraries      │
                         │   VISA / TCP / HTTP       │
                         └─────────────────────────┘
```

```text
                    Python API / core
                     /             \
                    /               \
                  TUI             Console / script
                   │                 │
                   └───────┬─────────┘
                           │
                    same underlying API,
                    same synchronization,
                    no app object required
```

## Phased plan

### Phase 1 — Scope: extract operations, relocate locking (concrete, ready to start)

1. Pull `ChannelSettings`, `TriggerSettings`, `apply_channel_settings(scope, settings)`,
   `apply_trigger_settings(scope, settings)`, `acquire_waveforms(scope, channels)` out
   of `tui/screens/scope.py` into a new module sitting beside `scope.py`
   (mirroring how `experiment/` sits beside `mxa.py`) — no Textual import.
2. Move lock ownership from `IyzeeApp.instrument_locks` down to the handle
   (`ScopeHandle`/`LockedProxy`) so the new functions above take a scope
   handle (or driver + its own lock) and are safe to call from a script, the
   console, or the TUI identically.
3. `ScopeScreen`'s workers shrink to: read form → build a settings value →
   call the plain function → format the result for display. No behavior
   change from the user's perspective; this is a structural move.
4. Result: `apply_channel_settings(lab.scope, [...])` works from the console
   today, and the same call works from a bare script with its own `LeCroy`
   instance, with the same safety guarantee, no `IyzeeApp` involved.

### Phase 2 — Make it the standing convention, not a one-off

Any new screen built after this point follows the same shape by default:
plain functions/dataclasses for the operation, a thin screen that calls
them. Worth a short note in the repo (README or a module docstring
convention, matching how this document itself works) so it isn't
rediscovered by hand each time.

### Phase 3 — Deferred, not rejected, revisit only if a real need shows up

- Pluggable persistence backend, if a second format is ever actually needed.
- `devices`/`experiments` namespace + richer `Result`/`Run` objects with
  methods, if/when there's a concrete reason (a second instrument-family
  workflow complex enough to want it, external users of the library, etc.)
  — evaluated as its own proposal against the codebase's existing
  free-function convention, not assumed.
- `LastRun` rename, folded into whichever of the above actually happens
  first, rather than done alone.
