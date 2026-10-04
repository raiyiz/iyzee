from typing import Any

import pytest
import requests

import iyzee.devices.wavemeter as wavemeter_readout


def response(body: str, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("ascii")
    result.encoding = "ascii"
    return result


def test_read_frequency_reads_selected_channel_with_bounded_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return response("377.123456")

    monkeypatch.setattr(wavemeter_readout.requests, "get", get)

    assert wavemeter_readout.read_frequency(4) == pytest.approx(377.123456)
    assert seen == {
        "url": f"http://{wavemeter_readout.IP.WAVEMETER}:{wavemeter_readout.WAVEMETER_PORT}/api/4/",
        "timeout": wavemeter_readout.READ_TIMEOUT_S,
    }


def test_read_frequency_uses_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return response("377.123456")

    monkeypatch.setattr(wavemeter_readout.requests, "get", get)

    assert wavemeter_readout.read_frequency() == pytest.approx(377.123456)
    assert seen["url"].endswith(f"/api/{wavemeter_readout.DEFAULT_CHANNEL}/")


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("timed out"),
        requests.ConnectionError("connection reset"),
        OSError("connection refused"),
    ],
)
def test_read_frequency_translates_request_failures(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(wavemeter_readout.requests, "get", fail)

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="channel 1"):
        wavemeter_readout.read_frequency(1)


def test_read_frequency_translates_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wavemeter_readout.requests,
        "get",
        lambda *args, **kwargs: response("server error", status=500),
    )

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="channel 1"):
        wavemeter_readout.read_frequency(1)


def test_read_frequency_rejects_invalid_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wavemeter_readout.requests,
        "get",
        lambda *args, **kwargs: response("not-a-frequency"),
    )

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="channel 1"):
        wavemeter_readout.read_frequency(1)


def test_single_readout_applies_reference_to_one_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wavemeter_readout,
        "read_frequency",
        lambda channel=wavemeter_readout.DEFAULT_CHANNEL: 377.123456,
    )

    assert wavemeter_readout.single_readout(1, reference_f=377.0, printing=False) == pytest.approx(
        0.123456
    )


def test_set_pid_setpoint_posts_a_form_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def get(*args, **kwargs):
        raise AssertionError("PID setpoint must not use GET")

    def post(url, data=None, timeout=None):
        seen.update(url=url, data=data, timeout=timeout)
        return response("")

    monkeypatch.setattr(wavemeter_readout.requests, "get", get)
    monkeypatch.setattr(wavemeter_readout.requests, "post", post)

    wavemeter_readout.set_pid_setpoint(377.1052, 4)

    assert seen == {
        "url": f"http://{wavemeter_readout.IP.WAVEMETER}:{wavemeter_readout.WAVEMETER_PORT}/api/set_pid/",
        "data": b"freq_thz=377.1052&channel=4",
        "timeout": wavemeter_readout.SETPOINT_TIMEOUT_S,
    }


def test_set_pid_setpoint_uses_default_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    monkeypatch.setattr(
        wavemeter_readout.requests,
        "post",
        lambda url, data=None, timeout=None: (
            seen.update(url=url, data=data, timeout=timeout) or response("")
        ),
    )

    wavemeter_readout.set_pid_setpoint(377.1052)

    assert seen["data"] == b"freq_thz=377.1052&channel=4"
    assert seen["timeout"] == wavemeter_readout.SETPOINT_TIMEOUT_S


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("timed out"),
        requests.ConnectionError("unreachable"),
        OSError("connection refused"),
    ],
)
def test_set_pid_setpoint_translates_request_failures(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(wavemeter_readout.requests, "post", fail)

    with pytest.raises(
        wavemeter_readout.WavemeterReadoutError,
        match=r"channel 4 to 377\.1 THz",
    ):
        wavemeter_readout.set_pid_setpoint(377.1, 4)


def test_set_pid_setpoint_translates_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wavemeter_readout.requests,
        "post",
        lambda *args, **kwargs: response("server error", status=500),
    )

    with pytest.raises(
        wavemeter_readout.WavemeterReadoutError,
        match=r"channel 4 to 377\.1 THz",
    ):
        wavemeter_readout.set_pid_setpoint(377.1, 4)


def test_client_starts_without_a_last_seen_timestamp_and_records_successful_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = wavemeter_readout.Wavemeter()

    assert client.last_seen is None

    monkeypatch.setattr(
        wavemeter_readout.requests,
        "get",
        lambda *args, **kwargs: response("377.123456"),
    )

    assert client.read_frequency(4) == pytest.approx(377.123456)
    first_seen = client.last_seen

    assert first_seen is not None
    assert first_seen.tzinfo is wavemeter_readout.timezone.utc

    def fail(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(wavemeter_readout.requests, "get", fail)

    with pytest.raises(wavemeter_readout.WavemeterReadoutError):
        client.read_frequency(4)

    assert client.last_seen == first_seen
