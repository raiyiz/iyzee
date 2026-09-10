from iyzee import CH
from iyzee.tui import devices


class FakeInstrument:
    def __init__(self):
        self.closed = False
        self.writes = []
        self.timeout = None

    def write(self, command):
        self.writes.append(command)

    def close(self):
        self.closed = True


class FakeResourceManager:
    def __init__(self):
        self.opened = []

    def open_resource(self, address):
        instrument = FakeInstrument()
        self.opened.append((address, instrument))
        return instrument


class FakeMXA:
    def __init__(self):
        self.instrument = None
        self.rm = FakeResourceManager()
        self.connect_calls = 0
        self.close_calls = 0

    def connect(self):
        self.connect_calls += 1
        if self.instrument is None:
            self.instrument = self.rm.open_resource("mxa")

    def close(self):
        self.close_calls += 1
        if self.instrument is not None:
            self.instrument.close()
            self.instrument = None


class FakePSU:
    def __init__(self):
        self.instrument = FakeInstrument()
        self.connect_calls = 0
        self.close_calls = 0

    def connect(self):
        self.connect_calls += 1
        if self.instrument is None:
            self.instrument = FakeInstrument()

    def close(self):
        self.close_calls += 1
        if self.instrument is not None:
            self.instrument.close()
            self.instrument = None


class FakeShutter:
    def __init__(self):
        self.psu = FakePSU()
        self.chan = CH.THREE
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class FakeScope:
    def __init__(self):
        self.connected = False
        self.connect_calls = 0
        self.close_calls = 0

    def connect(self):
        self.connect_calls += 1
        self.connected = True

    def close(self):
        self.close_calls += 1
        self.connected = False


def test_mxa_connect_is_lazy_and_idempotent(monkeypatch):
    created = []

    def make_mxa():
        mxa = FakeMXA()
        created.append(mxa)
        return mxa

    monkeypatch.setattr(devices, "KeysightMXA", make_mxa)
    manager = devices.DeviceManager()

    assert not manager.mxa_connected
    first = manager.connect_mxa()
    second = manager.connect_mxa()

    assert first is second
    assert len(created) == 1
    assert first.connect_calls == 2
    assert manager.mxa_connected


def test_shutter_connect_is_lazy(monkeypatch):
    created = []

    def make_shutter():
        shutter = FakeShutter()
        created.append(shutter)
        return shutter

    monkeypatch.setattr(devices, "ShutterControl", make_shutter)
    manager = devices.DeviceManager()

    shutter = manager.connect_shutter()

    assert created == [shutter]
    assert manager.shutter_connected
    assert shutter.psu.connect_calls == 0


def test_scope_connect_is_lazy_and_idempotent(monkeypatch):
    created = []

    def make_scope():
        scope = FakeScope()
        created.append(scope)
        return scope

    monkeypatch.setattr(devices, "LeCroy", make_scope)
    manager = devices.DeviceManager()

    assert not manager.scope_connected
    first = manager.connect_scope()
    second = manager.connect_scope()

    assert first is second
    assert created == [first]
    assert first.connect_calls == 2
    assert manager.scope_connected


def test_scope_operation_requires_explicit_connection(monkeypatch):
    scope = FakeScope()
    monkeypatch.setattr(devices, "LeCroy", lambda: scope)
    manager = devices.DeviceManager()

    with manager.scope_for_operation() as _:
        raise AssertionError("an unconnected scope should not be usable")


def test_close_all_releases_all_resources(monkeypatch):
    mxa = FakeMXA()
    shutter = FakeShutter()
    scope = FakeScope()
    monkeypatch.setattr(devices, "KeysightMXA", lambda: mxa)
    monkeypatch.setattr(devices, "ShutterControl", lambda: shutter)
    monkeypatch.setattr(devices, "LeCroy", lambda: scope)
    manager = devices.DeviceManager()

    manager.connect_mxa()
    manager.connect_shutter()
    manager.connect_scope()
    manager.close_all()

    assert not manager.mxa_connected
    assert not manager.shutter_connected
    assert not manager.scope_connected
    assert mxa.close_calls == 1
    assert shutter.close_calls == 1
    assert shutter.psu.close_calls == 1
    assert scope.close_calls == 1


def test_scope_operation_serializes_access(monkeypatch):
    scope = FakeScope()
    monkeypatch.setattr(devices, "LeCroy", lambda: scope)
    manager = devices.DeviceManager()
    manager.connect_scope()

    with manager.scope_for_operation() as connected:
        assert connected is scope
        assert manager.scope_connected
