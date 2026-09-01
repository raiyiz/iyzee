from iyzee import CH, IP
from iyzee.power import PSU


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
    assert resource_manager.opened[0][0] == "TCPIP::10.140.1.42::5025::SOCKET"
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
