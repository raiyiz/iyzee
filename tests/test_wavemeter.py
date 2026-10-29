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
