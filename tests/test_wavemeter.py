import typing
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


def raising(failure: BaseException) -> typing.Any:
    def fail(*args: Any, **kwargs: Any) -> requests.Response:
        raise failure

    return fail


def test_read_frequency_uses_the_requested_and_default_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_host_uses_the_configured_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IYZEE_WAVEMETER_IP", "192.0.2.7")
    client = Wavemeter()
    assert client.base_url == f"http://192.0.2.7:{wavemeter.WAVEMETER_PORT}/api/"

    explicit = Wavemeter(host="198.51.100.1")
    assert explicit.base_url == f"http://198.51.100.1:{wavemeter.WAVEMETER_PORT}/api/"


def test_failures_are_raised_as_they_are_never_wrapped_or_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {
        "get": lambda client: client.read_frequency(1),
        "post": lambda client: client.set_pid_setpoint(377.1, 4),
    }
    for verb, call in calls.items():
        for failure in (
            requests.Timeout("timed out"),
            requests.ConnectionError("reset"),
            OSError("refused"),
        ):
            monkeypatch.setattr(wavemeter.requests, verb, raising(failure))
            with pytest.raises(type(failure)) as caught:
                call(Wavemeter())
            assert caught.value is failure

        monkeypatch.setattr(  # an HTTP error status is raised by requests
            wavemeter.requests, verb, lambda *a, **k: response("lock unit offline", status=500)
        )
        with pytest.raises(requests.HTTPError, match="500"):
            call(Wavemeter())

    monkeypatch.setattr(wavemeter.requests, "get", lambda *a, **k: response("not-a-frequency"))
    with pytest.raises(ValueError, match="not-a-frequency"):  # an unparseable reading
        Wavemeter().read_frequency(1)


def test_set_pid_setpoint_posts_the_expected_form(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def post(url: str, data: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        seen.append({"url": url, "data": data, "timeout": timeout})
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
    assert seen == [
        expected,
        {
            **expected,
            "data": {"freq_thz": 377.1052, "channel": wavemeter.DEFAULT_CHANNEL},
        },
    ]
