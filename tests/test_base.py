import pytest
import pyvisa

from iyzee import IP, BaseDevice


class FakeInstrument:
    def __init__(self):
        self.close_calls = 0
        self.timeout = None

    def close(self):
        self.close_calls += 1


class FakeResourceManager:
    def __init__(self):
        self.open_calls = 0
        self.instrument = FakeInstrument()

    def open_resource(self, address):
        self.open_calls += 1
        return self.instrument


def test_base_device_opens_lazily_once_closes_idempotently_and_reconnects():
    resource_manager = FakeResourceManager()
    device = BaseDevice(ip=IP.POWER_SUPPLY, resource_manager=resource_manager)  # injected
    assert device.rm is resource_manager
    assert resource_manager.open_calls == 0 and device.instrument is None

    device.close()  # closing a device that was never opened does nothing
    device.close()
    assert resource_manager.instrument.close_calls == 0

    device.connect()
    device.connect()  # and connecting twice opens once
    assert resource_manager.open_calls == 1
    assert device.instrument is resource_manager.instrument

    device.close()
    assert resource_manager.instrument.close_calls == 1
    device.connect()  # reconnecting opens a fresh link
    assert resource_manager.open_calls == 2


def test_base_device_as_a_context_manager_opens_once_and_closes_even_when_the_block_raises(
    monkeypatch,
):
    resource_manager = FakeResourceManager()
    selected_backends = []
    monkeypatch.setattr(
        pyvisa,
        "ResourceManager",
        lambda backend: selected_backends.append(backend) or resource_manager,
    )
    device = BaseDevice(ip=IP.POWER_SUPPLY)
    assert selected_backends == ["@py"]
    assert resource_manager.open_calls == 0

    with device as managed:
        assert managed is device
        assert resource_manager.open_calls == 1
        device.connect()
        assert resource_manager.open_calls == 1
    assert resource_manager.instrument.close_calls == 1

    with pytest.raises(RuntimeError), device:
        raise RuntimeError("acquisition failed")
    assert device.instrument is None
    assert resource_manager.instrument.close_calls == 2
