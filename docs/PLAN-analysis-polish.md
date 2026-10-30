# iyzee · analysis & notebooks polish — plan

> **Principle (decided).** Strict code lives under `src/` — typed, linted, unit-tested, covered.
> Notebooks are living documents: rough, allowed to be messy, checked only by a couple of
> end-to-end runs proving the core flow works. **Notebooks are not type-checked.**

## 1. Where we are

| Fact | Evidence |
|---|---|
| ~1,900 lines of real analysis sit in `notebooks/toolbox.py` (42 functions) and `notebooks/workset.py` | outside `src`: not type-checked in CI, not in the coverage measurement, not reusable by the TUI |
| `mypy notebooks` reports 10 errors | all in toolbox/workset; they surface the moment the code moves to `src` |
| `field_chart` is 142 lines, 12 parameters, complexity 23 | the worst function in the repo (`ruff C901`) |
| `reproduce_frequency_sweep.py` has a 138-line cell that copies `SweepScreen._run_sweep` | locks, analyzer setup, `RunRecord`, checkpoint-after-every-point, final status |
| Coverage fell 87.6% → 83.4% with the test pruning | long-series thinning (`tui/plotting.py` 62%) and sweep abort/failure marking lost their tests |
| Group statistics are documented only in docstrings | Welch SE, `|t| > 2` flag (not a p-value), linear interpolation on a shared grid, no extrapolation |

## 2. Scope

### Build now
1. **`iyzee.workbench` package** — the analysis library behind `notebooks/workbench.py`.
   `catalog.py` (manifests, scan, pick, describe, verify) · `tables.py` (sweep points, scoreboard,
   best, deltas, scope traces, array overlays) · `groups.py` (curves, differences, summary) ·
   `charts.py` (palette, `field_chart`, PNG/CSV export) · `workset.py` (`Workset`, `SavedSet`,
   `WorksetStore`) · `demo.py` (generated data). Public names re-exported from
   `iyzee.workbench`; behaviour unchanged. `notebooks/toolbox.py` and `notebooks/workset.py` are removed.
2. **Split `field_chart`** into small helpers (axes, series, bands, legend/labels); no function over
   complexity 12 anywhere in `src`.
3. **One headless sweep runner**, `iyzee.experiment.runner`: locks → analyzer setup → `RunRecord` →
   per-point checkpoint (a failed save is reported once and never stops the run) → final status.
   `SweepScreen` and the reproduce notebook both call it; the notebook shrinks to a thin cell.
4. **Document the group statistics** in the data-and-analysis chapter and next to the controls
   (what "significant" means and does not mean; the multiple-comparisons caveat).
5. **Demo data for everyone**: `uv run iyz-demo [DIR]` writes generated recordings and prints how to
   open the workbench on them (no instruments needed). Replaces `scripts/demo_data.py`.
6. **Quality gates**: tests for the lost coverage (series thinning; abort/failed run marking);
   ruff `C901` (max 12) enforced for the repo; coverage reported in CI with a floor.

### Tests (strict side)
- `tests/test_workbench.py` — the analysis library, in the main CI job (polars joins the `dev`
  group; the TUI itself still does not import it).
- `tests/test_notebooks.py` — rough flow only: each notebook runs every cell on generated data.
- `tests/test_runner.py` — the shared sweep runner with fake instruments.

### Decided against
- Type-checking the notebooks (they may stay messy).
- Rewriting `workbench.py` cell by cell: it only changes imports, so it keeps working as it is.

### Deferred (designed, not built here)
- **Real-terminal check** of the Vim console (cursor look, OSC 52, tmux): needs your terminal.
- **`:edit` real Vim** for big edits: patch `Buffer.open_in_editor` inside `app.suspend()`;
  needs a real terminal to verify.
- **`/` search + `n`/`N`** on the Log and Results pages.

## 3. Order of work and acceptance

| # | Step | Done when |
|---|---|---|
| 1 | polars into the `dev` group + optional `analysis` extra; `uv lock` | `uv sync` gets polars; the TUI still imports without it |
| 2 | Create `iyzee.workbench`, move code, re-export | notebooks import `iyzee.workbench`; `mypy src` clean (the 10 errors fixed) |
| 3 | Split `field_chart` | `ruff C901` max-complexity 12 passes for `src` |
| 4 | `experiment/runner.py` + refactor `SweepScreen` + notebook | sweep tests pass unchanged; notebook cell < 40 lines |
| 5 | Demo CLI, docs (statistics, new paths, README) | `iyz-demo` works; `check_doc_links.py` clean |
| 6 | Tests: workbench, runner, plotting thinning, run marking; CI coverage floor | full suite green; coverage ≥ the floor |
| 7 | Full run: ruff, mypy (`src tests`), doc links, suite, notebooks | all green; patch series rebased on `alma_dura` |

## 4. Risks

- **Moving 1,900 lines** invites silent behaviour change → mechanical moves only (no edits in the
  move commit); the existing pipeline test and three notebook runs guard it; split `field_chart`
  in its own commit with new tests first.
- **polars becomes a dev dependency**, so the lock file changes → `uv lock`, CI uses `--locked`.
- **Refactoring `SweepScreen`** touches the live-measurement path → the sweep screen tests
  (finished run, abort acknowledged, banner) must pass unchanged; the runner takes injected
  callbacks, so the UI wiring stays where it is.
- **Docs paths** (`#file("notebooks/toolbox.py")`) change → update in the same commit that moves the code.

## 5. Out of scope here, for later
`jk`-as-Escape preference, mode/pending keys in the status line, per-page `:` commands,
profiling `scan_catalog` on a large data directory (lazy polars scanning if it is slow).
