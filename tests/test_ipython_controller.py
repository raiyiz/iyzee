"""Behavioral tests for the non-terminal IPython execution controller."""

from __future__ import annotations

import threading

from helpers import FakeApp

from iyzee.tui.ipython_controller import IPythonSession


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
        assert session.submit("print('hello'); answer + 2")
        _finish(session)
        rendered = "".join(output)
        assert "hello" in rendered
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
        matches = session.complete("lab.", cursor_pos=4)
        assert ".mx" in matches and ".results" in matches
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
