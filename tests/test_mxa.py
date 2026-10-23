import pyvisa

from iyzee.devices.mxa import KeysightMXA


class FakeInstrument:
    def __init__(self):
        self.commands = []
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.close_count = 0
        self.responses = {
            "FREQ:STAR?": "100.0",
            "FREQ:STOP?": "200.0",
            "SWE:POIN?": "3",
            "*OPC?": "1",
            ":TRACe:DATA? TRACe1": "1.0,2.0,3.0",
        }

    def write(self, command):
        self.commands.append(command)

    def query(self, command):
        return self.responses[command]

    def query_binary_values(self, command, **kwargs):
        self.commands.append(command)
        return [1.0, 2.0, 3.0]

    def close(self):
        self.close_count += 1
        self.commands.append("<CLOSE>")


class FakeResourceManager:
    def __init__(self):
        self.opened = []

    def open_resource(self, address):
        instrument = FakeInstrument()
        self.opened.append((address, instrument))
        return instrument


def make_mxa():
    instrument = FakeInstrument()
    mxa = KeysightMXA.__new__(KeysightMXA)
    mxa.instrument = instrument
    mxa.timeout_ms = 5000
    return mxa, instrument


def test_constructor_connects_with_the_configured_visa_settings():
    resource_manager = FakeResourceManager()

    mxa = KeysightMXA("10.0.0.1", timeout_ms=1234, resource_manager=resource_manager)

    assert mxa.rm is resource_manager
    assert resource_manager.opened == []
    assert mxa.instrument is None

    mxa.connect()
    assert len(resource_manager.opened) == 1
    instrument = resource_manager.opened[0][1]
    assert mxa.instrument is instrument
    assert resource_manager.opened[0][0] == "TCPIP0::10.0.0.1::inst0::INSTR"
    assert instrument.timeout == 1234
    assert instrument.read_termination == "\n"
    assert instrument.write_termination == "\n"

    mxa.connect()
    assert len(resource_manager.opened) == 1

    mxa.close()
    assert mxa.instrument is None
    assert instrument.close_count == 1


def test_wait_opc_requires_explicit_completion_response():
    mxa, instrument = make_mxa()
    assert mxa.wait_opc() is True

    instrument.responses["*OPC?"] = "0"
    assert mxa.wait_opc() is False


def test_wait_opc_drops_a_timed_out_resource_without_touching_it_again():
    mxa, instrument = make_mxa()

    def timeout(command):
        raise pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_timeout)

    instrument.query = timeout
    assert mxa.wait_opc(timeout_sec=0.01) is False
    assert mxa.instrument is None
    assert instrument.close_count == 1


def test_get_errors_drains_scpi_error_queue():
    mxa, instrument = make_mxa()
    responses = iter(["-100,Command error", "-200,Execution error", "0,No error"])
    instrument.query = lambda command: next(responses)

    assert mxa.get_errors() == ["-100,Command error", "-200,Execution error"]


def test_get_errors_stops_if_the_queue_never_reports_empty():
    import pytest

    mxa, instrument = make_mxa()
    seen = []
    instrument.query = lambda command: seen.append(command) or "-100,Command error"

    with pytest.raises(RuntimeError, match="did not terminate after 3 replies"):
        mxa.get_errors(max_entries=3)
    assert seen == ["SYST:ERR?"] * 3


def test_frequency_configuration_is_sent_to_instrument():
    mxa, instrument = make_mxa()
    mxa.set_center_freq(1e6)
    mxa.set_span(100e3)
    mxa.set_rbw(10e3)
    assert instrument.commands == [
        "FREQ:CENT 1000000.0",
        "FREQ:SPAN 100000.0",
        "BWID 10000.0",
    ]
