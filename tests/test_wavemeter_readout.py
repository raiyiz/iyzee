import pytest
import requests

import iyzee.wavemeter_readout as wavemeter_readout


def response(body: str, status: int = 200) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("ascii")
    result.encoding = "ascii"
    return result


def test_read_frequency_reads_selected_channel_and_timeout(monkeypatch):
    seen = {}

    def get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return response("377.123456")

    monkeypatch.setattr(wavemeter_readout.requests, "get", get)

    assert wavemeter_readout.read_frequency(4) == pytest.approx(377.123456)
    assert seen == {
        "url": f"http://{wavemeter_readout.IP.WAVEMETER}:{wavemeter_readout.WAVEMETER_PORT}/api/4/",
        "timeout": wavemeter_readout.READ_TIMEOUT_S,
    }


def test_read_frequency_uses_the_default_channel(monkeypatch):
    seen = {}

    def get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return response("377.123456")

    monkeypatch.setattr(wavemeter_readout.requests, "get", get)

    assert wavemeter_readout.read_frequency() == pytest.approx(377.123456)
    assert seen["url"].endswith(f"/api/{wavemeter_readout.DEFAULT_CHANNEL}/")


@pytest.mark.parametrize(
    "failure",
    [
        OSError("connection refused"),
        requests.ConnectionError("connection reset"),
    ],
)
def test_read_frequency_translates_transport_failures(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(wavemeter_readout.requests, "get", fail)

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="channel 1"):
        wavemeter_readout.read_frequency(1)


def test_read_frequency_translates_http_failure(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.requests,
        "get",
        lambda *args, **kwargs: response("server error", status=500),
    )

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="channel 1"):
        wavemeter_readout.read_frequency(1)


def test_read_frequency_rejects_invalid_measurement(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.requests,
        "get",
        lambda *args, **kwargs: response("not-a-frequency"),
    )

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="channel 1"):
        wavemeter_readout.read_frequency(1)


def test_single_readout_applies_reference_without_duplicating_http_logic(monkeypatch):
    monkeypatch.setattr(wavemeter_readout, "read_frequency", lambda channel=0: 377.123456)

    assert wavemeter_readout.single_readout(1, reference_f=377.0, printing=False) == pytest.approx(
        0.123456
    )


def test_monitoring_frequencies_matches_channels_by_position(monkeypatch, capsys):
    readings = {2: 377.107385690, 5: 384.230406373}
    monkeypatch.setattr(
        wavemeter_readout,
        "single_readout",
        lambda channel, reference_f=0, printing=False: readings[channel],
    )

    wavemeter_readout.monitoring_frequencies([2, 5], two_photon=False)

    printed = capsys.readouterr().out
    assert "ch2" in printed
    assert "ch5" in printed


def test_set_pid_setpoint_posts_a_bounded_request_and_logs_instead_of_printing(
    monkeypatch, capsys, caplog
):
    seen = {}

    def get(url, data=None, timeout=None):
        seen.update(url=url, data=data, timeout=timeout)
        return response("")

    monkeypatch.setattr(wavemeter_readout.requests, "get", get)

    with caplog.at_level("INFO", logger="iyzee.wavemeter"):
        wavemeter_readout.set_pid_setpoint(377.1052, 4)

    assert seen["url"].endswith(f":{wavemeter_readout.WAVEMETER_PORT}/api/set_pid/")
    assert seen["data"] == b"freq_thz=377.1052&channel=4"
    assert seen["timeout"] == wavemeter_readout.SETPOINT_TIMEOUT_S
    assert "channel 4" in caplog.text
    assert capsys.readouterr().out == ""


def test_set_pid_setpoint_uses_the_default_channel(monkeypatch):
    seen = {}

    def get(url, data=None, timeout=None):
        seen.update(url=url, data=data, timeout=timeout)
        return response("")

    monkeypatch.setattr(wavemeter_readout.requests, "get", get)

    wavemeter_readout.set_pid_setpoint(377.1052)

    assert seen["data"] == b"freq_thz=377.1052&channel=0"


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        requests.ConnectionError("unreachable"),
    ],
)
def test_set_pid_setpoint_translates_transport_failures(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(wavemeter_readout.requests, "get", fail)

    with pytest.raises(
        wavemeter_readout.WavemeterReadoutError,
        match=r"channel 4 to 377\.1 THz",
    ):
        wavemeter_readout.set_pid_setpoint(377.1, 4)


def test_set_pid_setpoint_translates_http_failure(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.requests,
        "get",
        lambda *args, **kwargs: response("server error", status=500),
    )

    with pytest.raises(
        wavemeter_readout.WavemeterReadoutError,
        match=r"channel 4 to 377\.1 THz",
    ):
        wavemeter_readout.set_pid_setpoint(377.1, 4)
