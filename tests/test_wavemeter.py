from typing import Any

import pytest
import requests

import iyzee.devices.wavemeter as wavemeter
from iyzee.devices.wavemeter import Wavemeter


def response(body: str, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.reason = "Server Error" if status >= 400 else "OK"
    result._content = body.encode("ascii")
    result.encoding = "ascii"
    return result


def raising(failure: BaseException):
    def fail(*args: Any, **kwargs: Any) -> requests.Response:
        raise failure

    return fail


def test_the_wavemeter_reads_the_right_channel_at_the_configured_host_and_posts_pid_setpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # -- read frequency uses the requested and default channel
    seen: list[tuple[str, dict[str, Any]]] = []

    def get(url: str, **kwargs: Any) -> requests.Response:
        seen.append((url, kwargs))
        return response("377.123456")

    monkeypatch.setattr(wavemeter.requests, "get", get)
    client = Wavemeter()

    assert client.read_frequency(7) == pytest.approx(377.123456)
    assert client.read_frequency() == pytest.approx(377.123456)
    timeout = {"timeout": wavemeter.READ_TIMEOUT_S}
    assert seen == [
        (f"{client.base_url}frequency/7/", timeout),
        (f"{client.base_url}frequency/{wavemeter.DEFAULT_CHANNEL}/", timeout),
    ]

    # -- set pid setpoint posts the expected form
    posted: list[dict[str, Any]] = []

    def post(url: str, data: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        posted.append({"url": url, "data": data, "timeout": timeout})
        return response("")

    monkeypatch.setattr(wavemeter.requests, "post", post)
    client = Wavemeter()

    client.set_pid_setpoint(377.1052, 4)
    client.set_pid_setpoint(377.1052)

    expected = {
        "url": f"{client.base_url}set_pid/",
        "data": {"freq_thz": 377.1052, "channel": 4},
        "timeout": wavemeter.SETPOINT_TIMEOUT_S,
    }
    assert posted == [
        expected,
        {
            **expected,
            "data": {"freq_thz": 377.1052, "channel": wavemeter.DEFAULT_CHANNEL},
        },
    ]


# -- a flaky server, and answers that are not frequencies --------------------------------------


@pytest.fixture
def no_delay(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(wavemeter.time, "sleep", delays.append)
    return delays


def test_a_transient_failure_is_retried_and_a_dead_server_is_reported_after_the_attempts(
    monkeypatch: pytest.MonkeyPatch, no_delay: list[float]
) -> None:
    outcomes: list[Any] = [requests.ConnectionError("reset"), requests.Timeout("slow"), "377.5"]

    def get(url: str, **kwargs: Any) -> requests.Response:
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return response(outcome)

    monkeypatch.setattr(wavemeter.requests, "get", get)
    assert Wavemeter().read_frequency(4) == pytest.approx(377.5)
    assert len(no_delay) == 2  # waited between the attempts

    calls: list[int] = []

    def dead(*a: Any, **k: Any) -> requests.Response:
        calls.append(1)
        raise requests.Timeout("dead")

    monkeypatch.setattr(wavemeter.requests, "get", dead)
    with pytest.raises(wavemeter.WavemeterError, match="read channel 4"):
        Wavemeter().read_frequency(4)
    assert len(calls) == wavemeter.ATTEMPTS
    assert issubclass(wavemeter.WavemeterError, OSError)  # so the runner retries the point


def test_a_server_that_refuses_is_asked_once_but_one_that_fails_is_asked_again(
    monkeypatch: pytest.MonkeyPatch, no_delay: list[float]
) -> None:
    calls: list[int] = []

    def refuse(*a: Any, **k: Any) -> requests.Response:
        calls.append(1)
        return response("no", status=404)

    monkeypatch.setattr(wavemeter.requests, "post", refuse)
    with pytest.raises(wavemeter.WavemeterError):
        Wavemeter().set_pid_setpoint(377.1, 4)
    assert len(calls) == 1

    calls.clear()

    def failing(*a: Any, **k: Any) -> requests.Response:
        calls.append(1)
        return response("", status=503)

    monkeypatch.setattr(wavemeter.requests, "post", failing)
    with pytest.raises(wavemeter.WavemeterError):
        Wavemeter().set_pid_setpoint(377.1, 4)
    assert len(calls) == wavemeter.ATTEMPTS


@pytest.mark.parametrize("text", ["0", "-3.0", "-1", "nan", "inf", "", "no signal"])
def test_a_wavemeter_status_code_is_not_a_frequency(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    monkeypatch.setattr(wavemeter.requests, "get", lambda *a, **k: response(text))
    with pytest.raises(wavemeter.WavemeterError):
        Wavemeter().read_frequency(4)
