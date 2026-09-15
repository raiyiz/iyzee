# Console UX improvement plan

Scope: `tui/screens/console.py` (`ConsoleScreen`, `IyzeeConsole`, `_ConsoleInput`)
and `tui/ipython.py` (`IyzeeIPython`). Goal: make the embedded IPython
console feel as capable as a real terminal/Jupyter session, and make vim
mode legible instead of invisible.

Six items, in priority order. **1 and 2 are being implemented now.**
3–6 are scoped but not started.

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

## 3. Selectable tab-completion (not started)

Replace the static `#console-completions` text readout with a widget the
user can navigate (arrow keys) and accept (Enter/Tab) without retyping to
narrow. Likely a small `OptionList` or a custom overlay bound to the
existing `action_complete()` matches list.

## 4. Stream stdout live during long cells (not started)

`execute()` currently buffers all output via `contextlib.redirect_stdout`
and only returns once the cell fully finishes — the wrong shape for a
console whose main job is running blocking hardware calls where progress
prints matter. Needs a custom stdout-like object that pushes chunks to
the UI via `call_from_thread` as they're written, instead of a plain
`StringIO` collected at the end.

## 5. Visible "running…" state + real interrupt (not started)

`_execute` is `@work(thread=True, exclusive=True, ...)`: a second
Shift+Enter while a cell is running *silently* cancels it, with no
"still running" indicator beforehand and no way to interrupt a genuinely
hung call (e.g. `single_sweep_wait()` that never returns) short of
force-cancelling. Needs: (a) a status indicator while `_execute` is in
flight, (b) a bound key (e.g. Ctrl+C) that raises `KeyboardInterrupt` in
the worker thread rather than just letting `exclusive=True` silently
replace it.

## 6. Wire up or delete `IyzeeIPython.inspect()` (not started)

Dead code — implemented, documented, tested nowhere, never called from
the UI. Now that `?`/`??` work via normal cell execution (fix already
shipped), decide: bind `inspect()` to a dedicated no-execute-needed
shortcut, or delete it as redundant.

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

