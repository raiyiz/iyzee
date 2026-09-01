# iyzee `moonshine` cleanup plan

This document is the durable execution plan for the incremental cleanup of the `moonshine` branch.

## Goal

Improve correctness, safety, testability, maintainability, and reproducibility without performing a large rewrite. Preserve experimental behavior unless a change is explicitly identified as a bug or safety issue.

## Working branches

- Starting point: `moonshine`
- Cleanup branch: `cleanup-moonshine`
- Target: `moonshine`
- One pull request should contain the incremental commits.

## Critical workflow: one commit, then CI, then fix

Work strictly in small, reviewable commits.

For every planned change:

1. Inspect the current HEAD and the relevant files.
2. Make **one logical change only**.
3. Commit it with a focused conventional commit message.
4. Push/update the cleanup branch.
5. **Do not immediately make the next planned change.**
6. Check CI for the commit just created.
7. If CI fails, make one or more small follow-up fix commits addressing only those failures.
8. Re-check CI for the resulting HEAD.
9. Only once CI is green, proceed to the next planned change.
10. At every step, retain the commit history; do not squash intermediate commits.

Important: CI results are only available after a commit exists. Therefore the next iteration must explicitly inspect the CI result of the previous commit before proceeding. Treat CI as feedback, not as something to predict perfectly in advance.

If a CI failure is unrelated to the current change, document it and investigate before proceeding. Do not silently ignore failing checks.

## Local checks

Before each commit, run the checks that are available locally and relevant to the change:

- `pytest`
- `ruff check .`
- `ruff format --check .`
- `mypy` or the repository's configured type checker

Do not claim a check was run if it was not actually run.

CI remains authoritative for environment-dependent behavior.

## Safety rules

This is laboratory/instrument-control software.

- Never leave an optical shutter open because of an exception path.
- Never turn a hardware communication failure into a plausible measurement value.
- Do not make real hardware access happen merely because a Python module is imported.
- Do not change instrument setpoints or SCPI behavior unless the change is understood and tested.
- Prefer deterministic cleanup with `try/finally` or context managers.
- Keep hardware-facing changes small and independently reviewable.

## Planned sequence

### 01 — Fix package imports

Replace non-package imports in `src/iyzee/main.py` with correct package-relative imports. Verify package/module execution and tests.

### 02 — Remove import-time hardware execution

Remove the unconditional wavemeter monitoring call from module import. Put executable behavior behind an explicit entry point.

### 03 — Centralize instrument addresses

Put the wavemeter address into the shared address definition and remove duplicated/inconsistent literals. Verify which address is authoritative before changing behavior.

### 04 — Make hardware cleanup exception-safe

Ensure MXA disconnects and shutter closure happen even when acquisition fails. Keep the experiment semantics unchanged.

### 05 — Make device lifecycle explicit

Introduce context-manager support where appropriate, without forcing a broad architecture rewrite.

### 06 — Fix RBW/VBW scan behavior

Correct the RBW/VBW relationship in `record_bw_seq()` and correct misleading scan-step documentation/comments. Confirm intended experimental units from the existing code before changing values.

### 07 — Improve wavemeter error handling

Do not convert communication failures into `0`. Use an explicit exception or an unambiguous invalid measurement representation. Add tests.

### 08 — Verify `*OPC?` and SCPI errors

Make `wait_opc()` inspect the instrument response. Add an explicit mechanism for checking the SCPI error queue where useful.

### 09 — Finish the shared base-device extraction

Move shared device infrastructure into `base.py` and keep `__init__.py` minimal. Preserve public imports where practical.

### 10 — Remove stale/dead MXA implementations

Delete obsolete commented-out duplicate implementations and keep one authoritative implementation per operation.

### 11 — Improve hardware testability

Introduce/inject a fake VISA transport or test double so MXA/PSU behavior can be tested without physical hardware.

### 12 — Fix and expand tests

Fix incorrect monkeypatch targets and add tests for SCPI commands, cleanup, failure paths, and wavemeter behavior.

### 13 — Clean dependency configuration

Remove development-only packages from runtime dependencies where appropriate, especially `pytest`, `pip`, and any unused runtime dependency. Update the lockfile only when required by the dependency changes.

### 14 — Add lint/type-check configuration

Add or improve Ruff and mypy/pyright configuration and CI checks, keeping the configuration minimal and compatible with the project's Python version.

### 15 — Make units explicit

Rename ambiguous timing/configuration parameters or introduce typed configuration so milliseconds, seconds, Hz, and THz cannot easily be confused.

### 16 — Introduce typed analyzer configuration

Replace the loose dictionary/`**overrides` pattern with a small dataclass or equivalent typed configuration object.

### 17 — Separate acquisition, plotting, and persistence

Keep measurement procedures independent from plotting and file-system concerns. Preserve existing user-facing behavior.

### 18 — Improve measurement persistence

Store structured arrays and experiment metadata rather than relying on object arrays where practical. Include configuration, timestamp, and software revision information.

### 19 — Improve oscilloscope socket robustness

Audit binary socket reads and ensure exact-length reads rather than assuming a single `recv()` returns the requested number of bytes. Add protocol-level tests where feasible.

### 20 — Final review

Run the complete test/lint/type-check suite, inspect the complete PR diff and commit history, verify no accidental hardware behavior was introduced, and update documentation if behavior changed.

## Commit discipline

Prefer commits such as:

- `fix: use package-relative imports`
- `fix: prevent wavemeter work at import time`
- `fix: centralize wavemeter address`
- `fix: close shutter on acquisition failure`
- `fix: correct RBW scan VBW handling`
- `test: add MXA command transport double`
- `refactor: extract shared device base`

Avoid commits such as `cleanup`, `misc fixes`, or `refactor everything`.

## CI feedback log

Append a short note here after each CI cycle if useful:

| Step | Commit | CI result | Follow-up |
|---|---|---|---|
| 01 | TBD | TBD | TBD |
| 02 | TBD | TBD | TBD |
| 03 | TBD | TBD | TBD |
| 04 | TBD | TBD | TBD |
| 05 | TBD | TBD | TBD |
| 06 | TBD | TBD | TBD |
| 07 | TBD | TBD | TBD |
| 08 | TBD | TBD | TBD |
| 09 | TBD | TBD | TBD |
| 10 | TBD | TBD | TBD |
| 11 | TBD | TBD | TBD |
| 12 | TBD | TBD | TBD |
| 13 | TBD | TBD | TBD |
| 14 | TBD | TBD | TBD |
| 15 | TBD | TBD | TBD |
| 16 | TBD | TBD | TBD |
| 17 | TBD | TBD | TBD |
| 18 | TBD | TBD | TBD |
| 19 | TBD | TBD | TBD |
| 20 | TBD | TBD | TBD |

## Continuation instructions

If this work is resumed in a later conversation, start by reading this file and then:

1. Inspect `cleanup-moonshine` HEAD.
2. Inspect the latest commit and its diff.
3. Check CI for that exact commit.
4. If CI is failing, fix those failures first in small commits.
5. If CI is green, continue with the first unfinished numbered step.
6. Never assume a previous CI result; fetch it again.
7. Do not skip steps or combine unrelated planned changes merely to reduce commit count.

The objective is a clean, understandable commit-by-commit history where every step leaves the branch in a known state.