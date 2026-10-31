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


def _encode_terminal_key(base: str, modifiers: set[str]) -> bytes | None:
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
        final = _FUNCTION_SS3[base]
        if not modifiers:
            return f"\x1bO{final}".encode()
        return f"\x1b[1;{parameter}{final}".encode()
    if base in _FUNCTION_TILDE:
        number = _FUNCTION_TILDE[base]
        if not modifiers:
            return f"\x1b[{number}~".encode()
        return f"\x1b[{number};{parameter}~".encode()
    return None


def _encode_control_key(base: str) -> bytes | None:
    if base == "z":
        return None
    if _CTRL_LETTER.fullmatch(base):
        return bytes([ord(base) - 96])
    return _CTRL_PUNCTUATION.get(base)


def _alt_prefix(
    base: str,
    character: str | None,
    modifiers: set[str],
) -> tuple[bool, bytes | None]:
    if "alt" not in modifiers:
        return False, None
    rest = modifiers - {"alt"}
    inner_key = "+".join([*sorted(rest), base]) if rest else base
    inner = key_to_bytes(inner_key, character if len(base) == 1 else None)
    if inner is None and len(base) == 1:
        inner = base.encode("utf-8")
    if inner is None:
        return True, None
    if base in _CURSOR_FINAL or base in _TILDE_NUMBER:
        return False, None
    return True, b"\x1b" + inner


def key_to_bytes(key: str, character: str | None = None) -> bytes | None:
    """Encode one Textual key event, or return ``None`` if it has no encoding.

    ``key`` is Textual's key name (``"ctrl+r"``, ``"up"``, ``"a"``) and
    ``character`` is the text the key types, if any.

    ``None`` is also returned, on purpose, for **Ctrl+Z**: IPython's terminal
    UI binds it to "suspend to background", which stops the whole process —
    here that is the TUI itself, with no way to resume it.
    """
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
    modifiers = set(modifier_names)
    if modifiers - set(_MODIFIER_BITS):
        return None

    handled, encoded = _alt_prefix(base, character, modifiers)
    if handled:
        return encoded

    terminal = _encode_terminal_key(base, modifiers)
    if terminal is not None:
        return terminal
    if modifiers == {"ctrl"}:
        return _encode_control_key(base)
    if not modifiers and len(base) == 1:
        return base.encode("utf-8")
    return None
