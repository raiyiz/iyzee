import pyvisa
import pytest
from pyvisa.constants import StatusCode

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


def test_scpi_helpers_wait_for_completion_drain_errors_and_send_frequencies():
    # -- get errors drains scpi error queue
    mxa, instrument = make_mxa()
    responses = iter(["-100,Command error", "-200,Execution error", "0,No error"])
    instrument.query = lambda command: next(responses)

    assert mxa.get_errors() == ["-100,Command error", "-200,Execution error"]

    # -- frequency configuration is sent to instrument
    mxa, instrument = make_mxa()
    mxa.set_center_freq(1e6)
    mxa.set_span(100e3)
    mxa.set_rbw(10e3)
    assert instrument.commands == [
        "FREQ:CENT 1000000.0",
        "FREQ:SPAN 100000.0",
        "BWID 10000.0",
    ]


@pytest.mark.parametrize("operation", ["write", "query", "binary"])
def test_failed_visa_io_discards_the_session(operation):
    mxa, instrument = make_mxa()

    def fail(*args, **kwargs):
        raise pyvisa.errors.VisaIOError(StatusCode.error_timeout)

    with pytest.raises(pyvisa.errors.VisaIOError):
        if operation == "write":
            instrument.write = fail
            mxa.write("ABOR")
        elif operation == "query":
            instrument.query = fail
            mxa.query("*IDN?")
        else:
            instrument.query_binary_values = fail
            mxa.get_trace_data(binary=True)

    assert mxa.instrument is None
    assert instrument.close_count == 1


def test_wait_opc_timeout_aborts_and_discards_the_session():
    mxa, instrument = make_mxa()

    def timeout(_command):
        raise pyvisa.errors.VisaIOError(StatusCode.error_timeout)

    instrument.query = timeout

    assert mxa.wait_opc(timeout_sec=0.01) is False
    assert instrument.commands[-2:] == ["ABOR", "<CLOSE>"]
    assert mxa.instrument is None
    assert instrument.close_count == 1
