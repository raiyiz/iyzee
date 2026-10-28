"""``vi_keys``: `.` repeat, mode / at-rest state and the yank-only clipboard, headlessly.

A real ``PromptSession`` (prompt_toolkit's own vi engine) is driven through its key processor with
no terminal. Keys are fed as ``KeyPress`` objects that carry the same data a terminal would, because
macros and our `.` replay by re-parsing that data.
"""

from __future__ import annotations

import pytest
from helpers import async_test
from prompt_toolkit.application.current import set_app
from prompt_toolkit.clipboard import ClipboardData
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.input import DummyInput
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.shortcuts import PromptSession

from iyzee.tui import vi_keys

SPECIAL = {
    "<esc>": (Keys.Escape, "\x1b"),
    "<cr>": (Keys.ControlM, "\r"),
    "<c-v>": (Keys.ControlV, "\x16"),
}


class Prompt:
    """A vi prompt with our additions installed, fed keys synchronously."""

    def __init__(self, text: str = "", cursor: int = 0, *, normal: bool = True) -> None:
        self.copied: list[str] = []
        self.session: PromptSession[str] = PromptSession(
            input=DummyInput(), output=DummyOutput(), editing_mode=EditingMode.VI, multiline=True
        )
        self.extras = vi_keys.install(self.session, on_copy=self.copied.append)
        self.buffer = self.session.default_buffer
        self.buffer.text = text
        self.buffer.cursor_position = cursor
        if normal:  # prompt_toolkit (and IPython) start in insert mode
            self.session.app.vi_state.input_mode = InputMode.NAVIGATION

    def keys(self, sequence: str) -> Prompt:
        presses: list[KeyPress] = []
        i = 0
        while i < len(sequence):
            end = sequence.find(">", i)
            if sequence[i] == "<" and end != -1 and sequence[i : end + 1] in SPECIAL:
                presses.append(KeyPress(*SPECIAL[sequence[i : end + 1]]))
                i = end + 1
            else:
                presses.append(KeyPress(sequence[i], sequence[i]))
                i += 1
        with set_app(self.session.app):
            processor = self.session.app.key_processor
            processor.feed_multiple(presses)
            processor.process_keys()
        return self

    @property
    def text(self) -> str:
        return self.buffer.text


@pytest.mark.parametrize(
    ("text", "keys", "expected"),
    [
        ("a b c d", "dw..", "d"),
        ("aa bb cc", "ciwXY<esc>w.", "XY XY cc"),  # an insert command with its typed text
        ("x1\nx2", "A;<esc>j.", "x1;\nx2;"),
        ("1 2 3 4 5", "dw3.", "5"),  # a count repeats the change that many times
        ("a b c d", "dww.", "b d"),  # a motion in between is not a change
        ("a b c", "dwyw.", "c"),  # nor is a yank
        ("abc", ".", "abc"),  # nothing recorded yet
    ],
)
@async_test
async def test_dot_repeats_the_last_change(text: str, keys: str, expected: str) -> None:
    assert Prompt(text).keys(keys).text == expected


@async_test
async def test_dot_ignores_typing_at_a_fresh_prompt_and_survives_a_new_prompt() -> None:
    # A prompt starts in insert mode, so this typing did not begin from normal mode.
    fresh = Prompt(normal=False).keys("foo<esc>")
    assert fresh.extras.last_change == [] and fresh.keys(".").text == "foo"

    prompt = Prompt("a b c").keys("dwA")  # half-way through an insert command...
    prompt.session.app.key_processor.reset()  # ...when the prompt is accepted and reset
    prompt.session.app.vi_state.input_mode = InputMode.NAVIGATION
    prompt.buffer.text, prompt.buffer.cursor_position = "x y z", 0
    assert prompt.keys(".").text == "y z"  # `dw` is still what `.` repeats


@pytest.mark.parametrize(
    ("keys", "mode", "at_rest"),
    [
        ("", "normal", True),
        ("d", "normal", False),  # operator pending
        ("3", "normal", False),  # count pending
        ("i", "insert", False),
        ("R", "replace", False),
        ("v", "visual", False),
        ("V", "visual-line", False),
        ("<c-v>", "visual-block", False),
        ("d<esc>", "normal", True),  # Escape cancels the operator
    ],
)
@async_test
async def test_mode_and_at_rest(keys: str, mode: str, at_rest: bool) -> None:
    prompt = Prompt("hello world").keys(keys)
    assert (prompt.extras.mode, prompt.extras.at_rest) == (mode, at_rest)


@pytest.mark.parametrize(
    ("text", "keys", "copied"),
    [
        ("foo bar", "yiw", ["foo"]),  # any length: a short yank is still a yank
        ("foo\nbar", "yy", ["foo"]),
        ("foo bar", "vey", ["foo"]),  # visual yank
        ("foo bar", "dw", []),  # deletes and changes stay in the prompt
        ("foo bar", "cwX<esc>", []),
        ("foo bar", "vd", []),
    ],
)
@async_test
async def test_only_yanks_reach_the_system_clipboard(
    text: str, keys: str, copied: list[str]
) -> None:
    assert Prompt(text).keys(keys).copied == copied


def test_a_failing_copy_callback_never_breaks_editing() -> None:
    def boom(text: str) -> None:
        raise RuntimeError("clipboard unavailable")

    clipboard = vi_keys.BridgingClipboard(boom)
    clipboard.set_data(ClipboardData("kept"))  # must not raise
    assert clipboard.get_data().text == "kept"


def test_which_key_sequences_dot_repeats() -> None:
    def change(typed: str) -> bool:
        return vi_keys._is_change([KeyPress(c, c) for c in typed])

    assert all(map(change, ("dw", "3dd", "ciw", "A", "gU", "r5")))  # "r5": 5 is an argument
    assert not any(map(change, ("gg", "yw", "w", "u", "v", "0", "")))
