# IPython console redesign

## Goal and scope

Replace the embedded terminal emulator with a native Textual code editor and output view backed by IPython's in-process `InteractiveShell`. Keep the live `lab` API, instrument locking, run results, plotting and responsive UI. Reduce code by removing the terminal-inside-a-terminal architecture, not by adding a new abstraction layer.

## Findings and baseline

The legacy console implementation occupied six source modules (1,507 lines):

| Legacy source module | Lines |
| --- | ---: |
| `tui/ipython.py` | 230 |
| `tui/ipython_session.py` | 460 |
| `tui/screens/console.py` | 301 |
| `tui/terminal_view.py` | 186 |
| `tui/vterm.py` | 175 |
| `tui/termkeys.py` | 155 |
| **Total** | **1,507** |

The first native-cell baseline (before the Vim editor, stdin guard, interrupt-boundary fix and pre-exit shutdown checks) occupied 798 lines in the three modules below. The current implementation includes those fixes and the regression coverage for them:

| Current source module | Lines |
| --- | ---: |
| `tui/ipython.py` | 227 |
| `tui/ipython_controller.py` | 383 |
| `tui/screens/console.py` | 280 |
| **Total** | **890** |

That is 617 fewer source lines than the original six-module implementation (40.9% reduction).

The defined related test-file set is 966 lines now, compared with 1,077 before. It consists of `test_ipython.py`, `test_console.py`, `test_ipython_controller.py`, `test_app.py` and `test_layout.py`; the app/layout files also cover broader behavior. The set shrank by 111 lines. These are source-only and test-only counts, not PR-wide +/- totals.

## Design decisions

- Keep IPython as the execution engine. Do not implement a custom evaluator or command DSL.
- Keep `LabProxy`, fresh live-handle lookup, shared per-instrument locks and recent run results.
- Let Textual own editing, completion, output scrolling, status and plotting.
- Execute one cell at a time on a dedicated worker, off the UI event loop. Preserve one namespace across cells.
- Use IPython's `HistoryManager`: production history is **per-user and specific to iyzee**, stored at `platformdirs.user_data_dir("iyzee") / "console_history.sqlite"`; direct sessions and tests default to `:memory:`. Do not add a custom history store.
- Install only the process-wide stream guards needed for this embedded worker, route worker stdout/stderr into the console, reject worker stdin reads and `input()` immediately, and restore all original process globals after the worker exits.
- Use VimTextArea for the editor and preserve as much of its supported modal editing as possible. The editor starts in Vim NORMAL mode; INSERT mode remains a real multiline Python editor. Completion should be available by Tab in INSERT mode; when there are no candidates, Tab should retain ordinary indentation behavior. Shift+Enter runs the current cell, and Ctrl+Enter remains an alternate shortcut.
- Completion replacements use IPython's returned source range, not blind insertion at the cursor. Completion must work for partial words and the dynamic `lab` proxy.
- Disable ANSI color sequences in IPython output; the output pane renders text, not a terminal.
- Ctrl+C is best-effort; it cannot reliably abort native code or a VISA/driver call blocked in I/O. Current finite timeout defaults are MXA VISA 5 s, PSU VISA 10 s, LeCroy VISA 10 s, and wavemeter HTTP 1 s for reads / 3 s for setpoint writes. MXA `wait_opc()` / `single_sweep_wait()` allow a per-call 30 s timeout. Driver constructors and the LeCroy per-query timeout provide the documented override points; changing Python thread interruption must never be treated as a replacement for I/O timeouts.
- The execution worker remains a daemon thread as a last-resort fallback for abnormal interpreter termination, but ordinary user-requested quit is preflighted. The app atomically pauses submissions/completion, requests interruption and waits up to 12 s. If the cell is still active, shutdown is cancelled, submissions reopen and the app stays open; the operator is told that hardware may retain its current output state. There is no “safe force quit” path. Once the call finishes (or its driver timeout fires), the operator can retry. Never disconnect a device concurrently with cell code that might still use it.
- External process termination cannot guarantee hardware safety: the PSU output/shutter may remain at the last commanded state. Normal shutdown closes instruments only after worker code has stopped; the Power page is not a substitute for safely resolving a cell that still owns a device lock.
- Prefer fixes at the actual behavior boundary over warning filters, swallowed causes, generic exception handling or more indirection.
- Test user-visible behavior with a small, focused suite. Delete tests that only verify the removed ANSI terminal, screen drawing internals or keystroke byte protocol.

## Intentional behavior losses

These are accepted consequences of moving from a terminal-based IPython frontend to a Textual cell editor, not regressions to recreate by emulating a terminal:

- IPython's terminal vi/emacs editing integrations and their prompt-specific keymaps. The replacement editor provides its own Vim NORMAL/INSERT/VISUAL/VISUAL-LINE/COMMAND modes, common motions/operators, text objects, counts, undo/redo and a single unnamed register.
- Advanced Vim features not implemented by the current `VimTextArea` dependency: named/multiple registers, macros, marks, `:s///` regex substitution, folds and splits. Do not imply full Vim compatibility.
- Ctrl+R's prompt-toolkit incremental history search and terminal autosuggestions. History remains navigable in the editor with previous/next cell shortcuts; do not add a custom search/autosuggestion subsystem in this refactor.
- Interactive `input()` and other reads from `sys.stdin`.
- Interactive terminal debugger prompts, including `%debug` and `%pdb` flows that stop for input. These must fail fast with a clear explanation rather than leave the worker blocked.
- Terminal cursor movement, ANSI screen-control programs and terminal-only rendering protocols. Ordinary printed text, tracebacks, expression output and supported plots remain available.

## Work units

### 1. Execution, completion and lifecycle core

- [x] Build a plain `InteractiveShell` from `shell_config()` and `lab_namespace()`.
- [x] Serialize cell execution on one worker and retain namespace state across cells.
- [x] Route output and display results into Textual without ANSI color codes.
- [x] Preserve the live `lab` proxy and per-handle locking.
- [x] Use IPython's in-memory or per-user application history.
- [x] Make completion ranges explicit and guard Ctrl+C so asynchronous interrupts are delivered only inside cell execution.
- [x] Guard `input()` and `sys.stdin` reads against blocking the worker.
- [x] Reject interactive `%debug` and `%pdb` entry with a clear error rather than opening a terminal prompt.
- [x] Route ordinary quit paths through a pre-exit interrupt/wait check; pause cell submission/completion while the decision is in flight, reopen them on timeout, and keep the app open if the 12 s limit expires.
- [x] Document the finite driver timeout defaults and the longer MXA per-call acquisition timeout.

### 2. Native Vim cell editor

- [x] Mount `textual-vim-textarea`'s `VimTextArea` and show mode/status in the console.
- [x] Use the dependency's NORMAL, INSERT, VISUAL, VISUAL-LINE and COMMAND modes, common motions/operators/text objects/counts, undo/redo and its single unnamed register for yank/delete/paste.
- [x] Bind Tab to IPython completion in INSERT mode; if no completion is available, fall through to space indentation at the next four-column tab stop. Multiple matches are listed; a single match replaces the IPython-provided source range.
- [x] Bind Shift+Enter as the primary submit shortcut and Ctrl+Enter as an alternate. Ctrl+Space remains an explicit completion shortcut.
- [x] Keep multiline editing, line numbers, history recall, busy status, best-effort interrupt and plot output.
- [x] Add Textual pilot coverage for Tab, indentation fallback, completion replacement, Vim delete/undo and Shift+Enter submission. CI is the authority for whether these new tests pass.

### 3. Remove terminal emulation and clean dependencies

- [x] Delete the terminal widget, virtual screen and low-level terminal key encoder.
- [x] Confirm `pyte` is absent from both `pyproject.toml` and `uv.lock`.
- [x] Keep `prompt-toolkit` out of direct dependencies; it remains transitive through IPython.
- [x] Keep `jedi` transitive through IPython. The shell disables Jedi completion because static analysis misses dynamic `LabProxy` attributes; the regression tests exercise actual IPython completion instead.
- [x] Do not enable `TextArea(language="python")` syntax highlighting, so the `textual[syntax]` extra is not needed.
- [x] Remove stale references to the deleted terminal session, key protocol and per-file lint exceptions.
- [x] Use the existing direct dependency `textual-vim-textarea` for the actual console editor.

### 4. Behavioral regression tests

- [x] Keep focused controller, live-`lab`, completion-range, stdin and interrupt-boundary tests.
- [x] Remove virtual-terminal ANSI, cursor protocol and terminal byte-encoding tests.
- [x] Add regression coverage for debugger fail-fast behavior, Vim editor/Tab behavior, Shift+Enter, and shutdown refusal while a cell is still running (test results pending current CI).
- [x] Retain one concise end-to-end test for persistent cell state, live results and orderly shutdown rather than duplicating the full contract in each layer.

### 5. Documentation and verification

- [x] Update README and architecture/TUI guides for cell submission, completion, history, interruption and unsupported interactive input.
- [x] Document Vim mode, Tab completion/indent fallback, Shift+Enter and Ctrl+Enter, best-effort interruption and shutdown preflight.
- [ ] Run focused console/lab tests and the full suite in CI; local test execution is unavailable in the current GitHub-only editing environment.
- [ ] Confirm Ruff and mypy on the latest head, then verify the new commit's CI rather than relying on earlier runs.
- [ ] Review the final branch diff and line counts for unnecessary abstractions, stale comments, dead code and duplicated tests.

## Acceptance criteria

- Python cells execute sequentially in a persistent IPython namespace without blocking Textual's event loop.
- Connected instruments, shared locking and last-run results remain available through `lab`.
- The editor uses VimTextArea and preserves the dependency's supported modal editing; Tab completes when appropriate and indents otherwise; Shift+Enter and Ctrl+Enter submit cells.
- Completion works for partial names and `lab` attributes, replacing the exact source range.
- `input()`, direct stdin reads and interactive debugger prompts fail promptly; the worker remains usable afterwards.
- Ctrl+C is explicitly best-effort, while blocking device I/O is bounded by finite driver timeouts.
- Normal user-requested shutdown waits up to 12 s after a best-effort interrupt; if a cell is still active, ordinary quit is cancelled and the app stays open. The worker is a daemon only as a last-resort abnormal-exit fallback. No concurrent instrument disconnection or “safe force quit” is offered.
- `pyte` is absent; `prompt-toolkit` and `jedi` are transitive through IPython, not unnecessary direct dependencies; syntax highlighting is not enabled without its extra.
- Console source remains at or below 900 lines and the defined related-test set at or below 1,000 lines. Current measured counts are 874 source lines and 963 related-test lines. Coverage must not be deleted solely to meet these budgets.
- README and architecture/TUI guides accurately state the supported features and intentional losses.
- The latest head passes CI (tests, Ruff, mypy, docs, doc-links, notebooks); any check that could not be run is identified honestly.

## Progress log

- Replaced the terminal emulator with a Textual cell editor and non-terminal `InteractiveShell`; removed `terminal_view.py`, `vterm.py`, `termkeys.py` and the old terminal-only test suite.
- Fixed the first CI failures by changing stale `#console-terminal` selectors to `#console-editor`.
- Corrected completion after finding that IPython returns a replacement range: appending at the cursor turned `pri` into `priprint` and `lab.m` into `lab.m.mx`; completion now uses the range.
- Disabled IPython ANSI colors because tracebacks and `?` inspection output were being shown as raw escape codes in the plain-text log.
- Made Ctrl+C safe at cell boundaries after asynchronous exceptions could hit queue/bookkeeping code, kill the worker and leave the UI permanently busy. The controller now targets only a live worker inside `run_cell` and refuses submissions after worker death.
- Added worker-aware stdin guarding after `sys.stdin.readline()`, `read()` or iteration could block forever and steal terminal keystrokes. Such reads now fail immediately, and the original streams are restored.
- Added the pre-exit shutdown path: pause new cells, request an interrupt and wait up to 12 s; if the cell remains active, cancel the quit, reopen submissions, keep the UI available and leave instruments connected. This also closes the race between the idle check and worker shutdown. The worker is daemonized only as a fallback for abnormal interpreter termination.
- Added explicit `%debug` / `%pdb` rejection, so terminal-only debugger prompts cannot hold the cell worker at stdin.
- Reused the already-declared `textual-vim-textarea` dependency: Vim modes, Tab completion with space indentation fallback, Shift+Enter submit and Ctrl+Enter alternate, plus mode/status feedback and focused tests. Its documented limit is one unnamed register, with no macros, marks, regex substitution, folds or splits.
- Removed stale terminal-emulator comments and obsolete lint exceptions. Ruff later identified formatting-only issues in newly edited files; these were corrected in dedicated style commits. Current-head CI is running; the local environment still cannot clone the repository, so no local pytest/Ruff/mypy run is claimed.
