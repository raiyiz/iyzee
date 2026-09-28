import pytest

import iyzee.wavemeter_readout as wavemeter_readout


class FakeResponse:
    def __init__(self, value: str):
        self.value = value
        self.closed = False

    def read(self):
        return self.value.encode("ascii")

    def close(self):
        self.closed = True


def test_single_readout_returns_measured_frequency(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse("377.123456"),
    )

    assert wavemeter_readout.single_readout(1, reference_f=377.0, printing=False) == pytest.approx(
        0.123456
    )


def test_single_readout_raises_on_communication_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(wavemeter_readout.urllib.request, "urlopen", fail)

    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="channel 1"):
        wavemeter_readout.single_readout(1, printing=False)


def test_single_readout_raises_on_invalid_measurement(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.urllib.request,
        "urlopen",
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


def test_set_pid_setpoint_uses_a_bounded_request(monkeypatch):
    calls = []
    response = FakeResponse("ok")

    def fake_urlopen(url, data=None, timeout=None):
        calls.append((url, data, timeout))
        return response

    monkeypatch.setattr(wavemeter_readout.urllib.request, "urlopen", fake_urlopen)
    wavemeter_readout.set_pid_setpoint(377.1, 4, timeout_s=2.5)

    assert calls == [(
        f"http://{wavemeter_readout.IP.WAVEMETER}:8000/api/set_pid/",
        b"freq_thz=377.1&channel=4",
        2.5,
    )]
    assert response.closed


def test_set_pid_setpoint_wraps_network_failures(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("timed out")),
    )
    with pytest.raises(wavemeter_readout.WavemeterReadoutError, match="PID channel 4"):
        wavemeter_readout.set_pid_setpoint(377.1, 4)
