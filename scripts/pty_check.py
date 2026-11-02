"""Check the Vim console in a real pseudo-terminal: the real Textual driver and real escape bytes.

The unit tests drive Textual's headless pilot; this runs the installed ``iyz`` app under a pty,
types into it and reads the screen back with pyte. It needs no instruments and no display.

    uv run scripts/pty_check.py
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import os
import pty
import re
import select
import shutil
import struct
import sys
import tempfile
import termios
import time
from pathlib import Path

import pyte

ROWS, COLS = 34, 110


class Terminal:
    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execvpe(argv[0], argv, env)  # noqa: S606 - a fixed argument list, no shell
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        self.raw = b""

    def pump(self, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if select.select([self.fd], [], [], 0.02)[0]:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    return False
                self.raw += data
                self.stream.feed(data)
        return True

    def text(self) -> str:
        return "\n".join(self.screen.display)

    def wait_for(self, needle: str, timeout: float = 10) -> float | None:
        """Seconds until ``needle`` is on screen (or None)."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            self.pump(0.02)
            if needle in self.text():
                return time.monotonic() - start
        return None

    def press(self, data: bytes, settle: float = 0.3) -> None:
        os.write(self.fd, data)
        self.pump(settle)

    def exited(self, timeout: float = 5) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.pump(0.05)
            done, _ = os.waitpid(self.pid, os.WNOHANG)
            if done:
                return True
        return False


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="iyz-pty-"))
    env = dict(os.environ, TERM="xterm-256color", IYZEE_DATA_DIR=str(work / "data"))
    env["IYZEE_LOG_DIR"] = str(work / "log")
    editor = work / "editor"  # a stand-in for Vim: replaces the file's text
    editor.write_text("#!/bin/sh\necho 'x = 41 + 1' > \"$1\"\n")
    editor.chmod(0o755)
    env["EDITOR"] = str(editor)
    env.pop("VISUAL", None)
    exe = shutil.which("iyz") or str(Path(sys.executable).with_name("iyz"))
    term = Terminal([exe], env)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    try:
        check(
            "the app starts in a real terminal",
            term.wait_for("lab instrument control", 30) is not None,
        )
        term.press(b"i")
        check("the console prompt appears", term.wait_for("In [1]") is not None)

        term.press(b"abc")
        term.pump(0.1)
        term.press(b"\x1b", settle=0)
        latency = term.wait_for("[nav]", 3)
        check(
            "Escape reaches normal mode quickly",
            latency is not None and latency < 0.5,
            f"{latency * 1000:.0f} ms" if latency else "never",
        )

        term.press(b"i")
        term.press(b"\x15foo bar baz")  # Ctrl+U clears the line, then type
        term.press(b"\x1bdd", settle=0.6)  # Esc, d, d in one write: what Alt+d would also send
        screen = term.text()
        check(
            "a fast Esc d d deletes the line (not an Alt+d chord)",
            "[nav]" in screen and "foo bar baz" not in screen,
        )

        term.press(b"i")
        term.press(b"one two")
        for key in (b"\x1b", b"0", b"y", b"w"):  # one write per key, like a typist
            term.press(key, settle=0.15)
        term.pump(0.5)
        found = re.findall(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)", term.raw)
        copied = base64.b64decode(found[-1]).decode() if found else None
        check("a yank is sent to the clipboard as OSC 52", copied == "one ", repr(copied))

        term.press(b"\x1b")  # normal mode at rest: the keyboard goes back to the app
        term.press(b"o")
        check("Escape then a page letter navigates (Scope)", term.wait_for("Scope", 3) is not None)
        term.press(b"i")
        check("i returns to the console prompt", term.wait_for("In [1]", 3) is not None)

        term.press(b"\x1b")
        term.press(b":")
        term.press(b"edit\r")
        check(
            ":edit hands the prompt to $EDITOR and takes the result back",
            term.wait_for("x = 41 + 1", 5) is not None,
        )

        term.press(b"\x1b")
        term.press(b"\x1b")
        term.press(b":")
        term.pump(0.3)
        check(
            "':' opens the command line",
            any(row.startswith(":") for row in term.screen.display[-3:]),
        )
        term.press(b"qq\r")
        check("':qq' quits the app", term.exited(8))
    finally:
        with contextlib.suppress(OSError):
            os.kill(term.pid, 9)
        shutil.rmtree(work, ignore_errors=True)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("screen at the end:\n" + term.text())
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
