# ADR 0001: Python first, TUI second

**Status:** accepted and implemented.

## Decision

The TUI organizes, displays and controls the machinery; it does not contain
machinery a script could reasonably need. Anything that operates an instrument
or runs a measurement is a plain function or dataclass with no Textual import,
callable identically from a script, the IPython console (`lab`) or a screen.

```text
        TUI                     Console / script
         │                            │
         └─────────────┬──────────────┘
                       ▼
        experiment/ + scope_workflows.py     operations, results, persistence
                       ▼
        mxa.py / scope.py / power.py / ...   device drivers (VISA / TCP / HTTP)
```

## What this means in the code

- **Scope operations** live in `scope_workflows.py` (`ChannelSettings`,
  `apply_channel_settings`, `apply_and_verify_channel_settings`,
  `acquire_scope_recording`, `save_scope_acquisition`). `ScopeScreen` reads the
  form, calls them, and formats the result.
- **Locking belongs to the instrument, not the app.** Each `InstrumentHandle`
  owns its lock (for the scope, the driver's own transaction lock), so
  `apply_channel_settings(lab.scope, [...])` or a bare script with its own
  `LeCroy` gets the same safe concurrent access as a TUI button, with no
  `IyzeeApp` involved.
- **Progress seam:** `run_sequence(..., on_step=callback)` in
  `experiment/core.py`. The TUI turns the callback into progress and plots; a
  script can print.
- **New screens** follow the same shape: operations in a module beside the
  driver they operate on, a thin screen on top.

## Deliberately not done

- **A pluggable persistence backend.** There is one format (numeric `.npz` plus
  a JSON sidecar) used from a few call sites; an interface for a single
  implementation is indirection without payoff. Add it when a second backend is
  a real requirement.
- **A `devices`/`experiments` namespace with `Result` objects that carry
  `.save()`/`.to_dataframe()`/`.plot()`.** The codebase's convention is frozen
  dataclasses (`StepResult`) plus free functions (`save_step_results`,
  `build_figure`, `difference_series`), which composes over any number of
  results and is easy to test. A run is a *list* of `StepResult`s, so
  methods-on-results would first need a new `Run` wrapper, which is a design
  decision of its own. If wanted, propose it separately.
- **Renaming `LastRun`.** It is already a plain, Textual-free dataclass;
  only who sets it is TUI-flavored. Fold into a future pass if one happens.
