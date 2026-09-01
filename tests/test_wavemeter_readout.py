import wavemeter_readout
import pytest


class FakeResponse:
    def __init__(self, value: str):
        self.value = value

    def read(self):
        return self.value.encode("ascii")


def test_single_readout_returns_measured_frequency(monkeypatch):
    monkeypatch.setattr(
        wavemeter_readout.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse("377.123456"),
    )

    assert wavemeter_readout.single_readout(
        1, reference_f=377.0, printing=False
    ) == pytest.approx(0.123456)


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
