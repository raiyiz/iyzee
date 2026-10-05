from typing import Any

import pytest
import requests

import iyzee.devices.wavemeter as wavemeter


def response(body: str, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("ascii")
    result.encoding = "ascii"
    return result


def test_read_frequency_uses_the_requested_and_default_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def get(url: str, **kwargs: Any) -> requests.Response:
        seen.append((url, kwargs))
        return response("377.123456")

    monkeypatch.setattr(wavemeter.requests, "get", get)

    assert wavemeter.read_frequency(7) == pytest.approx(377.123456)
    assert wavemeter.read_frequency() == pytest.approx(377.123456)
    assert seen == [
        (
            f"http://{wavemeter.IP.WAVEMETER}:{wavemeter.WAVEMETER_PORT}/api/7/",
            {"timeout": wavemeter.READ_TIMEOUT_S},
        ),
        (
            (
                f"http://{wavemeter.IP.WAVEMETER}:{wavemeter.WAVEMETER_PORT}"
                f"/api/{wavemeter.DEFAULT_CHANNEL}/"
            ),
            {"timeout": wavemeter.READ_TIMEOUT_S},
        ),
    ]


@pytest.mark.parametrize(
    "failure, expected",
    [
        (requests.Timeout("timed out"), requests.Timeout),
        (requests.ConnectionError("connection reset"), requests.ConnectionError),
        (OSError("connection refused"), OSError),
        ("not-a-frequency", ValueError),
    ],
)
def test_read_frequency_exposes_the_underlying_failure(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException | str, expected: type[BaseException]
) -> None:
    if isinstance(failure, str):
        monkeypatch.setattr(
            wavemeter.requests,
            "get",
            lambda *args, **kwargs: response(failure),
        )
    else:
        def get(*args: Any, **kwargs: Any) -> requests.Response:
            raise failure

        monkeypatch.setattr(wavemeter.requests, "get", get)

    with pytest.raises(expected) as caught:
        wavemeter.read_frequency(1)

    if isinstance(failure, BaseException):
        assert caught.value is failure


def test_read_frequency_exposes_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wavemeter.requests,
        "get",
        lambda *args, **kwargs: response("server error", status=500),
    )

    with pytest.raises(requests.HTTPError):
        wavemeter.read_frequency(1)


def test_single_readout_subtracts_the_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wavemeter, "read_frequency", lambda channel=4: 377.123456)

    assert wavemeter.single_readout(1, reference_f=377.0, printing=False) == pytest.approx(0.123456)


def test_set_pid_setpoint_posts_the_expected_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def post(
        url: str, data: bytes | None = None, timeout: float | None = None
    ) -> requests.Response:
        seen.update(url=url, data=data, timeout=timeout)
        return response("")

    monkeypatch.setattr(wavemeter.requests, "post", post)

    wavemeter.set_pid_setpoint(377.1052, 4)

    assert seen == {
        "url": f"http://{wavemeter.IP.WAVEMETER}:{wavemeter.WAVEMETER_PORT}/api/set_pid/",
        "data": b"freq_thz=377.1052&channel=4",
        "timeout": wavemeter.SETPOINT_TIMEOUT_S,
    }


def test_client_last_seen_changes_only_after_a_successful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = wavemeter.Wavemeter()
    assert client.last_seen is None

    monkeypatch.setattr(
        wavemeter.requests, "get", lambda *args, **kwargs: response("377.123456")
    )
    assert client.read_frequency(4) == pytest.approx(377.123456)
    seen = client.last_seen
    assert seen is not None and seen.tzinfo is wavemeter.timezone.utc

    failure = requests.Timeout("timed out")
    def fail(*args: Any, **kwargs: Any) -> requests.Response:
        raise failure

    monkeypatch.setattr(wavemeter.requests, "get", fail)
    with pytest.raises(requests.Timeout) as caught:
        client.read_frequency(4)
    assert caught.value is failure
    assert client.last_seen == seen
