"""Driver-level tests for :class:`~iyzee.devices.scope.LeCroy`: commands sent, replies parsed.

The VISA resource is a fake (see ``helpers.FakeVisaResource``); nothing here opens a socket.
"""

import struct

import numpy as np
import pytest
from helpers import FakeResourceManager, FakeVisaResource
from pyvisa.constants import StatusCode
from pyvisa.errors import VisaIOError

from iyzee.devices.scope import (
    Channel,
    Coupling,
    LeCroy,
    LeCroyProtocolError,
    LeCroyTimeoutError,
    TriggerCoupling,
    TriggerMode,
    TriggerSlope,
)


def connected(*replies, timeout: int = 10_000) -> tuple[LeCroy, FakeVisaResource]:
    """A LeCroy wired to a fake VISA resource that will serve ``replies``."""
    resource = FakeVisaResource(replies, timeout=timeout)
    scope = LeCroy("10.0.0.1", resource_manager=FakeResourceManager(resource))
    scope.connect()
    return scope, resource


def wf_block(data: bytes, header: bytes = b"C1:WF DAT1,", trailer: bytes = b"\n") -> bytes:
    return header + b"#9" + b"%09d" % len(data) + data + trailer


def visa_timeout() -> VisaIOError:
    return VisaIOError(StatusCode.error_timeout)


# -- connection ------------------------------------------------------------------------------


def test_connect_opens_the_vxi11_resource_and_applies_timeout_and_chunk_size():
    manager = FakeResourceManager()
    scope = LeCroy("10.0.0.1", timeout_ms=7_000, resource_manager=manager)
    assert not scope.connected

    scope.connect()

    assert manager.opened == ["TCPIP0::10.0.0.1::inst0::INSTR"]
    assert scope.connected and scope.address == "10.0.0.1"
    assert manager.resource.timeout == 7_000
    assert manager.resource.chunk_size == LeCroy.CHUNK_SIZE

    scope.disconnect()
    assert manager.resource.closed and not scope.connected


def test_connect_timeout_is_a_lecroy_timeout():
    class Refusing(FakeResourceManager):
        def open_resource(self, address):
            raise visa_timeout()

    scope = LeCroy("10.0.0.1", resource_manager=Refusing())

    with pytest.raises(LeCroyTimeoutError, match="10.0.0.1"):
        scope.connect()
    assert not scope.connected


def test_a_silent_scope_raises_lecroy_timeout_and_drops_the_connection():
    scope, resource = connected(visa_timeout())

    with pytest.raises(LeCroyTimeoutError):
        scope.query("C1:VOLT_DIV?")

    assert scope.connected is False and resource.closed


def test_commands_before_connect_fail_clearly():
    scope = LeCroy("10.0.0.1", resource_manager=FakeResourceManager())
    with pytest.raises(RuntimeError, match="not connected"):
        scope.send("TRIG_MODE AUTO")


def test_idn_asks_for_the_identity_and_can_widen_the_timeout():
    scope, resource = connected("LECROY,WP804HD-MS,SN,11.6.0\n")

    assert scope.idn(timeout=15.0) == "LECROY,WP804HD-MS,SN,11.6.0"

    assert resource.written == ["*IDN?"]
    assert resource.timeouts == [10_000, 15_000, 10_000]  # connect, widened, restored


# -- waveform download -----------------------------------------------------------------------


def test_internal_word_read_decodes_an_int16_array_and_sets_format_first():
    # 0x0A bytes inside the data must not end the read early.
    body = struct.pack("<3h", -32768, 10, 32767)
    scope, resource = connected(wf_block(body))

    codes = scope._read_words("C1", "DAT1")

    assert resource.written == ["CFMT DEF9,WORD,BIN", "CORD LO", "C1:WF? DAT1"]
    assert codes.dtype == np.int16
    assert codes.tolist() == [-32768, 10, 32767]


def test_get_horizontal_properties_reads_unit_offset_and_interval():
    scope, _ = connected('Unit Name = s"\n', 'VALUE: 0.25"\n', 'VALUE: 0.001"\n')

    unit, offset, interval = scope.getHorProperties(channel="C1")

    assert unit == "s"
    assert offset == pytest.approx(0.25)
    assert interval == pytest.approx(0.001)


def test_get_data_floats_detailed_retains_raw_codes_and_calibration():
    data = struct.pack("<2h", 100, -50)
    scope, _ = connected(wf_block(data), 'VALUE: 0.25"\n', 'VALUE: 2.0"\n', 'Unit Name = V"\n')

    detailed = scope.getDataFloatsDetailed(channel="C1", block="DAT1")

    assert detailed["unit"] == "V"
    np.testing.assert_array_equal(detailed["raw_codes"], [100, -50])
    np.testing.assert_allclose(detailed["values"], [199.75, -100.25])
    assert detailed["vertical_gain"] == 2.0
    assert detailed["vertical_offset"] == 0.25


@pytest.mark.parametrize(
    "reply,match",
    [
        (b"no block here\n", "no DEF9"),
        (b"C1:WF DAT1,#9000", "invalid DEF9 byte count"),
        (wf_block(b"\x00\x01\x02\x03")[:-3], "expected 4 bytes, got 2"),
        (wf_block(b"\x00\x01\x02\x03", trailer=b"junk"), "unexpected bytes after"),
        (wf_block(b"\x00\x01\x02"), "odd number"),
    ],
)
def test_malformed_waveform_blocks_are_rejected_but_keep_the_link(reply, match):
    scope, resource = connected(reply)

    with pytest.raises(LeCroyProtocolError, match=match):
        scope._read_words("C1", "DAT1")

    assert scope.connected and not resource.closed  # the whole reply was read; stream is aligned


def test_a_waveform_read_timeout_drops_the_connection():
    scope, _ = connected(visa_timeout())
    with pytest.raises(LeCroyTimeoutError):
        scope._read_words("C1", "DAT1")
    assert not scope.connected


# -- channel / trigger control ---------------------------------------------------------------


@pytest.mark.parametrize(
    "call,expected",
    [
        (lambda s: s.set_volts_per_div(Channel.C1, 0.5), "C1:VOLT_DIV 0.5"),
        (lambda s: s.set_offset(Channel.C2, -0.3), "C2:OFFSET -0.3"),
        (lambda s: s.set_coupling(Channel.C1, Coupling.DC_50), "C1:COUPLING D50"),
        (lambda s: s.set_coupling(Channel.C1, Coupling.AC_1M), "C1:COUPLING A1M"),
        (lambda s: s.set_trace_display(Channel.C1, True), "C1:TRACE ON"),
        (lambda s: s.set_trace_display(Channel.C1, False), "C1:TRACE OFF"),
        (lambda s: s.set_trace_display("F1", True), "F1:TRACE ON"),
        (lambda s: s.set_trigger_mode(TriggerMode.SINGLE), "TRIG_MODE SINGLE"),
        (lambda s: s.set_trigger_source(Channel.C1), "TRIG_SELECT EDGE,SR,C1"),
        (lambda s: s.set_trigger_level(Channel.C1, 1.5), "C1:TRIG_LEVEL 1.5"),
        (lambda s: s.set_trigger_slope(Channel.C1, TriggerSlope.NEGATIVE), "C1:TRIG_SLOPE NEG"),
        (
            lambda s: s.set_trigger_coupling(Channel.C1, TriggerCoupling.HF_REJECT),
            "C1:TRIG_COUPLING HFREJ",
        ),
        (lambda s: s.set_time_per_div(2e-6), "TIME_DIV 2e-06"),
    ],
)
def test_setters_send_the_documented_command(call, expected):
    scope, resource = connected()

    call(scope)

    assert resource.written == [expected]


@pytest.mark.parametrize(
    "call,command",
    [
        (lambda s: s.get_coupling(Channel.C1), "C1:COUPLING?"),
        (lambda s: s.get_volts_per_div(Channel.C1), "C1:VOLT_DIV?"),
        (lambda s: s.get_offset(Channel.C2), "C2:OFFSET?"),
        (lambda s: s.get_trace_display(Channel.C2), "C2:TRACE?"),
        (lambda s: s.get_time_per_div(), "TIME_DIV?"),
        (lambda s: s.get_trigger_mode(), "TRIG_MODE?"),
        (lambda s: s.get_trigger_source(), "TRIG_SELECT?"),
        (lambda s: s.get_trigger_slope(Channel.C1), "C1:TRIG_SLOPE?"),
        (lambda s: s.get_trigger_coupling(Channel.C1), "C1:TRIG_COUPLING?"),
        (lambda s: s.get_trigger_level(Channel.C1), "C1:TRIG_LEVEL?"),
    ],
)
def test_getters_query_and_return_the_trimmed_response(call, command):
    scope, resource = connected("  reply text \n")

    assert call(scope) == "reply text"
    assert resource.written == [command]


# -- command strings are validated before they reach the instrument --------------------------


@pytest.mark.parametrize("bad", ["C1:VOLT_DIV 1;C2", "C1\n"])
def test_free_form_channel_names_must_be_plain_identifiers(bad):
    scope, resource = connected()

    with pytest.raises(ValueError, match="invalid channel"):
        scope.set_trace_display(bad, True)
    with pytest.raises(ValueError, match="invalid channel"):
        scope._read_words(bad, "DAT1")

    assert not resource.written, "nothing may be written for an invalid name"


def test_numeric_setters_reject_non_finite_values():
    scope, resource = connected()

    for call in (
        lambda: scope.set_volts_per_div(Channel.C1, float("nan")),
        lambda: scope.set_offset(Channel.C1, float("nan")),
        lambda: scope.set_trigger_level(Channel.C1, float("nan")),
    ):
        with pytest.raises(ValueError, match="must be finite"):
            call()

    assert not resource.written


def test_set_time_per_div_rejects_non_positive_values():
    scope, resource = connected()
    for bad in (0, -1e-6, float("nan")):
        with pytest.raises(ValueError):
            scope.set_time_per_div(bad)
    assert not resource.written
