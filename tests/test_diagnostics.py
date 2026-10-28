"""Evidence left behind when the process dies: the session marker, crash hooks, signals.

The parts that are about the process actually dying run in a subprocess: a real segfault, a
real SIGTERM against a real (headless) app. Anything else would test a model of it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import async_test, notifications

from iyzee.tui import app as app_mod
from iyzee.tui.diagnostics import SessionMarker, describe

ROOT = Path(__file__).resolve().parent.parent


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


# -- the session marker ----------------------------------------------------------------------


def test_the_marker_is_kept_while_running_and_stale_ones_are_reported_once(tmp_path: Path) -> None:
    # -- the marker lives while the session runs is updated and removed on clean exit
    marker = SessionMarker(tmp_path)

    assert marker.start() == []
    assert json.loads(marker.path.read_text())["pid"] == os.getpid()

    marker.note("bandwidth sweep running (19 points)")  # replaces the activity atomically
    assert json.loads(marker.path.read_text())["activity"] == "bandwidth sweep running (19 points)"
    assert not list(tmp_path.glob("*.tmp"))

    marker.stop()
    assert not marker.path.exists()

    # -- only markers of dead processes are reported once and damage never stops startup
    left_behind = tmp_path / "session-1.json"
    left_behind.write_text(
        json.dumps(
            {
                "pid": _dead_pid(),
                "started": "2026-10-13T09:00:00+02:00",
                "activity": "frequency sweep running (200 points)",
                "noted": "2026-10-13T09:01:00+02:00",
            }
        )
    )
    first = SessionMarker(tmp_path).start()
    second = SessionMarker(tmp_path).start()
    assert len(first) == 1
    assert "frequency sweep running (200 points)" in describe(first[0])
    assert not left_behind.exists() and second == []  # reported once, not on every start

    (tmp_path / "session-2.json").write_text("{not json")  # unreadable: reported without details
    unclean = SessionMarker(tmp_path).start()
    assert unclean == [{}] and describe(unclean[0]) == "no details were recorded"

    other_instance = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:  # a marker of a process that is still running is not an unclean exit
        theirs = tmp_path / f"session-{other_instance.pid}.json"
        theirs.write_text(json.dumps({"pid": other_instance.pid, "activity": "idle"}))
        assert SessionMarker(tmp_path).start() == [] and theirs.exists()
    finally:
        other_instance.kill()
        other_instance.wait()


# -- crash hooks and faulthandler (in a subprocess: they are process-wide) ---------------------

_CRASH_SCRIPT = """
import faulthandler, logging, sys, threading
from pathlib import Path
from iyzee.tui import diagnostics

directory, mode = Path(sys.argv[1]), sys.argv[2]
logger = logging.getLogger("iyzee")
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.FileHandler(directory / "app.log"))
diagnostics.install(directory)

if mode == "thread":
    worker = threading.Thread(target=lambda: 1 / 0, name="sweep-worker")
    worker.start()
    worker.join()
elif mode == "main":
    raise RuntimeError("main thread boom")
elif mode == "segfault":
    faulthandler._sigsegv()
"""


def _crash(tmp_path: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own interpreter and a fixed script
        [sys.executable, "-c", _CRASH_SCRIPT, str(tmp_path), mode],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT,
    )


def test_crashes_leave_evidence_a_thread_exception_a_main_thread_exit_and_a_hard_crash(
    tmp_path: Path,
) -> None:
    (tmp_path / "t").mkdir()
    thread = _crash(tmp_path / "t", "thread")
    assert thread.returncode == 0  # a thread dying does not end the process
    log = (tmp_path / "t" / "app.log").read_text()
    assert "uncaught exception in thread 'sweep-worker'" in log and "ZeroDivisionError" in log

    (tmp_path / "m").mkdir()
    assert _crash(tmp_path / "m", "main").returncode == 1
    log = (tmp_path / "m" / "app.log").read_text()
    assert "uncaught exception, the process is ending" in log
    assert "RuntimeError: main thread boom" in log

    (tmp_path / "s").mkdir()
    assert _crash(tmp_path / "s", "segfault").returncode == -signal.SIGSEGV
    dump = (
        tmp_path / "s" / "faulthandler.log"
    ).read_text()  # every thread's stack, on a hard crash
    assert "Fatal Python error: Segmentation fault" in dump
    assert "Current thread" in dump and "=== iyzee pid " in dump


# -- the app: marker lifecycle and signals -------------------------------------------------------


@async_test
async def test_the_app_keeps_a_marker_while_running_and_removes_it_on_exit(tmp_path: Path) -> None:
    app = app_mod.IyzeeApp(session_dir=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.session_marker is not None and app.session_marker.path.exists()
        app.session_note("frequency sweep running (50 points)")
        assert "frequency sweep running" in app.session_marker.path.read_text()
    assert not list(tmp_path.glob("session-*.json"))

    bare = app_mod.IyzeeApp()  # without a session dir nothing is written or hooked
    async with bare.run_test() as pilot:
        await pilot.pause()
        bare.session_note("ignored")  # a no-op, not an error
    assert bare.session_marker is None


@async_test
async def test_starting_after_a_crash_says_so_and_what_was_running(tmp_path: Path) -> None:
    (tmp_path / "session-1.json").write_text(
        json.dumps({"pid": _dead_pid(), "activity": "frequency sweep running (200 points)"})
    )
    app = app_mod.IyzeeApp(session_dir=tmp_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        said = " ".join(notifications(app))

    assert "did not end cleanly" in said
    assert "frequency sweep running (200 points)" in said
    assert "faulthandler.log" in said


@pytest.mark.parametrize("name", ["SIGTERM"])
@async_test
async def test_a_termination_signal_is_logged_and_shuts_the_app_down(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, name: str
) -> None:
    app = app_mod.IyzeeApp(session_dir=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_signal(name)
        await pilot.pause(0.5)

    assert any(f"received {name}" in record.getMessage() for record in caplog.records)
    assert not list(tmp_path.glob("session-*.json")), "a signalled exit is still a clean one"


_APP_SCRIPT = """
import sys
from iyzee.tui.app import IyzeeApp

app = IyzeeApp(session_dir=sys.argv[1], handle_signals=True)
app.run(headless=True)
print("EXITED", app.return_code, flush=True)
"""


def test_a_real_sigterm_to_a_real_app_ends_it_cleanly_and_leaves_a_log_line(
    tmp_path: Path,
) -> None:
    env = {**os.environ, "IYZEE_DATA_DIR": str(tmp_path / "data")}
    sessions = tmp_path / "sessions"
    process = subprocess.Popen(  # noqa: S603 - our own interpreter and a fixed script
        [sys.executable, "-c", _APP_SCRIPT, str(sessions)],
        env=env,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not list(sessions.glob("session-*.json")):  # the app is up once it has a marker
            assert process.poll() is None, process.communicate()
            assert time.monotonic() < deadline, "the app never started"
            time.sleep(0.1)
        time.sleep(0.5)  # let the signal handlers go in right after the marker is written

        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 0, (out, err)
    assert "EXITED" in out
    assert not list(sessions.glob("session-*.json")), "SIGTERM is handled, so it is a clean exit"
    logs = "".join(p.read_text() for p in (tmp_path / "data" / "logs").glob("*.log*"))
    assert "received SIGTERM" in logs
