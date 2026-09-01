import pyvisa

from iyzee import BaseDevice, IP


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


def test_base_device_does_not_open_more_than_once(monkeypatch):
    resource_manager = FakeResourceManager()
    monkeypatch.setattr(pyvisa, "ResourceManager", lambda: resource_manager)

    device = BaseDevice(ip=IP.POWER_SUPPLY)
    assert resource_manager.open_calls == 1

    device.connect()
    assert resource_manager.open_calls == 1

    with device as managed:
        assert managed is device
        assert resource_manager.open_calls == 1

    assert resource_manager.instrument.close_calls == 1


def test_base_device_can_reconnect_after_close(monkeypatch):
    resource_manager = FakeResourceManager()
    monkeypatch.setattr(pyvisa, "ResourceManager", lambda: resource_manager)

    device = BaseDevice(ip=IP.POWER_SUPPLY)
    device.close()
    device.close()
    assert resource_manager.instrument.close_calls == 1

    device.connect()
    assert resource_manager.open_calls == 2
