# iyzee architecture direction — refined plan

## Status of the core principle

Refab implementation status: the original scope extraction and lock-ownership phase is complete. This ADR now records the resulting architecture and the remaining maintenance work.

> **Python first, TUI second: the TUI organizes, displays, and controls the
> machinery; it does not contain machinery a script could reasonably need.**

This is correct and worth stating explicitly as a standing rule. It is also
**already substantially true** in this codebase, which changes the shape of
the work from "build this" to "close the specific gaps and stop new ones
from opening":

| Layer | Status |
|---|---|
| Device I/O (`mxa.py`, `scope.py`, `power.py`, `wavemeter_readout.py`) | Pure Python — no Textual import. `ShutterControl` uses explicit connect/disconnect lifecycle. |
| Experiment workflow (`experiment/core.py`, `procedures.py`, `io.py`) | Already pure. `main.py` is a working, script-only path today: connect, run a sweep, plot, save — zero TUI. |
| Progress seam | Already exists: `run_sequence(..., on_step=callback)` (`StepCallback` in `core.py`). The TUI turns this into progress/plots; a script can use the same seam for a print statement. No new work needed here. |
| Console access to live instruments | Already the target pattern: `LabProxy`/`LockedProxy` (`ipython.py`, `instruments.py`) give `lab.scope`, `lab.mx`, etc., identically from console or (indirectly) TUI. |
| Scope screen (`tui/screens/scope.py`) | Implemented: scope read/apply/acquire/persist operations live in `scope_workflows.py`; the screen owns form state, workers, plotting, and reporting. |
| Instrument locking | Implemented: handles own re-entrant locks, and `ScopeHandle` shares the LeCroy driver transaction lock. |

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

## Implemented refab changes

The original Phase 1 concern is now resolved on `refab`. Scope operations are in `scope_workflows.py`, the TUI supplies the presentation seam, and instrument handles own the synchronization used by screens and the embedded console.

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

## Follow-up plan

### Done in refab

The original Phase 1 extraction is implemented. The standing convention is plain Python operations and dataclasses below the TUI, with handle-owned locking where shared hardware access needs serialization.

### Remaining maintenance

New screens should continue this shape by default. Future work can focus on coverage expansion, metadata schema evolution, and reduction of legacy compatibility APIs rather than another architectural rewrite.

### Deferred by design

- A pluggable persistence backend, unless a second storage format becomes a real requirement.
- A `devices`/`experiments` namespace or richer result/run objects, if a concrete external use case justifies them.
- Renaming `LastRun` when a broader run model is actually needed.
