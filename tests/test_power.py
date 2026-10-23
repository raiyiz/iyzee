import pytest
import pyvisa

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


def test_psu_sends_the_documented_commands_and_closes_its_transport():
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

    with PSU(ip=IP.POWER_SUPPLY, resource_manager=FakeResourceManager()) as managed:
        instrument = managed.instrument
    assert managed.instrument is None and instrument.close_calls == 1  # closed on exit


def test_the_shutter_is_a_channel_of_a_psu_it_borrows():
    resource_manager = FakeResourceManager()
    psu = PSU(resource_manager=resource_manager)
    shutter = ShutterControl(psu)

    # Constructing it sends nothing and opens nothing.
    assert shutter.psu is psu and shutter.chan == SHUTTER_CHANNEL_DEFAULT
    assert resource_manager.opened == [] and psu.instrument is None

    with pytest.raises(ConnectionError, match="not connected"):
        shutter.configure()

    psu.connect()
    instrument = psu.instrument
    assert instrument is not None
    shutter.configure()
    shutter.open()
    shutter.close()

    assert instrument.commands == [
        "INST:NSEL 3",
        "VOLT 1.7",
        "INST:NSEL 3",
        "CURR 0.01",
        "INST OUT3",
        "OUTP:SEL 1",
        "INST OUT3",
        "OUTP:SEL 0",
    ]
    # The shutter never closes the supply it borrowed.
    assert instrument.close_calls == 0 and psu.instrument is instrument


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


def test_retrieve_state_reports_what_the_supply_says():
    supply = FakeHMP()
    psu = _connected_psu(supply)
    assert supply.read_termination == "\n"  # set on open, so queries can return

    psu.enable_output(3)  # armed, but the master output is still off: not live
    armed = psu.retrieve_state([3])
    assert armed.channel(3).selected and not armed.general_output and not armed.is_live(3)
    assert [c.channel for c in armed.channels] == [3]
    with pytest.raises(KeyError, match="channel 1 was not retrieved"):
        armed.channel(1)

    psu.set_voltage(1.7, 3)
    psu.set_current(0.01, 3)
    psu.enable_global_output()
    state = psu.retrieve_state()

    assert isinstance(state, PSUState) and state.general_output is True
    assert [c.channel for c in state.channels] == [1, 2, 3, 4]
    assert state.channel(3) == ChannelState(
        channel=3,
        voltage_set=1.7,
        current_set=0.01,
        voltage_measured=1.7,
        current_measured=0.008,
        selected=True,
    )
    assert state.is_live(3) and not state.is_live(1)


def test_psu_drops_a_timed_out_session_and_does_not_restore_channel_on_closed_link():
    supply = FakeHMP()
    psu = _connected_psu(supply)

    def timeout(command):
        raise pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_timeout)

    supply.query = timeout
    with pytest.raises(pyvisa.errors.VisaIOError):
        psu.retrieve_state([3])
    assert psu.instrument is None


def test_retrieve_state_only_asks_and_puts_the_selected_channel_back():
    supply = FakeHMP()
    psu = _connected_psu(supply)
    psu.set_voltage(5.0, 2)  # leaves channel 2 selected
    supply.log.clear()

    psu.retrieve_state([3])

    assert [
        c
        for c in supply.log
        if c.startswith(("INST:NSEL", "VOLT ", "CURR ", "OUTP")) and not c.endswith("?")
    ] == ["INST:NSEL 3", "INST:NSEL 2"]  # no writes but selection

    supply.overrides["CURR?"] = "garbage"  # even a bad reply midway restores it
    with pytest.raises(ValueError, match="CURR"):
        psu.retrieve_state()
    assert supply.selected == 2


def test_bad_channels_and_bad_replies_are_errors_never_guesses():
    supply = FakeHMP()
    psu = _connected_psu(supply)
    supply.log.clear()
    for channels in ([0], [5], [1, 7], [-1]):
        with pytest.raises(ValueError, match="channels must be in"):
            psu.retrieve_state(channels)
    assert supply.log == []  # refused before any I/O

    for command, replies in {
        "VOLT?": ("", "nan", "inf", "ERR", "1,2"),
        "CURR?": ("nan",),
        "MEAS:VOLT?": ("nan",),
        "MEAS:CURR?": ("nan",),
    }.items():
        for reply in replies:
            supply.overrides.clear()
            supply.overrides[command] = reply
            with pytest.raises(
                ValueError, match=rf"answered .* to '{command.replace('?', r'\?')}'"
            ):
                psu.retrieve_state()

    for command in ("OUTP:SEL?", "OUTP:GEN?"):
        for reply in ("", "2", "ON", "0.5"):
            supply.overrides.clear()
            supply.overrides[command] = reply
            with pytest.raises(ValueError, match="expected 0 or 1"):
                psu.retrieve_state()


def test_safe_close_verifies_the_channel_is_disarmed():
    supply = FakeHMP()
    shutter = ShutterControl(_connected_psu(supply), chan=3)
    shutter.configure()
    shutter.open()
    assert supply.channels[3]["SEL"] == 1

    shutter.safe_close()
    assert supply.channels[3]["SEL"] == 0
    assert not shutter.is_open()


def test_safe_close_retries_when_the_channel_refuses_to_disarm(monkeypatch):
    supply = FakeHMP()
    psu = _connected_psu(supply)
    shutter = ShutterControl(psu, chan=3)
    shutter.configure()
    shutter.open()
    real_disable = psu.disable_output
    attempts = []

    def disable_once_fails(channel):
        attempts.append(channel)
        if len(attempts) == 1:
            return
        real_disable(channel)

    monkeypatch.setattr(psu, "disable_output", disable_once_fails)
    shutter.safe_close(retry_delay_s=0)
    assert attempts == [3, 3]
    assert supply.channels[3]["SEL"] == 0


def test_the_shutter_reports_open_only_when_the_supply_says_it_is_powered():
    supply = FakeHMP()
    shutter = ShutterControl(_connected_psu(supply), chan=2)
    shutter.configure()
    shutter.psu.enable_global_output()
    assert [c.channel for c in shutter.retrieve_state().channels] == [2]  # its channel only

    assert shutter.is_open() is False
    shutter.open()
    assert shutter.is_open() is True
    shutter.close()
    assert shutter.is_open() is False

    shutter.open()
    shutter.psu.disable_global_output()  # armed, but the master switch cuts the power
    assert shutter.is_open() is False
