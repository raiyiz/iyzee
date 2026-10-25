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
- Support interactive `input()` if it can be integrated cleanly with the UI. Otherwise document it as a deliberate limitation rather than emulate a full terminal to preserve it.
- Consolidate tests around public behavior. Remove tests whose only purpose is ANSI colors, cursor drawing, terminal byte protocol and terminal-widget internals once that implementation is gone.

## Work units

### 1. Establish the execution core

- [ ] Reuse `shell_config()` and `lab_namespace()` to construct a plain `InteractiveShell`.
- [ ] Add a small, serialized execution controller that retains namespace state between cells.
- [ ] Capture normal output, errors, expression results, IPython tracebacks and rich display output.
- [ ] Verify live `lab` access remains protected by the existing instrument locks.
- [ ] Keep UI rendering and hardware calls off the Textual event loop.

### 2. Replace terminal presentation

- [ ] Replace the emulated terminal with a Textual multiline editor and scrollable output.
- [ ] Support submit, multiline code, history navigation and IPython completion.
- [ ] Preserve visible busy/idle and interrupt affordances.
- [ ] Keep the Matplotlib line-plot panel and useful `lab` status.
- [ ] Decide and implement a clear `input()` experience or document its limitation.
- [ ] Delete `terminal_view.py`, `vterm.py`, and `termkeys.py` when there are no production references.

### 3. Simplify lifecycle and output routing

- [ ] Replace the terminal-specific `IPythonSession` with a smaller execution controller.
- [ ] Minimize process-wide stream and builtin modifications; always restore any temporary routing.
- [ ] Make Ctrl+C best-effort without targeting unrelated/dead threads.
- [ ] Ensure closing the app does not leave a cell silently running against instruments.
- [ ] Preserve isolated in-memory history for tests and per-user persistent history for real runs.

### 4. Consolidate tests

- [ ] Keep focused tests for persistent namespace, execution, output/errors, completion/history and live `lab` behavior.
- [ ] Keep a small number of end-to-end tests for the Textual console workflow and shutdown.
- [ ] Remove tests that solely verify discarded terminal rendering/key-encoding implementation details.
- [ ] Avoid duplicate tests for the same invariant at helper and UI levels unless they guard distinct boundaries.

### 5. Verify and document

- [ ] Run focused console and lab tests locally, then the full test suite.
- [ ] Run Ruff and mypy.
- [ ] Check documentation and examples for references to the old terminal-specific interaction model.
- [ ] Review the final diff for unnecessary abstractions, dead code and test duplication.

## Acceptance criteria

- Python cells execute in a persistent IPython namespace without freezing the UI.
- Connected instruments and last-run results remain available through `lab`; existing device locking is retained.
- Output, exceptions, history, completion, interruption, plotting and shutdown have explicit behavior and regression coverage.
- The TUI no longer owns an ANSI terminal emulator or a terminal byte-encoding layer for its Python console.
- Tests cover user-visible contracts with fewer, more focused cases.
- All locally runnable checks pass, with any unavailable checks or environmental limitations reported honestly.
