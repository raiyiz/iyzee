from typing import Any

import pytest
import requests

import iyzee.devices.wavemeter as wavemeter


def response(body: str, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.reason = "Server Error" if status >= 400 else "OK"
    result._content = body.encode("ascii")
    result.encoding = "ascii"
    return result


def test_client_uses_the_default_wavemeter_address() -> None:
    client = wavemeter.Wavemeter()
    assert client.host == wavemeter.address(wavemeter.IP.WAVEMETER)
    assert client.port == wavemeter.WAVEMETER_PORT


def test_read_frequency_uses_selected_channel_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def get(url: str, **kwargs: Any) -> requests.Response:
        seen.append((url, kwargs))
        return response("377.123456")

    monkeypatch.setattr(wavemeter.requests, "get", get)

    client = wavemeter.Wavemeter(host="127.0.0.1", port=8001)
    assert client.read_frequency(4) == pytest.approx(377.123456)
    assert seen == {
        "url": "http://127.0.0.1:8001/api/4/",
        "timeout": wavemeter.READ_TIMEOUT_S,
    }


def test_read_frequency_uses_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return response("377.123456")

    monkeypatch.setattr(wavemeter.requests, "get", get)

    assert wavemeter.Wavemeter().read_frequency() == pytest.approx(377.123456)
    assert seen["url"].endswith(f"/api/{wavemeter.DEFAULT_CHANNEL}/")


@pytest.mark.parametrize(
    "failure",
    [requests.Timeout("timed out"), requests.ConnectionError("reset"), OSError("refused")],
)
def test_read_frequency_propagates_request_failures(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    verb = "get" if call == "read" else "post"
    monkeypatch.setattr(wavemeter.requests, verb, raising(failure))
    client = Wavemeter()

    monkeypatch.setattr(wavemeter.requests, "get", fail)

    with pytest.raises(type(failure), match=str(failure)):
        wavemeter.Wavemeter().read_frequency(1)


def test_read_frequency_propagates_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wavemeter.requests,
        "get",
        lambda *args, **kwargs: response("server error", status=500),
    )
    client = Wavemeter()

    with pytest.raises(requests.HTTPError, match="500"):
        wavemeter.Wavemeter().read_frequency(1)


def test_read_frequency_propagates_invalid_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wavemeter.requests,
        "get",
        lambda *args, **kwargs: response("not-a-frequency"),
    )

    with pytest.raises(ValueError, match="could not convert"):
        wavemeter.Wavemeter().read_frequency(1)


def test_set_pid_setpoint_posts_the_form_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def post(url, data=None, timeout=None):
        seen.update(url=url, data=data, timeout=timeout)
        return response("")

    monkeypatch.setattr(wavemeter.requests, "post", post)

    wavemeter.Wavemeter(host="127.0.0.1", port=8001).set_pid_setpoint(377.1052, 4)

    assert seen == {
        "url": "http://127.0.0.1:8001/api/set_pid/",
        "data": {"freq_thz": 377.1052, "channel": 4},
        "timeout": wavemeter.SETPOINT_TIMEOUT_S,
    }
    assert seen == [expected, expected]


def test_set_pid_setpoint_uses_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def post(url, data=None, timeout=None):
        seen.update(url=url, data=data, timeout=timeout)
        return response("")

    monkeypatch.setattr(wavemeter.requests, "post", post)

    wavemeter.Wavemeter().set_pid_setpoint(377.1052)

    assert seen["data"] == {
        "freq_thz": 377.1052,
        "channel": wavemeter.DEFAULT_CHANNEL,
    }
    assert seen["timeout"] == wavemeter.SETPOINT_TIMEOUT_S


def test_set_pid_setpoint_propagates_request_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = requests.Timeout("timed out")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(wavemeter.requests, "post", fail)

    with pytest.raises(requests.Timeout, match="timed out"):
        wavemeter.Wavemeter().set_pid_setpoint(377.1)


def test_set_pid_setpoint_propagates_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wavemeter.requests,
        "post",
        lambda *args, **kwargs: response("server error", status=500),
    )

    with pytest.raises(requests.HTTPError, match="500"):
        wavemeter.Wavemeter().set_pid_setpoint(377.1)


def test_single_readout_uses_the_plain_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wavemeter.Wavemeter,
        "read_frequency",
        lambda self, channel=wavemeter.DEFAULT_CHANNEL: 377.123456,
    )

    assert wavemeter.single_readout(1, reference_f=377.0, printing=False) == pytest.approx(0.123456)
