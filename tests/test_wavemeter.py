from typing import Any

import pytest
import requests

import iyzee.devices.wavemeter as wavemeter
from iyzee.config import IP, address
from iyzee.devices.wavemeter import Wavemeter


def response(body: str, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.reason = "Server Error" if status >= 400 else "OK"
    result._content = body.encode("ascii")
    result.encoding = "ascii"
    return result


def base_url() -> str:
    return f"http://{address(IP.WAVEMETER)}:{wavemeter.WAVEMETER_PORT}/api/"


def raising(failure: BaseException):
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
        (f"{base_url()}frequency/7/", timeout),
        (f"{base_url()}frequency/{wavemeter.DEFAULT_CHANNEL}/", timeout),
    ]


def test_host_follows_the_configured_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IYZEE_WAVEMETER_IP", "192.0.2.7")
    assert Wavemeter().base_url == f"http://192.0.2.7:{wavemeter.WAVEMETER_PORT}/api/"
    assert Wavemeter(host="198.51.100.1").base_url.startswith("http://198.51.100.1:")


@pytest.mark.parametrize(
    "failure",
    [requests.Timeout("timed out"), requests.ConnectionError("reset"), OSError("refused")],
)
@pytest.mark.parametrize("call", ["read", "set"])
def test_request_failures_are_raised_unwrapped(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException, call: str
) -> None:
    verb = "get" if call == "read" else "post"
    monkeypatch.setattr(wavemeter.requests, verb, raising(failure))
    client = Wavemeter()

    with pytest.raises(type(failure)) as caught:
        client.read_frequency(1) if call == "read" else client.set_pid_setpoint(377.1, 4)
    assert caught.value is failure


@pytest.mark.parametrize("call", ["read", "set"])
def test_http_errors_carry_status_url_and_body(monkeypatch: pytest.MonkeyPatch, call: str) -> None:
    verb = "get" if call == "read" else "post"
    monkeypatch.setattr(
        wavemeter.requests, verb, lambda *args, **kwargs: response("lock unit offline", 500)
    )
    client = Wavemeter()

    with pytest.raises(
        requests.HTTPError, match=r"500 Server Error for http://.*'lock unit offline'"
    ):
        client.read_frequency(1) if call == "read" else client.set_pid_setpoint(377.1, 4)


def test_unparseable_reading_raises_value_error_quoting_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wavemeter.requests, "get", lambda *a, **k: response("not-a-frequency"))

    with pytest.raises(ValueError, match="not-a-frequency"):
        Wavemeter().read_frequency(1)


def test_set_pid_setpoint_posts_the_expected_form(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def get(*args: Any, **kwargs: Any) -> requests.Response:
        raise AssertionError("PID setpoint must not use GET")

    def post(url: str, data: bytes | None = None, timeout: float | None = None) -> Any:
        seen.append({"url": url, "data": data, "timeout": timeout})
        return response("")

    monkeypatch.setattr(wavemeter.requests, "get", get)
    monkeypatch.setattr(wavemeter.requests, "post", post)
    client = Wavemeter()

    client.set_pid_setpoint(377.1052, 4)
    client.set_pid_setpoint(377.1052)  # default channel

    expected = {
        "url": f"{base_url()}set_pid/",
        "data": {"freq_thz": 377.1052, "channel": 4},
        "timeout": wavemeter.SETPOINT_TIMEOUT_S,
    }
    assert seen == [expected, expected]


def test_last_seen_changes_only_after_a_successful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Wavemeter()
    assert client.last_seen is None

    monkeypatch.setattr(wavemeter.requests, "get", lambda *a, **k: response("377.123456"))
    assert client.read_frequency(4) == pytest.approx(377.123456)
    seen = client.last_seen
    assert seen is not None and seen.tzinfo is wavemeter.timezone.utc

    monkeypatch.setattr(wavemeter.requests, "get", raising(requests.Timeout("timed out")))
    with pytest.raises(requests.Timeout):
        client.read_frequency(4)
    assert client.last_seen == seen
