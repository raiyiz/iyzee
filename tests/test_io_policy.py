"""The I/O failure policy: a failed exchange never leaves a desynchronised link in use.

The fake keeps an output queue like a real instrument. A timed-out ``*OPC?`` leaves its
answer behind, and (as on the real thing) the next query would be handed that stale ``1``
instead of its own reply. These tests fail if a driver keeps using a link in that state.
"""

import pytest
import pyvisa
from pyvisa.constants import StatusCode

from iyzee.devices.base import InstrumentError
from iyzee.devices.mxa import KeysightMXA
from iyzee.devices.power import PSU


def visa_error(code: StatusCode = StatusCode.error_timeout) -> pyvisa.errors.VisaIOError:
    return pyvisa.errors.VisaIOError(code)


class StreamInstrument:
    """A resource with an output queue; ``busy`` models a sweep that has not finished."""

    def __init__(self, *, busy=False, clear_works=True):
        self.timeout = 5000
        self.busy = busy
        self.clear_works = clear_works
        self.outq: list[str] = []
        self.sent: list[str] = []
        self.closed = False
        self.replies = {"SWE:POIN?": "3", "*OPC?": "1"}

    def write(self, command):
        self.sent.append(command)
        if command == "ABOR":
            self.busy = False

    def query(self, command):
        self.sent.append(command)
        if command == "*OPC?" and self.busy:
            self.outq.append("1")  # arrives later, after the caller has given up
            raise visa_error()
        self.outq.append(self.replies[command])
        return self.outq.pop(0)  # a stale entry, if any, is read first

    def clear(self):
        if not self.clear_works:
            raise visa_error(StatusCode.error_nonsupported_operation)
        self.outq.clear()

    def close(self):
        self.closed = True


def make_mxa(instrument):
    mxa = KeysightMXA.__new__(KeysightMXA)
    mxa.instrument = instrument
    mxa.timeout_ms = 5000
    return mxa


# -- a timed-out wait ---------------------------------------------------------------------------


def test_a_timed_out_wait_is_resynchronised_so_the_next_query_gets_its_own_answer():
    instrument = StreamInstrument(busy=True)
    mxa = make_mxa(instrument)

    assert mxa.wait_opc(timeout_sec=0.01) is False

    assert mxa.connected and not instrument.closed  # proved in step, so the link stays up
    assert mxa.query("SWE:POIN?") == "3"  # not the stale "1" left behind by the timeout
    assert instrument.timeout == 5000  # the caller's timeout is restored
    assert "ABOR" in instrument.sent  # the sweep that was still running was stopped


def test_without_the_resync_the_stale_answer_would_be_read_as_the_next_reply():
    """The hazard itself, on the same fake: why a timed-out link must not simply be reused."""
    instrument = StreamInstrument(busy=True)
    with pytest.raises(pyvisa.errors.VisaIOError):
        instrument.query("*OPC?")
    assert instrument.query("SWE:POIN?") == "1"  # the wrong answer, silently


def test_if_sync_cannot_be_proved_the_link_is_dropped_not_reused():
    instrument = StreamInstrument(busy=True, clear_works=False)
    mxa = make_mxa(instrument)

    assert mxa.wait_opc(timeout_sec=0.01) is False

    assert not mxa.connected and instrument.closed
    with pytest.raises(RuntimeError, match="not connected"):
        mxa.query("SWE:POIN?")


def test_a_link_that_is_still_not_in_step_after_the_clear_is_dropped():
    instrument = StreamInstrument(busy=True)
    instrument.replies["*OPC?"] = "0"  # the sync check itself does not answer 1
    instrument.write = lambda command: instrument.sent.append(command)  # ABOR does not help
    mxa = make_mxa(instrument)

    assert mxa.wait_opc(timeout_sec=0.01) is False
    assert not mxa.connected


def test_a_wait_that_fails_for_another_reason_drops_the_link_and_raises():
    instrument = StreamInstrument()

    def lost(command):
        raise visa_error(StatusCode.error_connection_lost)

    instrument.query = lost
    mxa = make_mxa(instrument)

    with pytest.raises(pyvisa.errors.VisaIOError):
        mxa.wait_opc()
    assert not mxa.connected and instrument.closed


# -- every other exchange -----------------------------------------------------------------------


@pytest.mark.parametrize("action", ["write", "query", "query_binary"])
def test_any_failed_exchange_drops_the_link(action):
    instrument = StreamInstrument()

    def fail(*args, **kwargs):
        raise visa_error()

    instrument.write = instrument.query = instrument.query_binary_values = fail
    mxa = make_mxa(instrument)

    with pytest.raises(pyvisa.errors.VisaIOError):
        {
            "write": lambda: mxa.write("INIT:IMM"),
            "query": lambda: mxa.query("SWE:POIN?"),
            "query_binary": lambda: mxa.query_binary(":TRACe:DATA? TRACe1"),
        }[action]()
    assert not mxa.connected and instrument.closed


def test_a_dropped_link_can_be_connected_again():
    resources = [StreamInstrument(busy=True, clear_works=False), StreamInstrument()]

    class Manager:
        def open_resource(self, address):
            return resources.pop(0)

    mxa = KeysightMXA("192.0.2.1", resource_manager=Manager())
    mxa.connect()
    assert mxa.wait_opc(timeout_sec=0.01) is False and not mxa.connected

    mxa.connect()
    assert mxa.connected and mxa.query("SWE:POIN?") == "3"


# -- the supply follows the same policy ---------------------------------------------------------


def test_a_failed_supply_exchange_drops_the_link_and_is_not_masked_by_the_restore():
    class DyingSupply:
        def __init__(self):
            self.closed = False
            self.selected_restores = 0

        def write(self, command):
            if command.startswith("INST:NSEL"):
                self.selected_restores += 1

        def query(self, command):
            if command == "OUTP:GEN?":
                return "1\n"
            if command == "INST:NSEL?":
                return "2\n"
            raise visa_error()  # the channel read dies midway

        def close(self):
            self.closed = True

    supply = DyingSupply()
    psu = PSU.__new__(PSU)
    psu.instrument = supply

    with pytest.raises(pyvisa.errors.VisaIOError):  # the real failure, not "not connected"
        psu.retrieve_state([3])
    assert not psu.connected and supply.closed
    assert supply.selected_restores == 1  # only the select for the read; no write to a dead link


# -- the error queue ----------------------------------------------------------------------------


def queued(*messages):
    instrument = StreamInstrument()
    replies = iter([*messages, '+0,"No error"'])
    instrument.query = lambda command: next(replies)
    return make_mxa(instrument)


def test_the_error_queue_is_drained_and_a_clean_queue_passes():
    mxa = queued('-221,"Settings conflict"', '-222,"Data out of range"')
    assert mxa.get_errors() == ['-221,"Settings conflict"', '-222,"Data out of range"']
    queued().check_errors("configuring")  # nothing queued: no error


def test_check_errors_names_what_the_analyzer_said_and_where():
    with pytest.raises(InstrumentError, match=r"-222.*Data out of range.*while setting up"):
        queued('-222,"Data out of range"').check_errors("setting up")


def test_a_queue_that_never_reports_empty_is_an_error_not_an_endless_loop():
    instrument = StreamInstrument()
    polls = []
    instrument.query = lambda command: polls.append(command) or '-100,"Command error"'
    mxa = make_mxa(instrument)

    with pytest.raises(InstrumentError, match="did not drain"):
        mxa.get_errors(limit=5)
    assert len(polls) == 6


def test_an_unreadable_error_reply_is_an_error_not_an_endless_loop():
    instrument = StreamInstrument()
    instrument.query = lambda command: "garbled reply"
    with pytest.raises(InstrumentError, match="unreadable reply"):
        make_mxa(instrument).get_errors()


def test_read_back_reports_what_the_analyzer_is_actually_using():
    instrument = StreamInstrument()
    instrument.replies |= {
        "BWID?": "24000",
        "BWID:VID?": "48000",
        "SWE:TIME?": "0.01",
        "AVER:COUN?": "200",
        "AVER:TYPE?": "LOG",
    }
    assert make_mxa(instrument).read_back() == {
        "rbw_hz": 24000.0,
        "vbw_hz": 48000.0,
        "sweep_time_s": 0.01,
        "avg_count": 200,
        "avg_type": "LOG",
    }
