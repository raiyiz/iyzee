import pytest

import mxa as mxa_module
from mxa import KeysightMXA


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
    mxa.instr = instrument
    mxa.timeout_ms = 5000
    return mxa, instrument


def test_constructor_opens_exactly_one_connection(monkeypatch):
    resource_manager = FakeResourceManager()
    monkeypatch.setattr(mxa_module.pyvisa, "ResourceManager", lambda: resource_manager)

    mxa = KeysightMXA("10.0.0.1", timeout_ms=1234)

    assert len(resource_manager.opened) == 1
    assert resource_manager.opened[0][0] == "TCPIP0::10.0.0.1::inst0::INSTR"
    assert mxa.instr is resource_manager.opened[0][1]
    assert mxa.instr.timeout == 1234
    assert mxa.instr.read_termination == "\n"
    assert mxa.instr.write_termination == "\n"

    mxa.connect()
    assert len(resource_manager.opened) == 1


def test_context_manager_does_not_reopen_connection(monkeypatch):
    resource_manager = FakeResourceManager()
    monkeypatch.setattr(mxa_module.pyvisa, "ResourceManager", lambda: resource_manager)

    mxa = KeysightMXA("10.0.0.1")
    instrument = mxa.instr

    with mxa as managed:
        assert managed is mxa
        assert mxa.instr is instrument
        assert len(resource_manager.opened) == 1

    assert mxa.instr is None
    assert instrument.close_count == 1


def test_close_is_idempotent_and_reconnects_after_close(monkeypatch):
    resource_manager = FakeResourceManager()
    monkeypatch.setattr(mxa_module.pyvisa, "ResourceManager", lambda: resource_manager)

    mxa = KeysightMXA("10.0.0.1")
    first_instrument = mxa.instr

    mxa.close()
    mxa.close()

    assert first_instrument.close_count == 1
    assert mxa.instr is None

    mxa.connect()
    assert len(resource_manager.opened) == 2
    assert mxa.instr is resource_manager.opened[1][1]


def test_disconnect_remains_alias_for_close(monkeypatch):
    resource_manager = FakeResourceManager()
    monkeypatch.setattr(mxa_module.pyvisa, "ResourceManager", lambda: resource_manager)

    mxa = KeysightMXA("10.0.0.1")
    instrument = mxa.instr

    mxa.disconnect()

    assert mxa.instr is None
    assert instrument.close_count == 1


def test_context_manager_closes_on_exception(monkeypatch):
    resource_manager = FakeResourceManager()
    monkeypatch.setattr(mxa_module.pyvisa, "ResourceManager", lambda: resource_manager)

    mxa = KeysightMXA("10.0.0.1")
    instrument = mxa.instr

    with pytest.raises(RuntimeError):
        with mxa:
            raise RuntimeError("acquisition failed")

    assert mxa.instr is None
    assert instrument.close_count == 1


def test_wait_opc_requires_explicit_completion_response():
    mxa, instrument = make_mxa()
    assert mxa.wait_opc() is True

    instrument.responses["*OPC?"] = "0"
    assert mxa.wait_opc() is False


def test_get_errors_drains_scpi_error_queue():
    mxa, instrument = make_mxa()
    responses = iter(["-100,Command error", "-200,Execution error", "0,No error"])
    instrument.query = lambda command: next(responses)

    assert mxa.get_errors() == ["-100,Command error", "-200,Execution error"]


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


def test_ascii_trace_can_be_read_without_hardware():
    mxa, _ = make_mxa()
    assert mxa.get_trace_data(trace_num=1, binary=False) == [1.0, 2.0, 3.0]


def test_frequency_axis_is_calculated_from_sweep_settings():
    mxa, _ = make_mxa()
    assert mxa.get_frequency_axis() == [100.0, 150.0, 200.0]


def test_single_sweep_wait_uses_fake_instrument():
    mxa, instrument = make_mxa()
    mxa.wait_opc = lambda timeout_sec=30.0: True
    assert mxa.single_sweep_wait() is True
    assert instrument.commands == ["INIT:CONT OFF", "INIT:IMM"]


def test_binary_trace_path_is_available_offline():
    mxa, instrument = make_mxa()
    assert mxa.get_trace_data(trace_num=2, binary=True) == [1.0, 2.0, 3.0]
    assert instrument.commands[:2] == ["FORMat:DATA REAL,32", "FORMat:BORDer NORM"]
