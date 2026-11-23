"""``@traced``: a failed or stuck call says what it was doing."""

from __future__ import annotations

import logging
import time

import pytest

from iyzee.trace import traced


def test_a_failure_carries_the_last_events_and_the_logger_is_left_as_found() -> None:
    log = logging.getLogger("iyzee.experiment")
    before = (logging.getLogger("iyzee").level, list(logging.getLogger("iyzee").handlers))

    @traced()
    def run() -> None:
        log.info("run ab12: acquiring the squeezing trace")
        try:
            raise TimeoutError("no answer")
        except TimeoutError:
            log.exception("run ab12: point 3 failed")
        raise TimeoutError("sweep lagged")

    with pytest.raises(TimeoutError) as caught:
        run()
    note = "\n".join(caught.value.__notes__)
    assert "acquiring the squeezing trace" in note
    assert "point 3 failed  [TimeoutError: no answer]" in note
    assert (logging.getLogger("iyzee").level, logging.getLogger("iyzee").handlers) == before


def test_a_call_that_goes_quiet_names_the_line_it_is_blocked_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def blocked_in_a_read() -> None:
        time.sleep(0.6)

    with traced(stall_s=0.2) as trace:
        blocked_in_a_read()
    assert "quiet for" in trace.timeline()
    assert "blocked_in_a_read" in capsys.readouterr().err
