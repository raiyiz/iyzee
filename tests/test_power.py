from iyzee import CH, IP
from iyzee.power import PSU, ShutterControl


class FakeInstrument:
    def __init__(self):
        self.commands = []
        self.timeout = None
        self.close_calls = 0

    def write(self, command):
        self.commands.append(command)

    def close(self):
        self.close_calls += 1


class FakeResourceManager:
    def __init__(self):
        self.opened = []

    def open_resource(self, address):
        instrument = FakeInstrument()
        self.opened.append((address, instrument))
        return instrument


def test_psu_accepts_injected_resource_manager():
    resource_manager = FakeResourceManager()

    psu = PSU(ip=IP.POWER_SUPPLY, resource_manager=resource_manager)

    assert psu.rm is resource_manager
    assert resource_manager.opened == []
    assert psu.instrument is None

    psu.connect()
    assert psu.instrument is resource_manager.opened[0][1]

    psu.set_voltage(1.7, CH.THREE)
    psu.set_current(0.01, CH.THREE)
    psu.enable_output(CH.THREE)
    psu.disable_output(CH.THREE)

    assert psu.instrument.commands == [
        "INST:NSEL 3",
        "VOLT 1.7",
        "INST:NSEL 3",
        "CURR 0.01",
        "INST OUT3",
        "OUTP:SEL 1",
        "INST OUT3",
        "OUTP:SEL 0",
    ]

    psu.close()
    assert psu.instrument is None
    assert resource_manager.opened[0][1].close_calls == 1


def test_psu_context_manager_closes_injected_transport():
    resource_manager = FakeResourceManager()

    with PSU(ip=IP.POWER_SUPPLY, resource_manager=resource_manager) as psu:
        instrument = psu.instrument
        psu.enable_global_output()

    assert psu.instrument is None
    assert instrument.close_calls == 1


def test_shutter_control_has_explicit_connection_lifecycle():
    resource_manager = FakeResourceManager()
    shutter = ShutterControl(resource_manager=resource_manager)

    assert resource_manager.opened == []
    assert shutter.psu.instrument is None

    shutter.connect()
    shutter.connect()
    instrument = shutter.psu.instrument
    assert instrument is not None
    assert resource_manager.opened == [(resource_manager.opened[0][0], instrument)]
    assert instrument.commands == [
        "INST:NSEL 3",
        "VOLT 1.7",
        "INST:NSEL 3",
        "CURR 0.01",
    ]

    shutter.open()
    shutter.disconnect()
    shutter.disconnect()

    assert instrument.commands[-4:] == [
        "INST OUT3",
        "OUTP:SEL 1",
        "INST OUT3",
        "OUTP:SEL 0",
    ]
    assert instrument.close_calls == 1
    assert shutter.psu.instrument is None


def test_shutter_control_context_manager_disconnects_the_psu():
    resource_manager = FakeResourceManager()

    with ShutterControl(resource_manager=resource_manager) as shutter:
        instrument = shutter.psu.instrument
        assert instrument is not None
        shutter.open()

    assert shutter.psu.instrument is None
    assert instrument.close_calls == 1
    assert instrument.commands[-2:] == ["INST OUT3", "OUTP:SEL 0"]
