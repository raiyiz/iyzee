"""Vim behaviour for the console's prompt that IPython and prompt_toolkit leave out or get wrong.

The console runs IPython's own prompt, whose vi mode is prompt_toolkit's (``key_binding/bindings/
vi.py``). This module adds to *that engine* rather than reimplementing Vim next to it, so every
motion, operator, text object, register and macro stays prompt_toolkit's and is maintained there:

* **Escape is instant, and only ever Escape.** IPython enables emacs bindings in vi insert mode, one
  of which is every ``Alt+<letter>`` as ``Esc`` followed by that letter. A bare Escape then has to
  wait to see whether a letter follows, and when one does within the timeout it is read as the
  Alt chord instead of "leave insert mode": ``Esc d d`` typed quickly kills a word and types a
  literal ``d``, and the prompt stays in insert mode. (IPython's own timeout settings are only ever
  applied from a ``modal_cursor`` hook this app turns off, so the wait was prompt_toolkit's
  default 1.5 s.) :func:`install` makes Escape an *eager* binding: it wins at once.
* **``.`` repeats the last change** (``dw``, ``ciw`` + typed text + ``Esc``, ``A;`` ...), which
  prompt_toolkit's vi mode does not have (python-prompt-toolkit issue #503). It is recorded from
  the key sequences of the engine's own handler calls and replayed by feeding them back, so it
  repeats whatever the engine does, including operators and text objects added upstream later.
* **Yanks reach the system clipboard** through ``on_copy`` (the app passes OSC 52), like Vim's
  ``"+y``; deletes and changes stay in the prompt's own register, as in plain Vim. The terminal
  view captures the mouse, so there is no other way to get text out. prompt_toolkit has a single
  clipboard, so a yank is told from a delete by the operator that is running (see ``_is_yank``).
* :attr:`ViExtras.mode` and :attr:`ViExtras.at_rest` let the view draw a mode-aware cursor and
  decide whether Escape / ``:`` belong to the prompt or to the app.

Nothing here touches Textual; it works on a ``PromptSession`` and can be driven headlessly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from prompt_toolkit.clipboard import ClipboardData, InMemoryClipboard
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import Condition, vi_insert_mode, vi_navigation_mode, vi_replace_mode
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys
from prompt_toolkit.selection import SelectionType

if TYPE_CHECKING:
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent
    from prompt_toolkit.shortcuts import PromptSession

log = logging.getLogger("iyzee.tui")

# How long prompt_toolkit waits for the rest of a *terminal escape sequence* before deciding a lone
# ESC byte is the Escape key. The virtual terminal delivers whole sequences at once, so this only
# has to cover scheduling; it is IPython's own intended value (0.01) rather than prompt_toolkit's 0.5.
ESCAPE_FLUSH_SECONDS = 0.01

# First keys (after any count) of the normal-mode commands that ``.`` can repeat. Yanks, motions,
# undo, visual selections and macros are deliberately not here: Vim does not repeat them either.
_CHANGE_KEYS = frozenset("dcxXDCpPsSoOiaIArRJ~<>")
_CHANGE_AFTER_G = frozenset("uU~J")


def _is_change(keys: list[KeyPress]) -> bool:
    """Is this recorded key sequence a repeatable change (as opposed to a motion or a yank)?"""
    typed = [k.key for k in keys if isinstance(k.key, str) and len(k.key) == 1]
    count_digits = 0
    while count_digits < len(typed) and typed[count_digits].isdigit():
        count_digits += 1
    typed = typed[count_digits:]  # a leading count (``3dd``); a later digit is an argument (``r5``)
    if not typed:
        return False
    if typed[0] == "g":
        return len(typed) > 1 and typed[1] in _CHANGE_AFTER_G
    return typed[0] in _CHANGE_KEYS


# The operator function prompt_toolkit's ``y{motion}`` / ``y{text object}`` runs.
_YANK_OPERATOR = "_yank"


class BridgingClipboard(InMemoryClipboard):
    """prompt_toolkit's in-memory clipboard that also hands copied text to ``on_copy``.

    ``should_copy`` is asked at the moment of each ``set_data``; it is how yanks (copied out) are
    told from deletes (kept in the prompt's register only).
    """

    def __init__(
        self, on_copy: Callable[[str], object], should_copy: Callable[[], bool] = lambda: True
    ) -> None:
        super().__init__()
        self._on_copy = on_copy
        self._should_copy = should_copy

    def set_data(self, data: ClipboardData) -> None:
        super().set_data(data)
        if data.text and self._should_copy():
            try:
                self._on_copy(data.text)
            except Exception:
                log.exception("copying to the system clipboard failed")


class ViExtras:
    """The additions described in the module docstring, attached to one ``PromptSession``."""

    def __init__(self, session: PromptSession[Any]) -> None:
        self._session = session
        self.app = session.app
        self.last_change: list[KeyPress] = []
        self._recording: list[KeyPress] = []
        self._recording_from_rest = False
        self._replaying = False
        self._yanking = False

    # -- state the UI reads (from the UI thread; plain attribute reads) ---------------------

    @property
    def mode(self) -> str | None:
        """``insert``, ``normal``, ``replace``, ``visual``, ``visual-line`` or ``visual-block``."""
        if self.app.editing_mode != EditingMode.VI:
            return None
        buffer = self._session.default_buffer
        selection = buffer.selection_state
        if selection is not None:
            return {
                SelectionType.LINES: "visual-line",
                SelectionType.BLOCK: "visual-block",
            }.get(selection.type, "visual")
        return {
            InputMode.INSERT: "insert",
            InputMode.INSERT_MULTIPLE: "insert",
            InputMode.REPLACE: "replace",
            InputMode.NAVIGATION: "normal",
        }.get(self.app.vi_state.input_mode)

    @property
    def at_rest(self) -> bool:
        """Normal mode with nothing half-typed: no operator, count, register, search or menu."""
        return self._at_rest(ignore_key_buffer=False)

    def _at_rest(self, *, ignore_key_buffer: bool) -> bool:
        processor = self.app.key_processor
        state = self.app.vi_state
        buffer = self._session.default_buffer
        return (
            self.app.editing_mode == EditingMode.VI
            and state.input_mode == InputMode.NAVIGATION
            and state.operator_func is None
            and not state.waiting_for_digraph
            and processor.arg is None
            and (ignore_key_buffer or not processor.key_buffer)
            and buffer.selection_state is None
            and buffer.complete_state is None
            and not self.app.layout.search_target_buffer_control
        )

    # -- installation -------------------------------------------------------------------------

    def install(self, *, on_copy: Callable[[str], object] | None = None) -> None:
        self.app.ttimeoutlen = ESCAPE_FLUSH_SECONDS
        bindings = self._bindings()
        self._session.key_bindings = _merge(self._session.key_bindings, bindings)
        self._record_changes()
        if on_copy is not None:
            self._session.clipboard = BridgingClipboard(on_copy, lambda: self._yanking)

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()
        replaying = Condition(lambda: self._replaying)
        can_repeat = Condition(lambda: bool(self.last_change))

        @kb.add("escape", eager=True, filter=vi_insert_mode | vi_replace_mode)
        def _leave_insert(event: KeyPressEvent) -> None:
            # prompt_toolkit's own Escape handler, but eager (see the module docstring).
            buffer = event.current_buffer
            buffer.cursor_position += buffer.document.get_cursor_left_position()
            event.app.vi_state.input_mode = InputMode.NAVIGATION
            if buffer.selection_state:
                buffer.exit_selection()

        @kb.add(".", filter=vi_navigation_mode & can_repeat, record_in_macro=False)
        def _repeat_last_change(event: KeyPressEvent) -> None:
            keys = [KeyPress(k.key, k.data) for k in self.last_change] * max(1, event.arg)
            self._replaying = True
            # The sentinel marks where the replay ends, so that it is never recorded as a change.
            event.app.key_processor.feed_multiple([*keys, KeyPress(Keys.Ignore, "")], first=True)

        @kb.add(Keys.Ignore, filter=replaying, eager=True, record_in_macro=False)
        def _end_of_replay(event: KeyPressEvent) -> None:
            self._replaying = False

        return kb

    def _is_yank(self, key_sequence: list[KeyPress]) -> bool:
        """Is the handler about to run a yank (``y{motion}``, ``yy``, ``Y``, visual ``y``)?"""
        keys = [k.key for k in key_sequence]
        operator = getattr(self.app.vi_state.operator_func, "__name__", "")
        visual = self._session.default_buffer.selection_state is not None
        return (
            operator == _YANK_OPERATOR or keys in (["y", "y"], ["Y"]) or (visual and keys == ["y"])
        )

    def _record_changes(self) -> None:
        """Record each repeatable change, from the engine's own handler calls."""
        processor = self.app.key_processor
        call_handler = processor._call_handler
        reset = processor.reset

        def recording_call_handler(handler: Any, key_sequence: list[KeyPress]) -> None:
            replaying = self._replaying
            from_rest = self._at_rest(ignore_key_buffer=True)
            self._yanking = self._is_yank(key_sequence)
            try:
                call_handler(handler, key_sequence)
            finally:
                self._yanking = False
            if replaying:
                return
            if from_rest:
                self._recording = list(key_sequence)
                self._recording_from_rest = True
            elif self._recording_from_rest:
                self._recording.extend(key_sequence)
            if self._at_rest(ignore_key_buffer=True):
                if self._recording_from_rest and _is_change(self._recording):
                    self.last_change = self._recording
                self._recording = []
                self._recording_from_rest = False

        def recording_reset() -> None:
            # A new prompt: whatever was half-recorded belongs to the previous one.
            self._recording = []
            self._recording_from_rest = False
            self._replaying = False
            reset()

        processor._call_handler = recording_call_handler  # type: ignore[method-assign]
        processor.reset = recording_reset  # type: ignore[method-assign]


def _merge(existing: Any, extra: KeyBindings) -> Any:
    from prompt_toolkit.key_binding import merge_key_bindings

    return extra if existing is None else merge_key_bindings([existing, extra])


def install(
    session: PromptSession[Any], *, on_copy: Callable[[str], object] | None = None
) -> ViExtras:
    """Attach the additions to ``session`` (an IPython shell's ``pt_app``) and return the handle."""
    extras = ViExtras(session)
    extras.install(on_copy=on_copy)
    return extras
