"""Textual key events -> the bytes a terminal program expects to receive.

IPython's terminal UI (prompt_toolkit) reads raw terminal input: ``\\r`` for
Enter, ``\\x1b[A`` for Up, ``\\x03`` for Ctrl+C, ``\\x1b`` + key for Alt+key.
Textual has already decoded those into named key events, so the console's
terminal view has to encode them back. This is the xterm convention, which is
what prompt_toolkit's parser understands.
"""

from __future__ import annotations

import re

# Keys that are a fixed byte sequence.
_SIMPLE: dict[str, bytes] = {
    "enter": b"\r",
    "tab": b"\t",
    "escape": b"\x1b",
    "backspace": b"\x7f",
    "space": b" ",
    "shift+tab": b"\x1b[Z",
    "shift+enter": b"\r",  # most terminals cannot tell it from Enter anyway
    "ctrl+enter": b"\r",
}

# CSI cursor keys: ESC [ <final>, or ESC [ 1 ; <modifier> <final> when modified.
_CURSOR_FINAL = {
    "up": "A",
    "down": "B",
    "right": "C",
    "left": "D",
    "home": "H",
    "end": "F",
}

# CSI "tilde" keys: ESC [ <n> ~, or ESC [ <n> ; <modifier> ~ when modified.
_TILDE_NUMBER = {
    "insert": 2,
    "delete": 3,
    "pageup": 5,
    "pagedown": 6,
}

# Function keys. F1-F4 are SS3 sequences; F5+ are CSI tilde keys.
_FUNCTION_SS3 = {"f1": "P", "f2": "Q", "f3": "R", "f4": "S"}
_FUNCTION_TILDE = {
    "f5": 15,
    "f6": 17,
    "f7": 18,
    "f8": 19,
    "f9": 20,
    "f10": 21,
    "f11": 23,
    "f12": 24,
}

_CTRL_PUNCTUATION = {
    "@": b"\x00",
    "backslash": b"\x1c",
    "right_square_bracket": b"\x1d",
    "circumflex_accent": b"\x1e",
    "underscore": b"\x1f",
}

_CTRL_LETTER = re.compile(r"[a-z]")

# xterm's modifier parameter is 1 + (shift=1, alt=2, ctrl=4).
_MODIFIER_BITS = {"shift": 1, "alt": 2, "ctrl": 4}


def _modifier_parameter(modifiers: set[str]) -> int:
    return 1 + sum(_MODIFIER_BITS[m] for m in modifiers)


def key_to_bytes(key: str, character: str | None = None) -> bytes | None:
    """Encode one Textual key event, or return ``None`` if it has no encoding.

    ``key`` is Textual's key name (``"ctrl+r"``, ``"up"``, ``"a"``) and
    ``character`` is the text the key types, if any.

    ``None`` is also returned, on purpose, for **Ctrl+Z**: IPython's terminal
    UI binds it to "suspend to background", which stops the whole process —
    here that is the TUI itself, with no way to resume it.
    """
    # Typed text goes through as-is — but only when no ctrl/alt/meta modifier
    # is involved: Textual may report ``character="a"`` for Alt+A, and taking
    # the shortcut then would silently drop the Alt.
    if (
        character is not None
        and character.isprintable()
        and not key.startswith(("ctrl+", "alt+", "meta+"))
    ):
        return character.encode("utf-8")

    exact = _SIMPLE.get(key)
    if exact is not None:
        return exact

    *modifier_names, base = key.split("+")
    # "a bare +": ``key`` for the plus sign itself is "plus", so split is safe.
    modifiers = set(modifier_names)
    unknown = modifiers - set(_MODIFIER_BITS)
    if unknown:
        return None

    # Alt+<anything>: ESC followed by the key. This is how terminals send
    # Meta/Alt, and how prompt_toolkit expects to receive it.
    if "alt" in modifiers:
        rest = modifiers - {"alt"}
        inner_key = "+".join([*sorted(rest), base]) if rest else base
        inner = key_to_bytes(inner_key, character if len(base) == 1 else None)
        if inner is None and len(base) == 1:
            inner = base.encode("utf-8")
        if inner is None:
            return None
        # For the CSI keys the Alt is folded into the modifier parameter
        # instead (handled below), not sent as an ESC prefix.
        if base not in _CURSOR_FINAL and base not in _TILDE_NUMBER:
            return b"\x1b" + inner

    parameter = _modifier_parameter(modifiers)

    if base in _CURSOR_FINAL:
        final = _CURSOR_FINAL[base]
        if not modifiers:
            return f"\x1b[{final}".encode()
        return f"\x1b[1;{parameter}{final}".encode()

    if base in _TILDE_NUMBER:
        number = _TILDE_NUMBER[base]
        if not modifiers:
            return f"\x1b[{number}~".encode()
        return f"\x1b[{number};{parameter}~".encode()

    if base in _FUNCTION_SS3:
        if not modifiers:
            return f"\x1bO{_FUNCTION_SS3[base]}".encode()
        return f"\x1b[1;{parameter}{_FUNCTION_SS3[base]}".encode()
    if base in _FUNCTION_TILDE:
        number = _FUNCTION_TILDE[base]
        if not modifiers:
            return f"\x1b[{number}~".encode()
        return f"\x1b[{number};{parameter}~".encode()

    if modifiers == {"ctrl"}:
        if base == "z":
            return None  # see the docstring: would suspend the whole TUI
        if _CTRL_LETTER.fullmatch(base):
            return bytes([ord(base) - 96])
        return _CTRL_PUNCTUATION.get(base)

    if not modifiers and len(base) == 1:
        return base.encode("utf-8")
    return None
