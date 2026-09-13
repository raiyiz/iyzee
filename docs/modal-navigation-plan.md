# Iyzee Modal Navigation Plan

## Goal

Add a small, reusable **Iyzee Command Mode** layer to the TUI on `feat/ipython-console`.

The experience should feel Vim-like without attempting to reimplement Vim:

- keyboard-first navigation between pages, controls, tables, and buttons;
- a `:` command line at the bottom for application commands;
- clear modal state;
- normal Textual widgets continue to behave naturally;
- the embedded IPython console keeps its own editing/completion behavior.

The central architectural rule is:

> **Navigation owns global intent; widgets own editing/interaction; screens own application behavior.**

---

## 1. Three modes from the start

Implement exactly three modes:

```text
NORMAL
INSERT
COMMAND
```

### NORMAL

Global navigation mode.

Typical keys:

```text
j / k       move focus forward / backward
Enter       activate focused widget
Space       activate / toggle focused widget
gg          first focusable widget
G           last focusable widget
Ctrl-d      page down
Ctrl-u      page up
:           enter COMMAND mode
?           show help
q           quit
c           Connect
s           Sweep
t           Traces
i           IPython
```

### INSERT

Editing/interaction mode owned by the currently focused widget.

Any widget that is meaningfully accepting user input should transition into INSERT mode when it receives focus or starts editing, for example:

- `Input`
- `TextArea`
- text-entry portions of `Select`
- future editable controls

In INSERT mode, **the widget gets first ownership of keyboard events**.

Examples:

```text
Input focused
    ↓
INSERT
    ↓
typing behaves normally
    ↓
Esc
    ↓
NORMAL
```

For the embedded IPython console:

```text
console TextArea
    ↓
INSERT
    ↓
Python/IPython editing, completion, history, etc.
    ↓
Esc
    ↓
NORMAL
```

Do not make global `j/k/:/...` bindings compete with text editing.

The navigation layer should provide a clear way for widgets to opt into INSERT behavior rather than maintaining a fragile hard-coded list forever.

A useful abstraction is a small navigation/editing policy such as:

```python
class NavigationPolicy(Protocol):
    def mode_for(self, widget: Widget) -> Mode: ...
```

The first implementation can use known Textual editing widgets, but the API should leave room for custom widgets.

### COMMAND

Opened with `:` from NORMAL mode.

The bottom command bar becomes visible:

```text
COMMAND │ :sweep_
```

Behavior:

```text
Tab        command completion
Enter      execute command
Esc        cancel
Up/Down    command history
```

After execution or cancellation:

```text
COMMAND
   ↓
NORMAL
```

---

# 2. Package structure

Add:

```text
src/iyzee/tui/navigation/
├── __init__.py
├── controller.py     # mode state + global event routing
├── mode.py           # NORMAL / INSERT / COMMAND
├── commands.py       # command registry + parsing + completion
├── focus.py          # focus traversal / semantic focus
└── command_bar.py    # bottom : command-line widget
```

Keep domain behavior where it already lives:

```text
src/iyzee/tui/screens/connect.py
src/iyzee/tui/screens/sweep.py
src/iyzee/tui/screens/traces.py
src/iyzee/tui/screens/console.py
```

`IyzeeApp` should own the navigation controller and command registry.

---

# 3. Focus model

Use Textual's existing focus system. Do **not** create a second global cursor model.

Start with deterministic focus traversal:

```text
j  → next focusable widget
k  → previous focusable widget
gg → first focusable widget
G  → last focusable widget
```

Activation:

```text
Enter → activate focused widget
Space → activate / toggle focused widget
```

The first version should **not** attempt clever spatial `h/l` navigation.

Once the basic system is reliable, consider:

```text
h / l
Ctrl-d / Ctrl-u
```

and more advanced spatial navigation.

## Semantic focus

Support stable IDs for important controls so commands can jump directly to them:

```text
:focus run-sweep
:focus abort-sweep
:focus rbw-start
:focus instrument-table
```

Prefer explicit IDs over labels or widget text, because labels may change.

---

# 4. Command system

Use a registry instead of a large `if/elif` command handler.

Conceptually:

```python
@dataclass(frozen=True)
class Command:
    name: str
    description: str
    callback: Callable[[list[str]], None]
```

Register core commands:

```text
:connect
:sweep
:traces
:console
:quit
:help
:focus <target>
```

Useful aliases:

```text
:c
:s
:t
:i
:q
```

Initial grammar:

```text
:command [arg1] [arg2]
```

Keep parsing intentionally small. There is no need to recreate Vim's full Ex language.

Design the registry so arguments and completion can grow later:

```text
:connect mxa
:disconnect shutter
:sweep bandwidth
:focus rbw-start
```

---

# 5. Command bar

Create a reusable `CommandBar` widget anchored at the bottom of the app.

Target appearance:

```text
────────────────────────────────────────────
NORMAL │ Sweep │ MXA connected

:sweep_
```

When COMMAND mode is active:

```text
COMMAND │ :sweep_
```

Required behavior:

```text
:          open
Tab        completion
Enter      execute
Esc        cancel
↑ / ↓      command history
Ctrl-C     cancel
```

Keep command history separate from IPython history.

---

# 6. Status line

Add a lightweight global status line so the current mode is always obvious.

Examples:

```text
NORMAL  │ Sweep │ MXA connected
INSERT  │ RBW start
COMMAND │ :focus run-sweep_
```

Do not duplicate the IPython console's execution status. The global status line reports navigation mode; the console status continues to report IPython execution state.

Keep styling consistent with the existing `app.tcss`.

---

# 7. Integration into `IyzeeApp`

`IyzeeApp` currently owns global bindings for:

```text
c
s
t
i
q
ctrl+q
```

Move the global navigation responsibility into the navigation layer.

The app should conceptually become:

```text
IyzeeApp
├── navigation controller
├── command registry
└── screens
```

The navigation layer should invoke existing app actions:

```python
app.action_show_connect()
app.action_show_sweep()
app.action_show_traces()
app.action_show_console()
```

Do not duplicate screen-switching logic inside navigation.

---

# 8. Event ownership rules

This is the most important implementation detail.

Keyboard events should flow roughly like:

```text
Key event
   │
   ▼
navigation controller
   │
   ├── INSERT → focused widget gets ownership
   │
   ├── COMMAND → command bar gets ownership
   │
   └── NORMAL → navigation handles global keys
```

The navigation controller must not swallow events belonging to widgets that are actively editing.

In particular, preserve the existing IPython console behavior:

- text editing;
- completion;
- history;
- Shift+Enter execution;
- other IPython interactions.

The console already contains its own bindings and `TextArea`; the new navigation layer must coexist with them rather than overriding them.

---

# 9. Recommended keymap

Start deliberately small.

| Key | Action |
|---|---|
| `j` | next focus |
| `k` | previous focus |
| `Enter` | activate focused widget |
| `Space` | activate / toggle |
| `Esc` | leave INSERT / COMMAND |
| `gg` | first focus |
| `G` | last focus |
| `Ctrl-d` | page down |
| `Ctrl-u` | page up |
| `:` | command mode |
| `?` | help |
| `q` | quit |
| `c` | Connect |
| `s` | Sweep |
| `t` | Traces |
| `i` | IPython |

Do not add a large Vim keymap until the basic interactions have been used in practice.

---

# 10. Optional later extensions

Only after the core model feels good:

```text
h / l             spatial focus
[g / ]g           previous / next navigation group
Ctrl-w h/j/k/l    pane navigation
f <key>           quick-focus a named control
:connect <name>   command arguments
```

Navigation groups could later organize controls such as:

```text
Sweep
├── configuration
│   ├── sweep-type
│   ├── rbw-start
│   ├── rbw-stop
│   └── rbw-steps
└── controls
    ├── run-sweep
    └── abort-sweep
```

But this is explicitly a second phase.

---

# 11. Commit strategy

Keep the work split into small, independently reviewable commits.

## Commit 1

### `refactor(tui): introduce navigation package`

Create the package and controller skeleton.

Tasks:

- create `tui/navigation/`;
- add mode definitions;
- add controller skeleton;
- add command registry skeleton;
- wire the controller into `IyzeeApp`;
- preserve current behavior.

Acceptance:

```text
c / s / t / i / q
```

continue to behave exactly as before.

---

## Commit 2

### `feat(tui): add normal insert and command modes`

Implement the three-mode state machine:

```text
NORMAL
INSERT
COMMAND
```

Tasks:

- explicit mode transitions;
- `:` enters COMMAND;
- `Esc` leaves COMMAND;
- focus of editable widgets enters INSERT;
- `Esc` returns INSERT → NORMAL;
- widget event ownership is respected.

Acceptance:

- typing into `Input` remains normal;
- typing into `TextArea` remains normal;
- IPython editing remains normal;
- `:` reliably opens the command UI;
- `Esc` reliably returns to NORMAL.

This is the key architectural commit.

---

## Commit 3

### `feat(tui): add keyboard focus navigation`

Implement:

```text
j
k
gg
G
Enter
Space
```

Tasks:

- focus traversal using Textual;
- first/last focus;
- activation of focused widgets;
- ignore non-focusable widgets;
- tests for focus behavior.

Acceptance:

A screen can be operated without the mouse using NORMAL mode.

---

## Commit 4

### `feat(tui): add command bar and command registry`

Implement:

```text
:connect
:sweep
:traces
:console
:quit
:help
```

plus aliases.

Tasks:

- bottom command bar;
- command parser;
- registry;
- completion;
- command history;
- cancellation;
- returning to NORMAL after execution.

Acceptance:

```text
:sweep
```

actually navigates to Sweep, and:

```text
:focus <id>
```

can be added without modifying the controller itself.

---

## Commit 5

### `feat(tui): add semantic focus targets`

Tasks:

- define stable navigation IDs for important controls;
- implement `:focus <target>`;
- add focus tests.

Examples:

```text
:focus run-sweep
:focus abort-sweep
:focus rbw-start
```

Acceptance:

Users can jump directly to important controls.

---

## Commit 6

### `feat(tui): add navigation status line`

Tasks:

- global mode/status widget;
- NORMAL / INSERT / COMMAND rendering;
- integrate current screen/context;
- keep styling minimal.

Acceptance:

The current mode is always visually obvious.

---

## Commit 7

### `feat(tui): improve spatial navigation`

Only after real interaction testing.

Candidate additions:

```text
h / l
Ctrl-d / Ctrl-u
navigation groups
```

Acceptance:

Navigation improvements are based on observed screen layouts rather than speculative complexity.

---

# 12. Testing strategy

Test the pure navigation pieces first:

```text
mode transitions
command parsing
command registry
command completion
command history
focus ordering
semantic focus lookup
```

Then add Textual integration tests.

Minimum interaction coverage:

```text
press(":")
type("sweep")
press("enter")
→ Sweep screen

press("j")
→ next focusable widget

press("k")
→ previous focusable widget

focus an Input
type("123")
→ Input contains "123"

press("escape")
→ NORMAL

focus the IPython TextArea
type("1 + 1")
→ text editing works normally

press("escape")
→ NORMAL
```

Also test that NORMAL-mode keys do not leak into INSERT mode.

---

# 13. Definition of done

The feature is complete when the application can be used comfortably like this:

```text
NORMAL

j
j
Enter
        # connect selected instrument

s
        # open Sweep

j
j
j
        # navigate to a field

:focus run-sweep
Enter
        # focus Run sweep

Enter
        # activate it

:console
Enter
        # open IPython
```

The mouse must continue to work normally.

The final UX should feel like a keyboard-native TUI, not like a Vim emulator bolted onto Textual.

---

# 14. Design principles

Keep these constraints throughout the implementation:

1. **Three modes from day one:** NORMAL, INSERT, COMMAND.
2. **INSERT belongs to the focused widget.**
3. **COMMAND belongs to the global command bar.**
4. **NORMAL owns application-level navigation.**
5. **Use Textual's focus system instead of inventing another cursor model.**
6. **Screens retain domain logic.**
7. **Commands invoke existing app/screen actions.**
8. **Do not interfere with IPython/Textual editing behavior.**
9. **Prefer small, composable abstractions over a giant key-handler.**
10. **Only add advanced Vim behaviors after the simple model has proven itself.**
