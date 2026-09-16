# Sidebar navigation shell — migration plan

## Why a rewrite, not a widget

The app currently uses Textual's `App.MODES`: Connect/Sweep/Traces/Console
are each an independent `Screen`, and `switch_mode()` unmounts one and
mounts the next. Nothing survives a switch by design — there's no way to
bolt a persistent sidebar onto that without changing the shape itself.

## Target shape

One `IyzeeApp.compose()` builds the whole app directly (no `Screen`
subclass needed for a single-screen app): `Header()`, then
`Horizontal(NavRail, ContentSwitcher(*pages))`, then `Footer()`. The four
existing screens become plain container widgets ("pages") held inside the
`ContentSwitcher`; switching pages sets `content_switcher.current` instead
of calling `switch_mode()`.

## Investigated before starting (so the migration doesn't surprise later)

- **No `BINDINGS` collisions**: grepped every screen — none of
  `ConnectScreen`/`SweepScreen`/`TracesScreen`/`ConsoleScreen` (the
  `Screen` subclasses) declare their own `BINDINGS`. Only `IyzeeConsole`
  (a widget inside `ConsoleScreen`, not the screen itself) does, and that
  keeps working unchanged regardless of what its ancestor is.
- **`switch_mode`/`push_screen`/`pop_screen`** are used nowhere except
  `app.py` itself — confirmed by grep. No other file assumes the
  Screen-per-page shape.
- **Three screens use `on_screen_resume`** (Connect, Console, Traces) to
  refresh state when becoming visible again — Sweep doesn't. This is a
  `Screen`-only lifecycle hook; the general Textual equivalent for any
  widget is `on_show`, which fires the same way when `ContentSwitcher`
  toggles a child's `display`. Direct rename, no behavior change.
- **`DataTable`'s Enter-key handling** (Connect's row-select) already goes
  through the `RowSelected` message, not a `Screen`-level `Binding`, per
  an existing code comment explaining exactly why — unaffected either way.

## What changes

- `connect.py` / `sweep.py` / `traces.py` / `console.py`: base class
  `Screen` → `Vertical`, drop the `Header()`/`Footer()` they each
  currently yield (now composed once, in the shell), rename
  `on_screen_resume` → `on_show` where present.
- **Naming**: classes keep their `*Screen` names rather than being
  renamed to `*Page` — deliberate scope reduction. A rename is cosmetically
  nicer but ripples into every test file's imports for no behavior change;
  not worth it in this pass. Noted here rather than left silent.
- `app.py`: `MODES`/`DEFAULT_MODE`/`switch_mode()` removed. New `NavRail`
  widget (thin wrapper, lives in `app.py` — one file, not a new package,
  consistent with the flatter tree from earlier) shows the four pages
  plus live per-instrument connection dots read from `self.app.handles`,
  the same state Connect already tracks. `c`/`s`/`t`/`i`/F-key bindings
  unchanged except their action body.
- `app.tcss`: new rules for the nav rail's fixed width and the content
  switcher filling remaining height (`ContentSwitcher`'s own default CSS
  is `height: auto`, wrong here — needs `height: 1fr`).
- Tests: `test_app.py` and `test_console_screen.py` assert
  `isinstance(app.screen, XScreen)` in several places — that's no longer
  meaningful once there's one shared `Screen`. Replaced with checking
  `ContentSwitcher.current`.

## Explicitly not in this pass

- No new **Runs** page content — this migration is the shell only, with
  the existing four pages moved into it. Runs is real future work (a
  table over `experiment/io.py`'s saved `.npz` run directories +
  `run_metadata`), sketched in the prior conversation turn but not built
  here.
- No visual polish beyond making the layout functional (colors, icons,
  etc. can follow once the shape is confirmed to work).
