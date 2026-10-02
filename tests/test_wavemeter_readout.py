from email.message import Message

import pytest
import requests

import iyzee.wavemeter_readout as wavemeter_readout


class FakeResponse:
    def __init__(self, value: str):
        self.value = value

    def read(self):
        return self.value.encode("ascii")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_single_readout_returns_measured_frequency(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.requests,
        "get",
        lambda *args, **kwargs: FakeResponse("377.123456"),
    )

    assert wavemeter_readout.single_readout(1, reference_f=377.0, printing=False) == pytest.approx(
        0.123456
    )


def test_single_readout_raises_on_communication_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(wavemeter_readout.requests, "get", fail)

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="channel 1"):
        wavemeter_readout.single_readout(1, printing=False)


def test_single_readout_raises_on_invalid_measurement(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.requests,
        "get",
        lambda *args, **kwargs: FakeResponse("not-a-frequency"),
    )

    with pytest.raises(wavemeter_readout.WavemeterReadoutError):
        wavemeter_readout.single_readout(1, printing=False)


def test_monitoring_frequencies_matches_channels_by_position(monkeypatch, capsys):
    # Non-contiguous, non-zero-based channel numbers: freqs[c] would previously
    # index out of range / pick the wrong reading for channels like these.
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

    def fake_urlopen(url, data=None, timeout=None):
        seen.update(url=url, data=data, timeout=timeout)
        return FakeResponse("")

    monkeypatch.setattr(wavemeter_readout.requests, "get", fake_urlopen)

    with caplog.at_level("INFO", logger="iyzee.wavemeter"):
        wavemeter_readout.set_pid_setpoint(377.1052, 4)

    assert seen["url"].endswith(":8000/api/set_pid/")
    assert seen["data"] == b"freq_thz=377.1052&channel=4"
    assert seen["timeout"] == wavemeter_readout.SETPOINT_TIMEOUT_S  # never unbounded
    assert "channel 4" in caplog.text
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        # urllib.error.URLError("unreachable"),
        # urllib.error.HTTPError("http://wm/api/set_pid/", 500, "boom", Message(), None),
        requests.ConnectionError,
        requests.HTTPError("http://wm/api/set_pid/", 500, "boom", Message(), None),
    ],
)
def test_set_pid_setpoint_failure_is_a_wavemeter_error_naming_channel_and_value(
    monkeypatch, failure
):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(wavemeter_readout.requests, "get", fail)

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match=r"channel 4 to 377\.1 THz"):
        wavemeter_readout.set_pid_setpoint(377.1, 4)


def test_wavemeter_client_reads_selected_channel(monkeypatch):
    calls = []

    def fake_request(path, *, timeout, data=None):
        calls.append((path, timeout, data))
        return "377.123456"

    monkeypatch.setattr(wavemeter_readout, "_request", fake_request)
    wavemeter = wavemeter_readout.Wavemeter(channel=4)

    assert wavemeter.read_frequency() == pytest.approx(377.123456)
    assert calls == [("4/", wavemeter_readout.READ_TIMEOUT_S, None)]


def test_wavemeter_client_sets_pid_setpoint(monkeypatch):
    calls = []

    def fake_request(path, *, timeout, data=None):
        calls.append((path, timeout, data))
        return ""

    monkeypatch.setattr(wavemeter_readout, "_request", fake_request)
    wavemeter = wavemeter_readout.Wavemeter(channel=4)

    wavemeter.set_pid_setpoint(377.105, channel=5)

    assert calls == [
        ("set_pid/", wavemeter_readout.SETPOINT_TIMEOUT_S, b"freq_thz=377.105&channel=5")
    ]
