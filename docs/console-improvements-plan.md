# Console UX improvement plan

Scope: `tui/screens/console.py` (`ConsoleScreen`, `IyzeeConsole`, `_ConsoleInput`)
and `tui/ipython.py` (`IyzeeIPython`). Goal: make the embedded IPython
console feel as capable as a real terminal/Jupyter session, and make vim
mode legible instead of invisible.

Six items, in priority order. **1, 2, 3, 5, and 6 are done.** 4 (stream
stdout live) is scoped but not started — see its section below for why it
was deliberately left out of this pass rather than rushed.

---

## 1. Route rich `display()` output instead of dropping it (highest value)

**Problem (verified by running it):** `display(obj)` for anything with
`_repr_html_`/`_repr_png_`/etc. — matplotlib figures, pandas tables —
silently degrades to `<object at 0x...>`. The base `InteractiveShell`'s
default `DisplayPublisher` does nothing with rich mimetypes; nothing
downstream ever receives them.

**Design:**
- Subclass `IPython.core.displaypub.DisplayPublisher`, override
  `publish(data, metadata=None, **kwargs)`.
- For `image/png` (matplotlib's default rich repr): decode the base64
  bytes and hand them to the console pane. The project already depends on
  `textual-plotext`; simplest first cut is to save the PNG bytes to a
  temp file and shell out to a terminal image protocol, **or** — simpler
  and dependency-free — render `text/plain` as a fallback and treat
  `image/png` as a stretch goal, so this ships without a new rendering
  path. Decide at implementation time based on remaining budget.
- For `text/html` (pandas `DataFrame._repr_html_`): strip tags to a
  reasonable text table, or just skip HTML and let `text/plain` (pandas
  always provides both) carry it — the console already renders plain text
  well via `RichLog`.
- Install the publisher on `self.shell.display_pub` in
  `IyzeeIPython.__init__`, after shell construction.
- `execute()` needs to capture whatever the publisher wrote (append to a
  buffer the publisher pushes into) and fold it into `ExecutionOutput`,
  the same way stdout/stderr are captured today.

**Risk:** matplotlib's `plt.plot()` alone doesn't call `display()` unless
`%matplotlib inline`-equivalent hooks are active (`InlineBackend`) — may
need `matplotlib.use("module://...")` or explicit `plt.show()` handling
folded into the publisher too. Needs a quick empirical check before
committing to the image path; fall back to "text/plain only, but no
longer silently dropped" if time-boxed out.

**Test:** one regression test mirroring
`test_help_syntax_does_not_hit_the_interactive_pager` — construct a
`_repr_html_`-only object, `display()` it, assert the captured output is
non-empty and doesn't regress to the bare `<object at 0x...>` repr.

---

## 2. Visible vim mode indicator

**Problem:** `_ConsoleInput` starts in `Mode.INSERT` (deliberate — REPL
first) and reaches real vim `NORMAL` mode via Escape, but nothing in the
UI shows which mode is active. A mode you can't see is a mode you'll
mistype into.

**Design:**
- `textual_vim_textarea.VimTextArea` exposes `.mode` as a reactive
  (`Mode.INSERT` / `Mode.NORMAL` / possibly `Mode.VISUAL`-family).
- Add a `watch_mode(self, mode: Mode) -> None` on `_ConsoleInput` that
  calls back into `IyzeeConsole` to update a small mode label.
- Cheapest implementation: reuse the existing `#console-status` line
  (already shows `lab: ...`) — prefix it with `-- INSERT --` /
  `-- NORMAL --`, refreshed on every mode change and on execution finish
  (`refresh_status()` already recomputes the `lab: ...` half).
- Alternative if that line gets crowded: a dedicated 1-line `Static`
  above `#console-input`, styled like vim/neovim's own mode line
  (different background color per mode is a nice, cheap touch).

**Test:** construct `_ConsoleInput`, simulate an Escape key event, assert
the mode label reflects `NORMAL`; simulate typing `i`, assert it's back to
`INSERT`. Keep this to 1–2 focused tests, not an exhaustive mode matrix.

---

## 3. Selectable tab-completion (done)

Replaced the static `#console-completions` `Static` text readout with an
`OptionList`. Up/Down move a real highlight (intercepted in
`_ConsoleInput.on_key` *before* either history browsing or vim motions
get a look at those keys — completion navigation always wins while the
list is open), Enter/Tab accept the highlighted match, Escape closes the
list without touching the buffer, and any other keystroke abandons it
(Tab reopens against the new text rather than trying to keep a live
filter in sync char-by-char).

**Real bug found and fixed along the way:** the original
common-prefix-insertion code computed "where does the current word
start" with a whitespace-only heuristic (`rfind(" ")`/`rfind("\n")`/
`rfind("\t")` + 1), which doesn't treat `.` as a boundary. For a plain
common-prefix insert that mostly went unnoticed, but wiring up "accept
the highlighted candidate" made it load-bearing, and it's wrong for the
single most common completion case in this app — dotted attribute access
(`lab.<Tab>`). Accepting `.handles` out of `lab.` completions replaced
the *whole* `lab.` (nothing stops at the dot) and left `.handles` in the
buffer instead of `lab.handles`. Fixed by using the prefix IPython's own
`complete()` reports instead of guessing: `InteractiveShell.complete`
returns `(prefix, matches)` where `prefix` is the exact slice of source
immediately before the cursor that every match is a full replacement
for — `complete("lab.", 4)` → `(".", [".connected", ".handles", ...])`,
`complete("lab.ha", 6)` → `(".ha", [".handles"])` — so the replacement
span is just `cursor_pos - len(prefix)`, no heuristic needed at all.
Covered by `test_tab_completion_is_selectable_and_replaces_the_right_span`
and `test_tab_completion_accepts_correctly_with_a_partial_prefix_typed` in
`tests/test_console_interaction.py`.

## 4. Stream stdout live during long cells (not started)

`execute()` currently buffers all output via `contextlib.redirect_stdout`
and only returns once the cell fully finishes — the wrong shape for a
console whose main job is running blocking hardware calls where progress
prints matter. Needs a custom stdout-like object that pushes chunks to
the UI via `call_from_thread` as they're written, instead of a plain
`StringIO` collected at the end. Left out of this pass deliberately: it's
a genuinely bigger change (a stateful stdout-like object shared across
the redirect and the live UI update, correct interaction with the
`text/plain`-vs-`display()` distinction `_ConsoleDisplayPublisher`
already handles) than the other items here, and item 5 below already
surfaced enough thread/timing subtlety for one pass — better to land that
solid than rush this alongside it.

## 5. Visible "running…" state + real interrupt (done)

`action_execute` now refuses a second Shift+Enter outright while a cell
is running (instead of `exclusive=True` silently cancelling the first
one and replacing it with the second — the worst part of the old
behavior being that nothing told you it happened). The status line shows
`running… (Ctrl+C to interrupt)` for the duration, and Ctrl+C raises
`KeyboardInterrupt` inside the worker thread via
`ctypes.pythonapi.PyThreadState_SetAsyncExc` — the standard
"interruptible thread" recipe; there's no higher-level stdlib API for
this because ordinary threads have no safe cancellation point.

**Two real limitations found empirically, not just theoretically:**

- **It only interrupts at a Python bytecode boundary.** `time.sleep(N)`
  — the single most obvious thing to test this against, and plausibly
  common in real instrument-polling code in this app — is a single
  blocking C call: the injected exception doesn't fire until `sleep()`
  returns *on its own*, i.e. Ctrl+C against a `time.sleep(5)` cell
  measurably does not stop it any sooner than 5 seconds would have taken
  anyway. A plain Python loop (`while True: pass`) interrupts in well
  under a second by contrast. This is a fundamental property of
  asynchronous exception injection, not a bug in this implementation —
  documented in `_raise_in_thread`'s docstring, and the regression test
  (`test_ctrl_c_interrupts_a_running_cell_and_console_stays_usable`)
  deliberately uses a busy loop rather than `time.sleep()` so the suite
  doesn't sit there for 5 seconds proving the limitation exists.
- **A cell that raises mid-flight can (rarely) print outside the
  captured output buffer.** The exception can in principle land at a
  bytecode boundary *inside* `execute()`'s own
  `contextlib.redirect_stdout`/`redirect_stderr` teardown, which — if it
  happens right there — means `sys.stdout` is back to the real one by
  the time IPython's own traceback display runs, so that particular
  traceback isn't captured into `ExecutionOutput.stdout` the way a
  normal exception's is. Observed once during manual interrupt testing.
  Not fixed (would need disabling async-exception delivery around a
  specific critical section, which CPython doesn't expose from pure
  Python/`ctypes`); mitigated in practice by Textual owning the
  alternate screen buffer and redrawing continuously, so any stray bytes
  written directly to the real terminal get painted over on the next
  frame rather than persisting.

`_execute`'s worker also now catches any exception that manages to
escape `shell.execute()` entirely (rather than only catching what
`run_cell` catches internally) and still calls `_finish_execution` with
a synthesized error result — without this, an interrupt landing in just
the wrong spot could skip resetting the running flag and leave the
console stuck showing "running…" forever with no way to submit another
cell. Not yet observed in practice, but cheap insurance once the
irregular timing of async exception delivery was understood.

One unrelated but directly blocking bug found and fixed while testing
this: the running-state flag was originally named `self._running`,
which collides with an attribute Textual's own `MessagePump` base class
already uses internally for the widget's message-pump lifecycle —
`self._running = False` in `__init__` was silently overwritten back to
`True` by Textual's own mount machinery moments later, making
`action_execute`'s "already running" guard permanently true and Shift+
Enter a no-op from the very first keypress. Renamed to `self._executing`
throughout.

Also found and fixed: **Ctrl+P for "previous history entry" never
actually worked.** Textual's `App` binds `ctrl+p` to open the command
palette by default, as a *priority* binding — priority bindings are
checked before the focused widget's own bindings regardless of the
DOM/focus chain, so `IyzeeConsole`'s own `ctrl+p` → `history_previous`
binding was unreachable from the moment it was written; pressing it just
opened the command palette instead. (Ctrl+N happens not to collide with
anything Textual claims by default, so only half of the history pair was
actually broken — easy to miss without exercising the keybinding
end-to-end, which nothing did.) Fixed by moving the command palette to
`ctrl+backslash` via `IyzeeApp.COMMAND_PALETTE_BINDING` rather than
giving up the documented, depended-on history keybinding. Covered by
`test_ctrl_l_clears_the_output_log_without_touching_history`, which
exercises Ctrl+P as part of checking that history survives a Ctrl+L
clear — first real integration coverage this keybinding has had.

Ctrl+L also now clears the output log (`action_clear`) — same idea as a
terminal's own `clear`, leaves history and the namespace untouched.

Covered by `test_second_shift_enter_while_running_does_not_cancel_or_restart`,
`test_ctrl_c_interrupts_a_running_cell_and_console_stays_usable`,
`test_ctrl_c_without_a_running_cell_is_a_harmless_no_op`, and
`test_ctrl_l_clears_the_output_log_without_touching_history` in
`tests/test_console_interaction.py`.

**Separately, a display bug found and fixed while working on this
item:** `_finish_execution` pushed `result.stdout`/`result.stderr`
through `rich.markup.escape()`, which only escapes Rich *markup*
(`[...]`) — it does nothing for raw ANSI escape codes, and IPython
formats its own tracebacks with real ANSI SGR codes, not Rich markup.
Every traceback was rendering as literal `\x1b[31m...` noise in the
console instead of color. Switched to `rich.text.Text.from_ansi(...)`,
which decodes ANSI into actual Rich styling. Covered by
`test_traceback_output_is_colored_not_raw_ansi_escape_bytes`.

## 6. Wire up or delete `IyzeeIPython.inspect()` (done)

Deleted, along with its only helper (`_identifier_before`). It was dead
code — implemented, documented, tested nowhere, never called from the
UI — and now that `?`/`??` already work via normal cell execution (fix
shipped alongside item 1), a separate no-execute-needed inspect binding
would be a second way to do the same thing rather than a new capability,
for a keybinding budget that's already fairly full after items 3 and 5.

---

## Out of scope for this pass

Exhaustive test coverage per item — token-constrained this round, so each
item gets 1–2 targeted regression tests (mirroring the style of the
existing `test_help_syntax_does_not_hit_the_interactive_pager`), not a
full matrix. Flagged explicitly per item above rather than left implicit.

---

## Addendum: app-wide vim navigation + real console plotting

Two more fixes landed in this same pass, prompted by "vim mode doesn't
work for most buttons" and "how do we plot from the console":

**`j`/`k` focus navigation didn't actually exist anywhere.** The README
claimed "outside editable widgets, `j`/`k` move focus" — that was
aspirational documentation, not real: confirmed by grep (zero `j`/`k`
bindings anywhere in `src/iyzee/`) and by running it (pressing `j` in a
focused `Input` just types the letter `j`). Also confirmed as a side
effect: plain `Input` fields had no Escape binding either, so once
focused there was no way out except Tab/click — meaning the global
screen-switch keys (`c`/`s`/`t`/`i`) were silently swallowed as literal
text too, not just `j`/`k`. Fixed with two `IyzeeApp`-level bindings
(`escape` → blur the focused widget, `j`/`k` → `focus_next`/
`focus_previous`) — no per-screen or per-widget changes, consistent with
"navigation lives in Textual's bindings, not hand-rolled key
interpretation." Known limitation, not fixed: this isn't persistent vim
NORMAL mode — landing on another `Input` re-enters implicit "insert
mode" for that field, so hopping across several fields is
`Escape j Escape j...`, matching vim's real modal semantics (NORMAL-mode
keys don't fire while typing) but not the free-roaming `jjjj` some users
might expect. A true persistent-mode form would need a modal `Input`
subclass — real work, not attempted here.

**Matplotlib figures now render into the console.** Registered a
`text/plain` type-printer for `matplotlib.figure.Figure` on the shell's
`PlainTextFormatter` (not routed through `_ConsoleDisplayPublisher` from
item #1 above — that layer only ever sees an already-flattened mimetype
bundle, by which point the `Figure` object itself is gone) that walks
`fig.axes[0].lines` and draws into a `PlotextPlot` panel added to the
console screen, hidden until first used. Reuses `textual-plotext`,
already a project dependency and already used the same way by the sweep
screen — no new dependency. One real gotcha hit and fixed during
implementation: `PlainTextFormatter` type-printers use IPython's
pretty-printer protocol (`func(obj, printer, cycle)`, write via
`printer.text(...)`), not a plain `func(obj) -> str` like other
formatters — easy to miss, caught by testing against the real formatter
rather than assuming the signature.

