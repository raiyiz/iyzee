# IPython console redesign

## Goal

Replace the embedded terminal emulator with a native Textual editing/output experience backed by IPython's in-process `InteractiveShell`. Keep the live lab API and measurement workflow; remove terminal emulation rather than reorganizing it into more classes.

## Findings

The current console has six main implementation modules totaling roughly 1,507 lines, including extensive explanatory comments:

- `tui/ipython.py` — useful `LabProxy`, namespace and shell configuration.
- `tui/ipython_session.py` — a 460-line adapter that manages a `TerminalInteractiveShell`, prompt-toolkit pipe input/output, a thread, process-wide stream routing, interruption, terminal hooks and cleanup.
- `tui/screens/console.py` — Textual integration, status, rich display and Matplotlib figures.
- `tui/terminal_view.py`, `tui/vterm.py`, `tui/termkeys.py` — terminal-widget protocol, ANSI screen model, key encoding and scrollback.

The implementation's complexity mostly comes from hosting a terminal application inside Textual. The repository already has a small non-terminal `InteractiveShell` test harness in `tests/test_ipython.py`, so the core approach is already familiar to the codebase.

## Design decisions

- Keep IPython as the execution engine. Do not implement a custom Python evaluator or command DSL.
- Keep `LabProxy`, live handle lookup, shared instrument locking, recent results, and app-specific persistent history.
- Let Textual own code editing, completion presentation, output scrolling, status and plotting.
- Serialize Python cell execution off the UI thread. Route cell output into Textual without sending ANSI sequences through a terminal emulator.
- Keep interruption best-effort and prevent a stale thread identifier from targeting unrelated workers.
- Make shutdown and worker state explicit; never close shared lab resources while a cell may still be using them.
- Explicitly document interactive `input()` and terminal-based debugger prompts as unsupported by the first cell-based editor.
- Consolidate tests around public behavior. Remove tests whose only purpose is ANSI colors, cursor drawing, terminal byte protocol and terminal-widget internals once that implementation is gone.

## Work units

### 1. Establish the execution core

- [x] Reuse `shell_config()` and `lab_namespace()` to construct a plain `InteractiveShell`.
- [x] Add a small, serialized execution controller that retains namespace state between cells.
- [x] Capture normal output, errors and expression results through IPython's display/output hooks.
- [x] Keep the live `lab` proxy and existing instrument-lock boundary; retain its focused regression tests.
- [x] Execute cells on a dedicated worker rather than the Textual event loop.

### 2. Replace terminal presentation

- [x] Replace the emulated terminal with a Textual multiline editor and scrollable output.
- [x] Support submit, multiline code, history navigation and IPython completion.
- [x] Preserve visible busy/idle and interrupt affordances.
- [x] Keep the Matplotlib line-plot panel and useful `lab` status.
- [x] Document `input()` and interactive terminal debugger prompts as unsupported by the cell editor.
- [x] Delete the terminal widget, virtual screen and terminal key encoder.

### 3. Simplify lifecycle and output routing

- [x] Replace terminal-specific shell handling with a smaller non-terminal controller.
- [x] Limit global hooks to worker-aware stdout/stderr and `input()` routing, restoring them when execution stops.
- [x] Make Ctrl+C best-effort and verify the worker thread is alive before interrupting it.
- [x] Refuse to disconnect instruments if the cell worker does not stop during shutdown.
- [x] Preserve isolated in-memory history for tests and per-user persistent history for real runs.

### 4. Consolidate tests

- [x] Keep focused controller and live-`lab` behavioral tests.
- [x] Keep one end-to-end cell execution/live-results/shutdown test.
- [x] Remove the terminal emulator and low-level key-protocol test suite.
- [x] Drop the redundant console-screen integration test in favor of the combined console test.

### 5. Verify and document

- [ ] Run focused console and lab tests locally, then the full test suite (this environment cannot clone the repository; use CI to validate the pushed commits).
- [ ] Run Ruff and mypy.
- [x] Update the README to describe cell submission, completion, history, interruption and the explicit input limitation.
- [ ] Review the final diff for unnecessary abstractions, dead code and test duplication.

## Acceptance criteria

- Python cells execute in a persistent IPython namespace without freezing the UI.
- Connected instruments and last-run results remain available through `lab`; existing device locking is retained.
- Output, exceptions, history, completion, interruption, plotting and shutdown have explicit behavior and regression coverage.
- The TUI no longer owns an ANSI terminal emulator or a terminal byte-encoding layer for its Python console.
- Tests cover user-visible contracts with fewer, more focused cases.
- All locally runnable checks pass, with any unavailable checks or environmental limitations reported honestly.
