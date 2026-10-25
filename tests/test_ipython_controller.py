"""Behavioral tests for the non-terminal IPython execution controller."""

from __future__ import annotations

import builtins
import sys
import time

import pytest
from helpers import FakeApp

from iyzee.tui.ipython_controller import Completion, IPythonSession


def _session() -> tuple[IPythonSession, list[str]]:
    output: list[str] = []
    session = IPythonSession(FakeApp(), on_output=output.append)
    session.start()
    return session, output


def _finish(session: IPythonSession) -> None:
    assert session._idle.wait(5), "cell execution did not finish"


def test_cells_share_state_and_route_output() -> None:
    session, output = _session()
    try:
        assert session.submit("answer = 40")
        _finish(session)
        assert session.submit(
            "import sys; print('hello'); sys.stdout.writelines(['via ', 'writelines\\n']); answer + 2"
        )
        _finish(session)
        rendered = "".join(output)
        assert "hello" in rendered
        assert "via writelines" in rendered
        assert "42" in rendered
    finally:
        assert session.close()


def test_exception_is_reported_and_next_cell_still_runs() -> None:
    session, output = _session()
    try:
        assert session.submit("1 / 0")
        _finish(session)
        assert "ZeroDivisionError" in "".join(output)
        assert session.submit("6 * 7")
        _finish(session)
        assert "42" in "".join(output)
    finally:
        assert session.close()


def test_completion_and_live_lab_namespace() -> None:
    session, _output = _session()
    try:
        texts = [c.text for c in session.complete("lab.", cursor_pos=4)]
        assert ".mx" in texts and ".results" in texts
        assert session.submit("lab.connected")
        _finish(session)
    finally:
        assert session.close()


def test_input_is_explicitly_unsupported_and_worker_closes() -> None:
    session, output = _session()
    try:
        assert session.submit("input('value? ')")
        _finish(session)
        assert "input() is not available" in "".join(output)
    finally:
        assert session.close()


def test_completion_reports_the_range_it_replaces() -> None:
    session, _output = _session()
    try:
        # The text replaces source[start:end]; it is not a suffix to append.
        assert session.complete("pri") == [Completion(0, 3, "print")]
        assert session.complete("lab.m") == [Completion(3, 5, ".mx")]
    finally:
        assert session.close()


def test_output_contains_no_ansi_escape_codes() -> None:
    session, output = _session()
    try:
        # Tracebacks and `?` help are coloured by default; the console log
        # prints text verbatim, so any escape would show up as garbage.
        assert session.submit("1 / 0")
        _finish(session)
        assert session.submit("len?")
        _finish(session)
        rendered = "".join(output)
        assert "ZeroDivisionError" in rendered and "Docstring" in rendered
        assert "\x1b" not in rendered
    finally:
        assert session.close()


def test_interrupt_stops_a_python_loop_and_console_stays_usable() -> None:
    session, output = _session()
    try:
        assert session.submit("while True: pass")
        deadline = time.monotonic() + 5
        while not session._idle.wait(0.05):  # interrupt() is a no-op until the cell starts
            assert time.monotonic() < deadline, "cell was not interrupted"
            session.interrupt()
        assert "KeyboardInterrupt" in "".join(output)
        assert session.submit("6 * 7")
        _finish(session)
    finally:
        assert session.close()


def test_stray_interrupts_never_kill_the_worker() -> None:
    session, output = _session()
    try:
        # Interrupts fired at cell boundaries (queued, finishing, idle) must be
        # ignored; one landing in the worker's own bookkeeping used to kill it
        # and leave the console permanently busy.
        deadline = time.monotonic() + 20
        for _ in range(150):
            assert session.submit("pass")
            while session.executing:
                assert time.monotonic() < deadline, "console is stuck busy"
                session.interrupt()
            session.interrupt()
            assert session._thread is not None and session._thread.is_alive()
        assert session.submit("6 * 7")
        _finish(session)
        assert "42" in "".join(output)
    finally:
        assert session.close()


@pytest.mark.parametrize(
    "source",
    [
        "import sys; sys.stdin.readline()",
        "import sys; sys.stdin.read()",
        "import sys; sys.stdin.readlines()",
        "import sys\nfor line in sys.stdin: pass",
        "import sys; sys.stdin.fileno()",
        "import sys; sys.stdin.buffer.readline()",
        "import sys; sys.stdin.detach()",
    ],
)
def test_reading_stdin_fails_fast_instead_of_blocking(source: str) -> None:
    session, output = _session()
    try:
        # Reading the real stdin would block the worker for good (interrupts
        # cannot free a blocked read) and would steal Textual's keystrokes.
        assert session.submit(source)
        _finish(session)
        assert "not available in the cell console" in "".join(output)
        assert session.submit("6 * 7")
        _finish(session)
    finally:
        assert session.close()


def test_close_restores_process_wide_streams_and_input() -> None:
    before = (sys.stdin, sys.stdout, sys.stderr, builtins.input)
    session, _output = _session()
    assert (sys.stdin, sys.stdout, sys.stderr, builtins.input) != before
    assert session.close()
    assert (sys.stdin, sys.stdout, sys.stderr, builtins.input) == before


@pytest.mark.parametrize("source", ["%debug", "%pdb on"])
def test_interactive_debugger_magics_fail_fast_and_worker_stays_usable(source: str) -> None:
    session, output = _session()
    try:
        assert session.submit(source)
        _finish(session)
        assert "interactive debugger prompts" in "".join(output)
        assert session.submit("6 * 7")
        _finish(session)
        assert "42" in "".join(output)
    finally:
        assert session.close()


def test_binary_stdout_access_fails_fast_and_worker_stays_usable() -> None:
    session, output = _session()
    try:
        assert session.submit("import sys; sys.stdout.buffer.write(b'raw')")
        _finish(session)
        assert "binary stream access is not supported" in "".join(output)
        assert session.submit("6 * 7")
        _finish(session)
        assert "42" in "".join(output)
    finally:
        assert session.close()
