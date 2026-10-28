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


def test_connecting_and_the_identity_query_fail_clearly():
    # -- connect applies timeout and chunk size and failures are clear
    manager = FakeResourceManager()
    scope = LeCroy("10.0.0.1", timeout_ms=7_000, resource_manager=manager)
    assert not scope.connected
    with pytest.raises(RuntimeError, match="not connected"):
        scope.send("TRIG_MODE AUTO")

    scope.connect()

    assert manager.opened == ["TCPIP0::10.0.0.1::inst0::INSTR"]
    assert scope.connected and scope.address == "10.0.0.1"
    assert manager.resource.timeout == 7_000
    assert manager.resource.chunk_size == LeCroy.CHUNK_SIZE
    scope.disconnect()
    assert manager.resource.closed and not scope.connected

    class Refusing(FakeResourceManager):
        def open_resource(self, address):
            raise visa_timeout()

    refused = LeCroy("10.0.0.1", resource_manager=Refusing())
    with pytest.raises(LeCroyTimeoutError, match=r"10\.0\.0\.1"):
        refused.connect()
    assert not refused.connected

    # -- a silent scope raises lecroy timeout and drops the connection
    for read in (lambda s: s.query("C1:VOLT_DIV?"), lambda s: s._read_words("C1", "DAT1")):
        scope, resource = connected(visa_timeout())
        with pytest.raises(LeCroyTimeoutError):
            read(scope)
        assert scope.connected is False and resource.closed

    # -- idn asks for the identity and can widen the timeout
    scope, resource = connected("LECROY,WP804HD-MS,SN,11.6.0\n")

    assert scope.idn(timeout=15.0) == "LECROY,WP804HD-MS,SN,11.6.0"

    assert resource.written == ["*IDN?"]
    assert resource.timeouts == [10_000, 15_000, 10_000]  # connect, widened, restored


# -- waveform download -----------------------------------------------------------------------


def test_waveforms_and_settings_use_the_documented_commands_and_refuse_bad_input():
    # -- waveform download decodes calibrates and rejects malformed blocks
    scope, resource = connected(wf_block(struct.pack("<3h", -32768, 10, 32767)))
    codes = scope._read_words("C1", "DAT1")
    assert resource.written == ["CFMT DEF9,WORD,BIN", "CORD LO", "C1:WF? DAT1"]
    assert codes.dtype == np.int16 and codes.tolist() == [-32768, 10, 32767]

    scope, _ = connected('Unit Name = s"\n', 'VALUE: 0.25"\n', 'VALUE: 0.001"\n')
    unit, offset, interval = scope.getHorProperties(channel="C1")
    assert (unit, offset, interval) == ("s", pytest.approx(0.25), pytest.approx(0.001))

    scope, _ = connected(
        wf_block(struct.pack("<2h", 100, -50)),
        'VALUE: 0.25"\n',
        'VALUE: 2.0"\n',
        'Unit Name = V"\n',
    )
    detailed = scope.getDataFloatsDetailed(channel="C1", block="DAT1")
    assert detailed["unit"] == "V"
    np.testing.assert_array_equal(detailed["raw_codes"], [100, -50])
    np.testing.assert_allclose(detailed["values"], [199.75, -100.25])
    assert detailed["vertical_gain"] == 2.0 and detailed["vertical_offset"] == 0.25

    for reply, match in (
        (b"no block here\n", "no DEF9"),
        (b"C1:WF DAT1,#9000", "invalid DEF9 byte count"),
        (wf_block(b"\x00\x01\x02\x03")[:-3], "expected 4 bytes, got 2"),
        (wf_block(b"\x00\x01\x02\x03", trailer=b"junk"), "unexpected bytes after"),
        (wf_block(b"\x00\x01\x02"), "odd number"),
    ):
        scope, resource = connected(reply)
        with pytest.raises(LeCroyProtocolError, match=match):
            scope._read_words("C1", "DAT1")
        assert scope.connected and not resource.closed  # the whole reply was read: stream aligned

    # -- setters and getters use the documented commands
    for call, expected in (
        (lambda s: s.set_volts_per_div(Channel.C1, 0.5), "C1:VOLT_DIV 0.5"),
        (lambda s: s.set_offset(Channel.C2, -0.3), "C2:OFFSET -0.3"),
        (lambda s: s.set_coupling(Channel.C1, Coupling.DC_50), "C1:COUPLING D50"),
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
    ):
        scope, resource = connected()
        call(scope)
        assert resource.written == [expected]

    for call, command in (
        (lambda s: s.get_coupling(Channel.C1), "C1:COUPLING?"),
        (lambda s: s.get_volts_per_div(Channel.C1), "C1:VOLT_DIV?"),
        (lambda s: s.get_trace_display(Channel.C2), "C2:TRACE?"),
        (lambda s: s.get_time_per_div(), "TIME_DIV?"),
        (lambda s: s.get_trigger_mode(), "TRIG_MODE?"),
        (lambda s: s.get_trigger_source(), "TRIG_SELECT?"),
        (lambda s: s.get_trigger_level(Channel.C1), "C1:TRIG_LEVEL?"),
    ):
        scope, resource = connected("  reply text \n")
        assert call(scope) == "reply text"  # trimmed
        assert resource.written == [command]

    # -- bad names and numbers are refused before anything is written
    scope, resource = connected()

    for bad in ("C1:VOLT_DIV 1;C2", "C1\n"):  # free-form names must be plain identifiers
        with pytest.raises(ValueError, match="invalid channel"):
            scope.set_trace_display(bad, True)
        with pytest.raises(ValueError, match="invalid channel"):
            scope._read_words(bad, "DAT1")
    for call in (
        lambda: scope.set_volts_per_div(Channel.C1, float("nan")),
        lambda: scope.set_offset(Channel.C1, float("nan")),
        lambda: scope.set_trigger_level(Channel.C1, float("nan")),
    ):
        with pytest.raises(ValueError, match="must be finite"):
            call()
    for bad_time in (0, -1e-6, float("nan")):
        with pytest.raises(ValueError):
            scope.set_time_per_div(bad_time)

    assert not resource.written


# -- channel / trigger control ---------------------------------------------------------------
