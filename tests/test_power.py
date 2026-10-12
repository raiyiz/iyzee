import pytest

from iyzee import IP, SHUTTER_CHANNEL_DEFAULT
from iyzee.devices.power import PSU, ChannelState, PSUState, ShutterControl


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

    psu.set_voltage(1.7, SHUTTER_CHANNEL_DEFAULT)
    psu.set_current(0.01, SHUTTER_CHANNEL_DEFAULT)
    psu.enable_output(SHUTTER_CHANNEL_DEFAULT)
    psu.disable_output(SHUTTER_CHANNEL_DEFAULT)

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
    assert len(resource_manager.opened) == 1
    assert resource_manager.opened[0][1] is instrument
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


class FakeHMP:
    """A small model of an HMP4040 that keeps state, so reads are checked against writes.

    A channel is live when it is armed (``OUTP:SEL 1``) and the master output is on.
    ``overrides`` replaces the reply to a query, to simulate a supply that answers badly.
    """

    def __init__(self):
        self.selected = 1
        self.general = 0
        self.channels = {n: {"VOLT": 0.0, "CURR": 0.1, "SEL": 0} for n in (1, 2, 3, 4)}
        self.overrides = {}
        self.log = []
        self.read_termination = None
        self.timeout = None

    def _live(self):
        return bool(self.general and self.channels[self.selected]["SEL"])

    def write(self, command):
        self.log.append(command)
        head, _, argument = command.partition(" ")
        if head == "INST:NSEL":
            self.selected = int(argument)
        elif head == "INST" and argument.startswith("OUT"):
            self.selected = int(argument[3:])
        elif head in ("VOLT", "CURR"):
            self.channels[self.selected][head] = float(argument)
        elif head == "OUTP:SEL":
            self.channels[self.selected]["SEL"] = int(argument)
        elif head == "OUTP:GEN":
            self.general = int(argument)

    def query(self, command):
        self.log.append(command)
        if command in self.overrides:
            return self.overrides[command]
        channel = self.channels[self.selected]
        replies = {
            "OUTP:GEN?": str(self.general),
            "INST:NSEL?": str(self.selected),
            "VOLT?": f"{channel['VOLT']:.3f}",
            "CURR?": f"{channel['CURR']:.3f}",
            "MEAS:VOLT?": f"{channel['VOLT'] if self._live() else 0.0:.3f}",
            "MEAS:CURR?": "0.008" if self._live() else "0.000",
            "OUTP:SEL?": str(channel["SEL"]),
        }
        return replies[command] + "\n"

    def close(self):
        pass


class FakeHMPResourceManager:
    def __init__(self, supply):
        self.supply = supply

    def open_resource(self, address):
        return self.supply


def _connected_psu(supply):
    psu = PSU(ip="192.0.2.1", resource_manager=FakeHMPResourceManager(supply))
    psu.connect()
    return psu


def test_opening_the_psu_sets_a_read_termination_so_queries_can_return():
    supply = FakeHMP()
    _connected_psu(supply)
    assert supply.read_termination == "\n"


def test_retrieve_state_reports_what_the_supply_says():
    supply = FakeHMP()
    psu = _connected_psu(supply)
    psu.set_voltage(1.7, 3)
    psu.set_current(0.01, 3)
    psu.enable_output(3)
    psu.enable_global_output()

    state = psu.retrieve_state()

    assert isinstance(state, PSUState)
    assert state.general_output is True
    assert [c.channel for c in state.channels] == [1, 2, 3, 4]
    assert state.channel(3) == ChannelState(
        channel=3,
        voltage_set=1.7,
        current_set=0.01,
        voltage_measured=1.7,
        current_measured=0.008,
        selected=True,
    )
    assert state.channel(1).selected is False
    assert state.is_live(3) and not state.is_live(1)


def test_an_armed_channel_is_not_live_while_the_master_output_is_off():
    supply = FakeHMP()
    psu = _connected_psu(supply)
    psu.enable_output(3)

    state = psu.retrieve_state([3])

    assert state.channel(3).selected is True
    assert state.general_output is False
    assert not state.is_live(3)


def test_retrieve_state_leaves_the_selected_channel_as_it_found_it():
    supply = FakeHMP()
    psu = _connected_psu(supply)
    psu.set_voltage(5.0, 2)  # leaves channel 2 selected
    assert supply.selected == 2

    psu.retrieve_state()

    assert supply.selected == 2
    assert supply.log[-1] == "INST:NSEL 2"


def test_retrieve_state_only_selects_and_asks():
    supply = FakeHMP()
    psu = _connected_psu(supply)
    supply.log.clear()

    psu.retrieve_state([3])

    writes = [
        c
        for c in supply.log
        if c.split(" ")[0] in ("INST:NSEL", "INST", "VOLT", "CURR", "OUTP:SEL", "OUTP:GEN")
    ]
    assert writes == ["INST:NSEL 3", "INST:NSEL 1"]  # select the channel, put the selection back
    assert all(c.endswith("?") or c.startswith("INST:NSEL ") for c in supply.log)


def test_retrieve_state_of_a_subset_asks_only_about_those_channels():
    supply = FakeHMP()
    psu = _connected_psu(supply)

    state = psu.retrieve_state([3])

    assert [c.channel for c in state.channels] == [3]
    with pytest.raises(KeyError, match="channel 1 was not retrieved"):
        state.channel(1)


@pytest.mark.parametrize("channels", [[0], [5], [1, 7], [-1]])
def test_retrieve_state_rejects_channels_the_supply_does_not_have_before_any_io(channels):
    supply = FakeHMP()
    psu = _connected_psu(supply)
    supply.log.clear()

    with pytest.raises(ValueError, match="channels must be in"):
        psu.retrieve_state(channels)

    assert supply.log == []


@pytest.mark.parametrize("command", ["VOLT?", "CURR?", "MEAS:VOLT?", "MEAS:CURR?"])
@pytest.mark.parametrize("reply", ["", "nan", "inf", "-inf", "ERR", "1,2"])
def test_a_bad_number_names_the_command_and_is_never_returned(command, reply):
    supply = FakeHMP()
    supply.overrides[command] = reply
    psu = _connected_psu(supply)

    with pytest.raises(ValueError, match=rf"answered .* to '{command.replace('?', r'\?')}'"):
        psu.retrieve_state()


@pytest.mark.parametrize("command", ["OUTP:SEL?", "OUTP:GEN?"])
@pytest.mark.parametrize("reply", ["", "2", "ON", "-1", "0.5"])
def test_a_bad_on_off_flag_is_an_error_not_a_guess(command, reply):
    supply = FakeHMP()
    supply.overrides[command] = reply
    psu = _connected_psu(supply)

    with pytest.raises(ValueError, match=r"expected 0 or 1"):
        psu.retrieve_state()


def test_the_selection_is_restored_even_when_a_reply_is_bad_midway():
    supply = FakeHMP()
    psu = _connected_psu(supply)
    psu.set_voltage(5.0, 2)
    supply.overrides["CURR?"] = "garbage"

    with pytest.raises(ValueError, match="CURR"):
        psu.retrieve_state()

    assert supply.selected == 2


def test_the_shutter_reports_open_only_when_the_supply_says_it_is_powered():
    supply = FakeHMP()
    shutter = ShutterControl(ip="192.0.2.1", resource_manager=FakeHMPResourceManager(supply))
    shutter.connect()
    shutter.psu.enable_global_output()

    assert shutter.is_open() is False
    shutter.open()
    assert shutter.is_open() is True
    shutter.close()
    assert shutter.is_open() is False

    shutter.open()
    shutter.psu.disable_global_output()  # armed, but the master switch cuts the power
    assert shutter.is_open() is False


def test_the_shutter_state_covers_its_own_channel_only():
    supply = FakeHMP()
    shutter = ShutterControl(
        chan=2, ip="192.0.2.1", resource_manager=FakeHMPResourceManager(supply)
    )
    shutter.connect()

    assert [c.channel for c in shutter.retrieve_state().channels] == [2]
