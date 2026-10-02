# Refab Consolidation Plan

## Goal

Make the `refab` codebase smaller, clearer, and easier to extend without turning this into another architectural rewrite.

The guiding rule for this pass is:

> **Move responsibilities to the layer that already owns them, remove duplicate paths, and keep the visible behavior unchanged.**

This is a consolidation pass first. Hardening, new abstractions, and speculative redesign stay out unless they directly reduce complexity or are required to make the consolidation correct.

## Principles

### 1. Reduce code before adding code

Prefer one existing implementation with a clean seam over two nearly-identical implementations with a new abstraction placed between them.

We should actively look for:

- duplicated configuration and defaults
- duplicated instrument calls
- duplicated result/plot preparation
- repeated lifecycle plumbing
- state that is passed through several layers only to be consumed once
- compatibility wrappers that no longer buy us anything

A new helper is justified when it lets us delete more code than it adds.

### 2. Keep comments, but make them earn their place

Comments are part of the project, especially where they explain hardware behavior, concurrency, sequencing, or a non-obvious constraint.

We will **not** delete useful comments just to make files shorter.

We will improve comments when they are:

- stale
- stronger than the code actually guarantees
- explaining obvious syntax instead of intent
- describing an old architecture that no longer exists

The target is fewer *narrative* comments, not fewer useful ones.

### 3. Deconvolute gently

Keep the current public shape where it is already working.

The preferred direction is:

```
UI / CLI / console
        |
        v
plain experiment/workflow operation
        |
        v
instrument driver
```

The TUI should collect input, start work, display progress, and render results. It should not rebuild experiment logic that already exists below it.

### 4. Preserve hardware behavior

No broad SCPI cleanup, driver rewrite, protocol change, or measurement-policy change unless the existing code is demonstrably duplicated or inconsistent.

For hardware-facing code, a smaller diff with stronger tests is preferable to a clever rewrite.

---

## Work sequence

### Step 1 — Remove the first layer of duplication

Start where the split between `experiment/` and `tui/` is already visible.

The immediate target is the sweep path:

- the procedure layer already knows how to build and execute sweeps
- `SweepScreen` currently reconstructs part of that same setup itself
- configuration defaults, run context creation, and sequence execution are therefore spread across two places

Refactor that seam so the TUI becomes a thin caller of the experiment layer while retaining its progress callback, abort behavior, locking, checkpointing, and live plotting.

**Success condition:** less experiment logic in `SweepScreen`, no user-visible behavior change, and tests covering the shared path.

### Step 2 — Collapse lifecycle plumbing

Review connection ownership and cleanup paths, especially where the same resource can be opened/closed through:

- a context manager
- an explicit `connect()/disconnect()`
- a TUI handle
- a console proxy

Remove duplicate cleanup paths and make the ownership contract obvious.

The important invariant remains:

> constructing an object should not unexpectedly perform hardware I/O.

### Step 3 — Consolidate representation and plotting helpers

Find places where the same measurement data is transformed more than once before it reaches a renderer.

Keep the scientific/data transformation in one place and let matplotlib, Plotext, and TUI widgets consume the same prepared representation.

The goal is not to force every renderer into one abstraction. The goal is to avoid repeating the *measurement logic* in each renderer.

### Step 4 — Simplify the scope/workflow boundary

`scope_workflows.py` is already the correct architectural direction. The next pass should therefore be reduction, not another extraction.

Look for:

- helpers that merely forward to helpers
- repeated lock/transaction setup
- result assembly that can be centralized
- duplicated error/result bookkeeping

Keep the explicit workflow functions because they are the useful seam between the legacy scope driver and callers.

### Step 5 — Make CI definitions share one source of truth

Both GitHub Actions **and** GitLab CI stay first-class.

The target is:

```
                 shared repo commands
                /                   \\
       GitHub Actions             GitLab CI
          wrapper                  wrapper
```

The provider YAML should describe the platform-specific runner behavior only. The actual checks should come from the repository's shared tooling.

The preferred environment contract is the existing `uv.lock`:

- one dependency set
- one reproducible lock
- both CI systems use it
- both run the same test/lint/typecheck commands
- docs compilation remains shared as far as the two Typst runners allow

That means GitHub and GitLab are both authoritative gates for the same project checks; there is no need to designate one platform as the “real” CI.

### Step 6 — Remove dead weight

Only after the structural duplicates are gone:

- remove genuinely unused dependencies
- remove compatibility code with no remaining callers
- simplify imports and small wrappers
- fix stale documentation/comments
- avoid introducing replacements for code we just deleted

### Step 7 — Verify and finish cleanly

Before the PR is considered done:

- run the full test suite
- run Ruff check + format check
- run mypy consistently in both CI systems
- compile the docs in both CI systems
- inspect the final diff for accidental behavior changes
- keep commits logically separated so the PR remains reviewable

---

## What we are deliberately **not** doing in this pass

- no second architectural rewrite
- no new framework or task-runner dependency just for CI
- no speculative `devices/` or `experiments/` namespace redesign
- no persistence backend abstraction without a second real backend
- no broad driver rewrite
- no comment purge
- no “cleanup” that changes measurement semantics without a concrete reason

---

## CI policy

GitHub Actions and GitLab CI should run the same checks and should agree on pass/fail semantics.

In particular:

- **tests:** required
- **lint + format:** required
- **typecheck:** required once the existing type errors are addressed
- **docs:** required

The two CI files may differ in runner/image setup and artifact upload syntax, but they should not contain separate copies of the project's actual validation logic.

---

## Definition of done

This pass is successful when the repository has:

1. fewer duplicated implementations,
2. thinner UI orchestration,
3. clearer lifecycle ownership,
4. one shared validation contract across GitHub and GitLab,
5. no regression in the existing workflow,
6. comments that explain intent and constraints rather than narrating obvious code.

The standard is not “smallest possible diff”.

The standard is **the smallest structure that makes the existing architecture easier to understand and harder to accidentally fork.**
