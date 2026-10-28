# iyzee · Vim-first console — plan & handoff

> **Goal.** Make iyzee a *whole Vim-like application*, starting from the IPython console: practical, flexible, and with as little hand-maintained Vim code as possible.
> **Decision (2026-10-26).** Build on **`main`'s real IPython console** (prompt_toolkit's vi engine, shown through a virtual terminal). The hand-rolled Vim of the `refactor/ipython-console` branch is **abandoned**.
> **State.** **9-commit patch series** (`patches/`) on top of `main@428702d`: code, tests, docs. `ruff`, `mypy src tests` and the doc-link check are clean. The full suite (190 collected cases, 23 hardware tests skipped) passes in ~5 min, down from 339 cases / 7.5 min after the test consolidation in the last commit; line coverage of `src` is unchanged (87.7%). Everything ran in Textual's pilot, **never in a real terminal**. See [§6](#6-status-what-is-and-is-not-verified) and [§7](#7-whats-left-for-the-ultimate-vim-experience).

---

## 1. Decision and why

| Option | Vim code we own | Verdict |
|---|---|---|
| **A. `main`** — real IPython + prompt_toolkit vi in a virtual terminal | ~0 | ✅ **chosen** |
| B. `refactor/ipython-console` — native Textual editor, hand-extended | ~480 lines and growing | ❌ every new Vim feature is more code to maintain |
| C. Swap in another Textual vim widget (`textual-vimkeys-input`) | ~0 | ⚠️ early (0.3.x), no `.`/macros, unverified in this app |
| D. Headless prompt_toolkit engine mirrored into a native widget (the spike) | ~0 vi, ~400–600 glue | ⚠️ same vi gaps as A; replaces ~510 emulator lines with similar new glue. Not a win for *code size*; only for native feel |
| E. Real Vim for big edits via `$EDITOR` | ~30 | ➕ complements A (see [§7](#7-remaining-work-in-order)) |

**Why A:** it already gives the maximum Vim fidelity per line of code *we* own. prompt_toolkit maintains the engine (motions, operators, text objects, named registers, macros, `/` search, `%`, `{ }`, visual modes), and IPython supplies the rest of the UI (completion menu, auto-suggest, history search, magics, Enter/auto-indent logic).

> **Correction recorded.** Earlier in the conversation the old approach was described as "no shared state". That was wrong: `main` runs IPython **in-process** in its own thread, so `lab.mx` is the live instrument. Only the *rendering* is a virtual terminal (prompt_toolkit → VT100 → pyte → a Textual widget).

History worth remembering: the project went hand-built vim console (Sep 11–19) → real IPython terminal (Sep 20) → the refactor branch. Decide by criteria, not by vibes, before changing course again.

---

## 2. How `main`'s console works

```
Textual key ──► termkeys.py ──► bytes ──► prompt_toolkit pipe input ─┐
                                                                     ▼
                    IPython TerminalInteractiveShell (in-process thread, vi mode)
                                                                     │ VT100 output
Textual widget ◄── terminal_view.py ◄── vterm.py (pyte screen) ◄─────┘
```

| File | ≈Lines | Role |
|---|---|---|
| `src/iyzee/tui/ipython_session.py` | 460 | Runs IPython in a thread with pipe input / VT100 output; stdout routing; busy/interrupt; `lab` namespace |
| `src/iyzee/tui/vterm.py` | 175 | pyte-backed virtual terminal |
| `src/iyzee/tui/terminal_view.py` | 185 | Textual widget; renders the screen, claims the keyboard (`check_consume_key`) |
| `src/iyzee/tui/termkeys.py` | 155 | Textual key → terminal bytes |
| `src/iyzee/tui/screens/console.py` | 300 | Console page, status line |
| `src/iyzee/tui/app.py` | 620 | App bindings (`F1–F4`, letters for pages, `:` command bar, `j/k`) |

Versions seen: Python 3.14 (uv), IPython 9.17.1, prompt_toolkit 3.0.53, Textual 8.2.8.
Thirty commits touched these files since Sep 11; the two most recent were fixes for dependency-update regressions and IPython cursor cleanup leaking into the real terminal (`a7ddf10`, `0eb4995`).

---

## 3. Findings on `main` (reproduced, root-caused)

### 3.1 Escape takes ~1.4 s to leave INSERT mode — **fixed, tested**
- **Measured:** 1441 ms and 1437 ms (`probes/probe_escape_latency.py`).
- **Root cause:** IPython applies its `timeoutlen`/`ttimeoutlen` settings only inside a getter that is installed when `modal_cursor=True` (`IPython/terminal/shortcuts/__init__.py` ≈ l.362). `main` sets `modal_cursor=False` (to stop cursor escape codes leaking), so prompt_toolkit's defaults apply (`timeoutlen=1.0`, `ttimeoutlen=0.5`).
- **Still ~0.5 s even with IPython's intended values**, because Escape is ambiguous (see 3.2).

### 3.2 Fast `Esc` + a letter is read as an Alt chord — **fixed, tested**
IPython enables emacs bindings in vi insert mode (`emacs_bindings_in_vi_insert_mode=True`), including every `Alt+<letter>` as `Esc <letter>`.

| Typed quickly | Expected (Vim) | Observed on `main` |
|---|---|---|
| `Esc d d` | delete the line | text `foo bar bazd`, **still INSERT** |
| `Esc b` then `iX` | back a word, insert | literal `i` typed, still INSERT |
| `Esc u` | undo | still INSERT (Alt-u uppercases a word) |

Reproduce with `probes/probe_escape_misfire.py`. This is data-damaging for a vim user.

### 3.3 The console strands the keyboard — **fixed, tested**
- The terminal view claims *all* keys, and Escape belongs to prompt_toolkit, so only `F1–F4` could leave the console. **Scope, Power, Rb and Log were unreachable by keyboard.**
- `:` in normal mode did nothing (prompt_toolkit has no ex line).
- `i`/F4 did nothing while already on the console page (`check_action` hid it).

### 3.4 Latent crash: `_current_page()` rejects the console — **fixed**
`App._current_page()` did `query_one(..., Page)`, but `ConsoleScreen` is a `Vertical`, not a `Page` → every `:` command typed with the console current raised `WrongType` ("command failed"). It was invisible on `main` only because `:` never reached the app from the console.

### 3.5 `j`/`k` jump focus instead of moving rows — **fixed, tested**
`j`/`k` were global `focus_next/previous`. On a `DataTable`, `ListView` or log they left the widget instead of moving the cursor / scrolling.

### 3.6 Smaller
- Banner hard-wrapped mid-word at ≈80 columns (`…Ctrl+Q quits i⏎yzee`) — shortened.
- Cursor looked the same in every vi mode (IPython's modal-cursor cue is deliberately off) — mode-aware cursor added.
- No way to copy yanked text out (mouse is captured) — clipboard bridge added.

---

## 4. Design principles

1. **Own as little Vim as possible.** Extend prompt_toolkit's engine; never reimplement a motion/operator/text object next to it.
2. **Silent divergence from real Vim is worse than a missing feature.** A command that quietly does something else breaks muscle memory and flow. Prefer "unsupported" over "subtly different".
3. **The app is modal too.** Same grammar everywhere: `Esc` backs out, `:` opens the command line, `j/k/gg/G/Ctrl-D/Ctrl-U` move, letters switch pages when nothing is being edited.
4. **Small, testable seams.** Anything touching prompt_toolkit lives in `vi_keys.py` and is testable headlessly (no Textual).
5. **Targeted tests only** (user preference): run the files you changed, not the whole suite.

---

## 5. What is written (branch `feat/vim-console`, 8 commits)

Base: `main@428702d`. Apply with `git am patches/*.patch`.

| # | Commit | Content |
|---|---|---|
| 1 | `fix(console): instant Escape, no Alt-chord misfires, . repeat, clipboard` | `vi_keys.py`, `ipython_session.py`, `on_copy` wiring in `console.py` |
| 2 | `feat(console): Escape and : at a resting prompt return the keyboard to the app` | `terminal_view.py`, `console.py`, `app.py` (`_current_page`, `check_action`) |
| 3 | `feat(app): Vim motions on tables, lists, dropdowns and logs` | `motions.py`, `app.py` |
| 4 | `docs(console): …` | README, `tui-and-devices.typ`, `architecture.typ` |
| 5 | `fix(console): only yanks reach the system clipboard` | `vi_keys.py` (decision 2) |
| 6 | `feat(app): counts for Vim motions; :q asks and :qq quits at once` | `app.py`, `motions.py`, `tests/test_command_bar.py` (decision 5) |
| 7 | `test(console): cover the Vim console, …` | `tests/test_vi_keys.py`, `tests/test_console_vim.py`, `tests/test_motions.py` |
| 8 | `docs: yank-only clipboard, :qq and motion counts` | README, guide |
| 9 | `test: consolidate the suite into fewer, fuller tests` | all test files (293 → 167 functions, 7m30s → 5m03s, coverage unchanged) |

Commits 1–4 type-check on their own (commit 9 only touches tests); the series applies cleanly to `main@428702d` and yields a tree identical to the branch.

### New files
- **`src/iyzee/tui/vi_keys.py`** — `ViExtras`, layered on prompt_toolkit's engine:
  - **Eager `Escape`** in insert/replace (a binding with `eager=True`, same body as prompt_toolkit's own handler) → instant, never an Alt chord. `ttimeoutlen` set to `0.01`.
  - **`.` repeat**: records the key sequences of the engine's *own handler calls* (wraps `KeyProcessor._call_handler` and `.reset`), commits a sequence only if it started *at rest* and its first non-count key is a change command (`dcxXDCpPsSoOiaIArRJ~<>`, `gu gU g~ gJ`); replays by `feed_multiple(..., first=True)` plus a `Keys.Ignore` sentinel that ends the replay. `3.` repeats the sequence three times.
  - **`BridgingClipboard`** (`InMemoryClipboard` + `on_copy`) so yanks reach the system clipboard.
  - **`mode`** (`insert`/`normal`/`replace`/`visual`/`visual-line`/`visual-block`) and **`at_rest`** (normal mode, no operator/count/register/search/completion/selection/pending keys).
- **`src/iyzee/tui/motions.py`** — `move(widget, motion)` for `down up top bottom half_down half_up`: cursor widgets (`DataTable`, `ListView`, `OptionList`), else scroll (`RichLog`, any scrollable; ancestors for the big jumps); returns `False` when the widget has no use for it.

### Changed files
| File | Change |
|---|---|
| `ipython_session.py` | `emacs_bindings_in_vi_insert_mode=False`; installs `ViExtras`; new `on_copy` ctor arg; `vi_mode` and `at_rest` properties (`at_rest` is also `True` while a cell runs so you can leave it behind); shorter banner |
| `terminal_view.py` | `LeaveRequested` / `CommandLineRequested` messages (Esc and `:` when `at_rest`); optional `vi_mode` / `at_rest` backend properties; mode-aware cursor (block = normal, bold underline = insert, reverse+underline = replace, dim underline = unfocused) |
| `screens/console.py` | passes `on_copy=app.copy_to_clipboard` to the session (the clipboard bridge was inert until this was wired); handlers for the two messages; `ConsoleScreen.commands()` with `:clear` (sends Ctrl-L) |
| `app.py` | `_current_page()` accepts the console; `check_action` lets `i`/F4 refocus the terminal when on console; `move` / `g_prefix` actions; bindings `j k g G Ctrl+D Ctrl+U` |

### Behaviour now
- `Esc` leaves INSERT instantly; a second `Esc` in normal mode at rest returns the keyboard to the app (letters switch pages, `j/k` move). `i` or `F4` goes back.
- `:` at rest opens the shared command line (completion, history, `:help`): `:scope`, `:power`, `:q`, `:clear` …
- `.` repeats the last change; **yanks only** (`y{motion}`, `yy`, `Y`, visual `y`) go to the system clipboard (OSC 52 via `App.copy_to_clipboard`); deletes stay in the prompt's register.
- `j/k/gg/G/Ctrl-D/Ctrl-U` on tables, lists, dropdowns and logs; counts: `5j`, `12G`, `3gg`.
- `:q` asks exactly like the `q` key; `:qq` (aliases `:quit!`, `:q!`) quits at once; `Ctrl+Q` unchanged.

---

## 6. Status: what is and is not verified

| Item | Status |
|---|---|
| Escape latency 1.44 s → under 0.6 s bound (≈80–110 ms measured) | ✅ test (`test_escape_leaves_insert_mode_at_once`), fails on the old config |
| `Esc d d` / `Esc u` behave like Vim | ✅ test, fails on the old config |
| Esc → normal → Esc leaves; `o` navigates; `i`/F4 return; Esc with a pending operator stays; Esc while a cell runs leaves | ✅ tests |
| `:` at rest opens the shared bar; `:power` from the console; `:clear`; `_current_page()` | ✅ tests |
| Mode-aware cursor (style object per mode) | ✅ test of the style; **not looked at on screen** |
| `.` repeat (9 cases, new prompt, fresh-prompt typing) | ✅ headless + real prompt tests; mutation-checked |
| Yank-only clipboard (11 cases) and `App.clipboard` end to end | ✅ tests; mutation-checked; **OSC 52 itself not seen working in a real terminal** |
| `j k gg G Ctrl-D Ctrl-U`, counts, expiry, `0` rule, digits still typed into fields/console | ✅ tests (found and fixed the squeezed-Button scroll bug) |
| `:q` asks / `:qq` quits | ✅ tests |
| `mypy src tests/<new>`, `ruff`, doc links, 35 existing tests in affected files | ✅ |
| Real terminal behaviour (kitty/iTerm/tmux, Shift+Enter, OSC 52, cursor look) | ❌ |
| Full test suite | ❌ not run |

Existing tests were grepped for the old banner text, `F1-F4` wording and `j/k = focus_next`; none asserted them.

---

## 7. What's left for the ultimate Vim experience

Ordered by how much it would change day-to-day flow. None of it is started unless marked.

### 7.1 Check what only a real terminal can show (do first, ~30 min, no code)
- [ ] Cursor look per mode (block / bold underline / reverse underline / dim), in your terminal and in tmux.
- [ ] OSC 52: `yw` in the console, paste in another app. Many terminals/tmux need clipboard-write enabled.
- [ ] `Esc d d`, `Esc b`, `.` and `gg` by hand at your normal typing speed.
- [ ] Whether you want the Esc grace period (see [§8](#8-open-decisions)).

### 7.2 Highest value next
- [ ] **Real Vim for big edits** (`:edit`, and a normal-mode key). Cleanest design: patch `Buffer.open_in_editor` to run `$EDITOR` on a temp `.py` file inside `app.suspend()` (called from the shell thread via `app.call_from_thread`), put the result back in the buffer, fall back to a plain subprocess when the driver cannot suspend. IPython's built-in `F2` / `Ctrl-X Ctrl-E` do not fit (F2 is a page key; the editor would write to Textual's real TTY; the emacs chord is probably off since `emacs_bindings_in_vi_insert_mode=False`). Needs a real terminal to verify.
- [ ] **`/` search and `n` / `N`** in the Log and Results pages (and `:` filters), the most-missed Vim motion outside the prompt.
- [ ] **Per-page `:` commands** for the pages that still lack them (the registry and `Page.commands()` are there).
- [ ] **`jk` / `jj` as Escape** (optional preference): an eager binding in `vi_keys.py`.
- [ ] **Mode and pending keys in the status line** (`-- INSERT --`, `3d`), using `ViExtras.mode` and `key_processor.arg/key_buffer`.

### 7.3 Nice to have
- [ ] `h` / `l` for horizontal scroll and table columns; `Ctrl-W`-style page switching; `H`/`M`/`L` in tables; `zz`/`zt`.
- [ ] Upstream to prompt_toolkit: dot-repeat (issue #503), the `"ayw` `IndexError`, `dap` blank-line behaviour, `i"` across lines.
- [ ] Optional re-enable of selected emacs insert keys (`Ctrl-A/E/K`) as non-Alt bindings.
- [ ] README "not full Vim" list (see §9) once the above settles.

### 7.4 Housekeeping
- [x] Full suite run (190 cases pass, 23 hardware skipped). Install the `notebooks` group (`uv sync --group notebooks`) to run the 43 notebook tests, which are otherwise skipped silently.
- [ ] Consider adding the pilot tests to whatever slow-test marker the repo uses (~25 s for `test_console_vim.py`).

---

## 8. Open decisions

Resolved this round:
- ✅ **Clipboard on deletes** → only yanks reach the system clipboard (any length). prompt_toolkit has one clipboard, so yank vs delete is told apart by the running operator; the 10-character fallback was not needed and would have dropped short yanks like `yiw`.
- ✅ **Quit** → `:q` asks like `q`; `:qq` quits at once.

Still yours:
1. **Esc-spam.** With "Esc at rest leaves", a third tap leaves the console and following letters switch pages (`c`, `s`, `p` …). Harmless but surprising. Option: ignore an Esc within ≈300 ms of the previous one. Currently no grace period.
2. **`3.` semantics.** Vim *replaces* the original count; this repeats the recorded sequence three times. Same for most deletes, different for some.
3. **Emacs-in-vi-insert stays off?** It was disabled because it makes Escape ambiguous. `Ctrl-A/E/K` could return as explicit non-Alt bindings.

---

## 9. Known limits of prompt_toolkit's vi mode (inherited, mostly upstream)

Checked in the 3.0.53 source and the spike (`spike/spike_ptk_vi.py`, 10 of 14 behaviour checks matched Vim):

- ❌ **No `.`** upstream (python-prompt-toolkit issue #503) — added here in `vi_keys.py`.
- ❌ **Named register + motion raises `IndexError`**: `"ayw`, `"ay$` (`_yank_to_register` reads `key_sequence[1]`); `"ayiw` works. Reproduced headlessly; not confirmed in a real terminal. Worth an upstream issue.
- ⚠️ `d%` deleted the whole buffer in one test (cause unresolved; `%` alone jumped correctly).
- ⚠️ `dap` leaves the blank line Vim would remove; `i"` / `a"` search across lines (Vim stays on the current line).
- ➖ No marks, no `:` ex line (hence the app's command bar), no `Ctrl-D/U` scrolling inside the prompt.
- ✅ Present: text objects (`iw aw iW aW ap`, quotes, brackets), `f t F T ; ,`, `%`, `{ }`, `gg G H M L`, visual / line / block, named registers, macros (`q`/`@`), `/ ? n N * #`, `u` / `Ctrl-R`, counts, `r R J ~ >> <<`.
- Macros are recorded as raw key *data* and replayed by re-parsing; synthetic key presses must carry real bytes (`\x1b` for Escape).

**Not Vim, by design:** folds, splits, `:s///`, visual-mode `.`, sentence/tag text objects.

---

## 10. Research notes

- **Flow.** No rigorous modern study of modal-editing productivity was found; claims are anecdotal. The defensible criteria are friction and muscle memory. HCI work on mode errors (Sellen, Kurtenbach & Buxton) finds that sensory feedback reduces them and that user-maintained modes work better than purely visual indicators — hence the mode-aware cursor and instant Escape.
- **Other Textual vim widgets** (not adopted): `textual-vimkeys-input` 0.3.x (operators, text objects, f/t, marks; no `.`/macros seen), `textual-vim-textarea` 1.2.0 (the refactor branch's base; no text objects), `vimkit`, `jvim`; `textual-editor` embeds a real editor through a terminal emulator.
- **The spike** (prompt_toolkit's engine driven headlessly, ~0.1 ms/key) proved mirroring is feasible and cheap, but would replace the emulator with comparable glue (painter widget, completion popup, ghost suggestion, cross-thread mirroring, paste, mouse). Not recommended unless emulator pain proves real; if tried, time-box to ~2 days, mirror IPython's own `PromptSession`, and kill it if glue passes ~500 lines or key loss/reordering appears.
- **Escape internals.** On a flush timeout prompt_toolkit runs exact matches and *drops* a pending prefix with no exact binding, so shortening `timeoutlen` would break chords like `gg`. That is why Escape is made eager instead of lowering timeouts.

---

## 11. Resume checklist

```bash
git clone https://github.com/raiyiz/iyzee && cd iyzee
git checkout -b feat/vim-console origin/main          # main@428702d or later
git am /path/to/patches/*.patch
uv sync

# reproduce the original bugs on main, then confirm the fixes on the branch
uv run python probes/probe_escape_latency.py             # ~1440 ms on main, ~100 ms fixed
uv run python probes/probe_escape_misfire.py             # 'Esc d d' leaves text 'foo bar bazd' on main
uv run python probes/probe_leave_and_return.py

# only the tests you touch
uv run pytest tests/test_vi_keys.py tests/test_console_vim.py tests/test_motions.py \
              tests/test_terminal.py tests/test_console_screen.py tests/test_command_bar.py -q
uv run mypy src tests/test_vi_keys.py tests/test_console_vim.py tests/test_motions.py
uv run ruff check src tests && uv run ruff format --check .
```

Notes for whoever continues:
- `print()` is captured while a Textual app runs; collect lines and print after the app exits (the probes do).
- The key processor needs a running event loop for its timeout task; headless harnesses must run inside `asyncio`.
- Repo conventions: Ruff-formatted, mypy-clean, docs link-checked by `uv run scripts/check_doc_links.py`; CI shards in `scripts/ci.sh`.
- Working preferences: targeted tests only, minimal owned code, clean commit series exported as patches (no push access from the sandbox).

---

## 12. Artifact index

| Path | What |
|---|---|
| `PLAN-vim-console.md` | this document (put it at `docs/PLAN-vim-console.md` or the repo root) |
| `patches/000{1..9}-*.patch` | the finalized series against `main@428702d` (`git am patches/*.patch`) |
| `probes/probe_escape_latency.py` | measures Escape → normal latency |
| `probes/probe_escape_misfire.py` | `Esc`+letter misfire cases |
| `probes/probe_leave_and_return.py` | the original exploratory probe; superseded by `tests/test_console_vim.py` |
| `spike/spike_ptk_vi.py` | headless prompt_toolkit vi harness with 14 behaviour checks |
| `archive-refactor-branch/000{1,2,3}-*.patch` | abandoned hand-rolled Vim (text objects, f/t, `.`, shared command line) — kept for reference; the `:`-command and `_current_page` ideas were carried over |
