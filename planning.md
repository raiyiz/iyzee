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

The current cell-based implementation occupies three source modules (798 lines at this baseline):

| Current source module | Lines |
| --- | ---: |
| `tui/ipython.py` | 227 |
| `tui/ipython_controller.py` | 349 |
| `tui/screens/console.py` | 222 |
| **Total** | **798** |

The focused related test set is 1,077 lines before and 885 lines at this baseline. That is the set of `test_ipython.py`, the old/new console tests, `test_app.py` and `test_layout.py`; these app/layout files also cover wider behavior. These are line-count checks on defined file sets, not PR-wide +/- totals.

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
- Ctrl+C is best-effort. It may interrupt Python code, but it cannot reliably abort native code or an instrument call blocked in driver I/O. Instrument/network operations must have finite, appropriate timeouts.
- Never race a running cell with instrument disconnection. Before normal shutdown, request interruption and wait for a bounded interval. If the cell remains active, do not exit through the ordinary path: keep the app open, explain that hardware state may persist, and offer waiting or returning to the app. No forced disconnect while cell code may still own an instrument lock. Any explicit force-exit policy must warn that outputs may remain energized and must not be described as safe or clean.
- Prefer fixes at the actual behavior boundary over warning filters, swallowed causes, generic exception handling or more indirection.
- Test user-visible behavior with a small, focused suite. Delete tests that only verify the removed ANSI terminal, screen drawing internals or keystroke byte protocol.

## Intentional behavior losses

These are accepted consequences of moving from a terminal-based IPython frontend to a Textual cell editor, not regressions to recreate by emulating a terminal:

- IPython's terminal vi/emacs editing integrations and their prompt-specific keymaps. The replacement editor provides its own Vim NORMAL/INSERT/VISUAL/COMMAND modes and motions instead.
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
- [ ] Reject/disable interactive debugger entry points such as `%debug` and `%pdb` with a fast, understandable error.
- [ ] Make worker shutdown outcome explicit before the app begins unmounting; a timeout must keep ordinary shutdown from exiting.
- [ ] Audit configured finite I/O timeouts used by the drivers reachable from the console and document where each is set.

### 2. Native Vim cell editor

- [ ] Mount the existing `textual-vim-textarea` `VimTextArea` rather than a plain `TextArea`, and expose current Vim mode/status in the console UI.
- [ ] Preserve normal, insert, visual, visual-line and command modes, common motions/operators/counts, undo/redo and register-backed yank/delete/paste supported by the dependency.
- [ ] Bind Tab to IPython completion in INSERT mode. With no matches, preserve standard indentation; with multiple matches, show candidates; apply single matches as source-range replacements.
- [ ] Bind Shift+Enter and Ctrl+Enter to execute the current cell without stealing normal editor input unnecessarily.
- [ ] Keep multiline editing, sensible line numbers, history recall, busy status, interrupt control and plot output.
- [ ] Test the editor modes, Tab completion/indent fallback, source-range replacement and both submission bindings under Textual's test pilot.

### 3. Remove terminal emulation and clean dependencies

- [x] Delete the terminal widget, virtual screen and low-level terminal key encoder.
- [ ] Ensure `pyte` is absent from `pyproject.toml` and `uv.lock`.
- [ ] Ensure `prompt-toolkit` is not a direct project dependency and remains only as an IPython transitive dependency.
- [ ] Keep `jedi` as an IPython transitive dependency, but test completion against actual iyzee behavior; the current shell disables Jedi completion because static Jedi analysis misses dynamic `LabProxy` members.
- [ ] Do not enable `TextArea(language="python")` syntax highlighting unless the `textual[syntax]` extra is declared and locked.
- [ ] Remove stale references to the deleted terminal session, key protocol and per-file lint exceptions.
- [ ] Audit `textual-vim-textarea` usage so this dependency is used by the console editor rather than left unused.

### 4. Behavioral regression tests

- [x] Keep focused controller, live-`lab`, completion-range, stdin and interrupt-boundary tests.
- [x] Remove virtual-terminal ANSI, cursor protocol and terminal byte-encoding tests.
- [ ] Cover debugger fail-fast behavior, Vim editor mode and bindings, and shutdown refusal while a cell is still running.
- [ ] Retain a concise end-to-end test for persistent cell state, live results and orderly shutdown; avoid duplicating the same contract in several test layers.

### 5. Documentation and verification

- [x] Update README and architecture/TUI guides for cell submission, completion, history, interruption and unsupported interactive input.
- [ ] Document Vim mode and the final keyboard contract, including Tab completion and Shift+Enter.
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
- Normal shutdown cannot proceed to app exit while a cell may still be using hardware. The timeout/recovery UX and the daemon-thread policy are explicit; no concurrent forced disconnect.
- `pyte` is absent; `prompt-toolkit` and `jedi` are transitive through IPython, not unnecessary direct dependencies; syntax highlighting is not enabled without its extra.
- Console source remains at or below 850 lines and the defined related-test set at or below 1,000 lines. Coverage must not be deleted solely to meet these budgets.
- README and architecture/TUI guides accurately state the supported features and intentional losses.
- The latest CI run passes; any check that could not be run is identified honestly.

## Progress log

- Replaced the terminal emulator with a Textual cell editor and non-terminal `InteractiveShell`; removed `terminal_view.py`, `vterm.py`, `termkeys.py` and the old terminal-only test suite.
- Fixed the first CI failures by changing stale `#console-terminal` selectors to `#console-editor`.
- Corrected completion after finding that IPython returns a replacement range: appending at the cursor turned `pri` into `priprint` and `lab.m` into `lab.m.mx`; completion now uses the range.
- Disabled IPython ANSI colors because tracebacks and `?` inspection output were being shown as raw escape codes in the plain-text log.
- Made Ctrl+C safe at cell boundaries after asynchronous exceptions could hit queue/bookkeeping code, kill the worker and leave the UI permanently busy. The controller now targets only a live worker inside `run_cell` and refuses submissions after worker death.
- Added worker-aware stdin guarding after `sys.stdin.readline()`, `read()` or iteration could block forever and steal terminal keystrokes. Such reads now fail immediately, and the original streams are restored.
- Added a shutdown guard so a failed cell stop does not trigger instrument disconnection. This still needs a pre-exit recovery path; the current `on_unmount` guard alone cannot keep the app open.
- Removed stale terminal-emulator comments and obsolete lint exceptions.
