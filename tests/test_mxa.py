from mxa import KeysightMXA


class FakeInstrument:
    def __init__(self):
        self.commands = []
        self.timeout = None
        self.read_termination = None
        self.write_termination = None
        self.responses = {
            "FREQ:STAR?": "100.0",
            "FREQ:STOP?": "200.0",
            "SWE:POIN?": "3",
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
        self.commands.append("<CLOSE>")


def make_mxa():
    instrument = FakeInstrument()
    mxa = KeysightMXA.__new__(KeysightMXA)
    mxa.instr = instrument
    mxa.timeout_ms = 5000
    return mxa, instrument


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
