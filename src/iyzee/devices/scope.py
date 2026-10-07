import math
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum

import numpy as np
import pyvisa
from pyvisa.constants import StatusCode

from ..config import IP, address
from .base import BaseDevice


class LeCroyTimeoutError(TimeoutError):
    """The scope didn't answer within the VISA timeout.

    A timed-out exchange leaves the reply stream in an unknown state (a late
    answer would be handed to the next query), so the driver closes the
    connection before raising this. ``LeCroy.connected`` becomes ``False`` and
    the caller must reconnect. It is a ``TimeoutError`` subclass, so existing
    ``except TimeoutError`` / ``except OSError`` handlers still catch it.
    """


class LeCroyProtocolError(RuntimeError):
    """A complete reply arrived but wasn't the expected shape (bad block header,
    wrong byte count). The connection stays usable: nothing is left unread."""


class Channel(StrEnum):
    """Analog input channel identifiers, as used in a command's header path
    (e.g. ``C1:VOLT_DIV 0.5``)."""

    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"


class Coupling(StrEnum):
    """A channel's own vertical input coupling and termination impedance.

    Distinct from :class:`TriggerCoupling`, which is the coupling of the
    *trigger path*, not of the displayed signal.
    """

    DC_50 = "D50"
    DC_1M = "D1M"
    AC_1M = "A1M"
    GROUND = "GND"


class TriggerCoupling(StrEnum):
    """Coupling of the trigger path (see :class:`Coupling` for the channel's
    own vertical coupling)."""

    DC = "DC"
    AC = "AC"
    HF_REJECT = "HFREJ"
    LF_REJECT = "LFREJ"


class TriggerSlope(StrEnum):
    POSITIVE = "POS"
    NEGATIVE = "NEG"


class TriggerMode(StrEnum):
    """AUTO free-runs if no trigger is found; NORMAL waits indefinitely for
    one; SINGLE arms for exactly one acquisition and then stops; STOP halts
    acquisition entirely."""

    AUTO = "AUTO"
    NORMAL = "NORM"
    SINGLE = "SINGLE"
    STOP = "STOP"


__all__ = [
    "Channel",
    "Coupling",
    "LeCroy",
    "LeCroyProtocolError",
    "LeCroyTimeoutError",
    "TriggerCoupling",
    "TriggerMode",
    "TriggerSlope",
]


_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,15}")


def _ident(value: object, what: str = "identifier") -> str:
    """A header-path token (``C1``, ``F2``, ``DAT1``) that is safe to interpolate.

    Channels are normally the enums above, but several methods also accept a
    plain ``str``; without this a value such as ``"C1:VOLT_DIV 1;C2"`` would be
    sent to the instrument verbatim.
    """
    text = str(value)
    if _IDENT.fullmatch(text) is None:
        raise ValueError(f"invalid {what} {text!r}")
    return text


def _finite(value: float, what: str) -> float:
    if not math.isfinite(float(value)):
        raise ValueError(f"{what} must be finite, got {value!r}")
    return value  # unchanged: ``10`` stays ``10``, not ``10.0``


_TERMINATORS = (b"\n", b"\r\n")


def _definite_block(raw: bytes) -> bytes:
    """The payload of a LeCroy ``DEF9`` binary block (``#9`` + nine-digit byte count).

    ``raw`` is a complete reply, possibly preceded by a ``C1:WF DAT1,`` header
    echo and followed by a line terminator. The declared count is authoritative:
    a truncated block is an error, not something to pad out with the terminator.
    """
    marker = raw.find(b"#9")
    if marker < 0:
        raise LeCroyProtocolError("binary reply has no DEF9 (#9) block")
    count_field = raw[marker + 2 : marker + 11]
    if len(count_field) < 9 or not count_field.isdigit():
        raise LeCroyProtocolError(f"invalid DEF9 byte count {count_field!r}")
    expected = int(count_field)
    body = raw[marker + 11 :]
    if len(body) < expected:
        raise LeCroyProtocolError(f"expected {expected} bytes, got {len(body)}")
    if body[expected:] not in (b"", *_TERMINATORS):
        raise LeCroyProtocolError(f"unexpected bytes after DEF9 block: {body[expected:]!r}")
    return body[:expected]


class LeCroy(BaseDevice):
    """Remote control and waveform download for LeCroy scopes over VISA (VXI-11).

    The scope must have remote control set to LXI/VXI-11 (not VICP). The driver
    targets the LeCroy/Teledyne LeCroy IEEE-488.2-style command dialect used by
    WaveSurfer, WaveAce and X-Stream. Channel and trigger methods forward that
    dialect; anything else is one ``scope.send("...")`` / ``scope.query("...")``
    away.

    Those commands follow Teledyne LeCroy's Remote Control manuals; most accept
    a ``?`` query form to read back what was set.
    """

    DEFAULT_TIMEOUT_MS = 10_000
    CHUNK_SIZE = 1 << 20  # VISA read chunk; the default is far too small for long records

    def __init__(
        self,
        ip: str | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        resource_manager=None,
    ):
        super().__init__(
            ip=ip or address(IP.SCOPE),
            resource_manager=resource_manager,
            timeout_ms=timeout_ms,
            # No read termination: waveform bytes may contain 0x0A, and VXI-11
            # marks the end of a reply itself. Text replies are stripped below.
            read_termination=None,
            write_termination="\n",
        )
        self._lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.instrument is not None

    @property
    def address(self) -> str:
        return str(self.ip)

    @property
    def transaction_lock(self) -> threading.RLock:
        """The one re-entrant lock guarding this connection.

        Anything that must serialize with the driver (e.g. an instrument
        handle) should share this lock rather than keep a second one.
        """
        return self._lock

    @property
    def SOCK_TIMEOUT(self) -> float:
        return self.timeout_ms / 1000

    def connect(self) -> None:
        """Open the VISA resource (idempotent). Raises :class:`LeCroyTimeoutError` / VISA errors."""
        with self._lock:
            if self.instrument is not None:
                return
            try:
                super().connect()
            except pyvisa.errors.VisaIOError as exc:
                self._drop()
                raise self._translate(exc) from exc
            if self.instrument:
                self.instrument.chunk_size = self.CHUNK_SIZE

    def disconnect(self) -> None:
        """Close the connection (idempotent)."""
        self.close()

    def _drop(self) -> None:
        try:
            self.close()
        except Exception:  # the link is already broken; closing is best effort
            self.instrument = None

    def _translate(self, exc: pyvisa.errors.VisaIOError) -> Exception:
        if exc.error_code == StatusCode.error_timeout:
            return LeCroyTimeoutError(
                f"{self.ip} did not respond within {self.timeout_ms / 1000:g}s"
            )
        return exc

    @contextmanager
    def _io(self) -> Iterator:
        """Hold the lock for one VISA exchange; any I/O failure drops the connection."""
        with self._lock:
            inst = self.instrument
            if inst is None:
                raise RuntimeError("Instrument not connected")
            try:
                yield inst
            except pyvisa.errors.VisaIOError as exc:
                self._drop()
                translated = self._translate(exc)
                if translated is exc:
                    raise
                raise translated from exc

    def send(self, message: str) -> None:
        """Send one command."""
        with self._io() as inst:
            inst.write(message)

    def query(self, message: str, *, timeout: float | None = None) -> str:
        """Send ``message`` and atomically return the trimmed response text.

        ``timeout`` (seconds) overrides the VISA timeout for this exchange only.
        """
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._io() as inst:
            previous = inst.timeout
            if timeout is not None:
                inst.timeout = int(timeout * 1000)
            try:
                return inst.query(message).strip()
            finally:
                if timeout is not None and self.instrument is inst:
                    inst.timeout = previous

    def idn(self, *, timeout: float | None = None) -> str:
        """Return the scope's ``*IDN?`` identification string."""
        return self.query("*IDN?", timeout=timeout)

    def _inspect(self, channel: Channel, field: str) -> str:
        return self.query(f'{_ident(channel, "channel")}:INSPECT? "{field}"')

    def _inspect_number(self, channel: Channel, field: str) -> float:
        return float(self._inspect(channel, field).split(":")[-1].strip().strip('"').strip())

    def _inspect_unit(self, channel: Channel, field: str) -> str:
        return self._inspect(channel, field).split("Unit Name = ")[-1].strip().strip('"')

    # ------------------------------------------------------------------
    # Channel (vertical) control
    # ------------------------------------------------------------------
    def set_volts_per_div(self, channel: Channel, volts_per_div: float) -> None:
        """Set the vertical scale for ``channel``, in volts/division."""
        self.send(f"{channel}:VOLT_DIV {_finite(volts_per_div, 'volts_per_div')}")

    def set_offset(self, channel: Channel, offset_volts: float) -> None:
        """Set the vertical offset for ``channel``, in volts."""
        self.send(f"{channel}:OFFSET {_finite(offset_volts, 'offset_volts')}")

    def set_coupling(self, channel: Channel, coupling: Coupling) -> None:
        """Set the input coupling and termination impedance for ``channel``.

        Note this is the channel's own vertical coupling — the signal path
        that gets displayed and digitized. To couple the *trigger* path
        instead (which may be a different channel, and can differ from its
        own vertical coupling), use :meth:`set_trigger_coupling`.
        """
        self.send(f"{channel}:COUPLING {coupling}")

    def set_trace_display(self, channel: Channel | str, state: bool) -> None:
        """Show or hide ``channel``'s trace on the display.

        Also works for a math trace (``"F1"``): any header-path prefix is accepted.
        """
        self.send(f"{_ident(channel, 'channel')}:TRACE {'ON' if state else 'OFF'}")

    def get_coupling(self, channel: Channel) -> str:
        """Return the device's raw response to a coupling query (see
        :meth:`query` for why this isn't parsed further)."""
        return self.query(f"{channel}:COUPLING?")

    def get_volts_per_div(self, channel: Channel) -> str:
        """Return the device's raw response to a volts/div query (see
        :meth:`query` for why this isn't parsed further)."""
        return self.query(f"{channel}:VOLT_DIV?")

    def get_offset(self, channel: Channel) -> str:
        """Return the device's raw response to a vertical-offset query
        (see :meth:`query` for why this isn't parsed further)."""
        return self.query(f"{channel}:OFFSET?")

    def get_trace_display(self, channel: Channel | str) -> str:
        return self.query(f"{_ident(channel, 'channel')}:TRACE?")

    # ------------------------------------------------------------------
    # Trigger control
    # ------------------------------------------------------------------
    def set_trigger_mode(self, mode: TriggerMode) -> None:
        """Set the overall trigger mode (see :class:`TriggerMode`)."""
        self.send(f"TRIG_MODE {mode}")

    def set_trigger_source(self, source: Channel) -> None:
        """Arm an Edge trigger on ``source``.

        Only the Edge trigger type is exposed here (the common case, and the
        one whose remote syntax is stable across the LeCroy family this
        driver targets). Other trigger types (glitch, width, TV, ...) use
        their own multi-parameter ``TRIG_SELECT`` forms, not implemented
        here — use :meth:`send` directly for those, e.g.
        ``scope.send("TRIG_SELECT GLIT,SR,C1,...")``, consulting your
        instrument's trigger chapter for the exact parameters it wants.
        """
        self.send(f"TRIG_SELECT EDGE,SR,{source}")

    def set_trigger_level(self, source: Channel, level_volts: float) -> None:
        """Set the trigger level for ``source``, in volts."""
        self.send(f"{source}:TRIG_LEVEL {_finite(level_volts, 'level_volts')}")

    def set_trigger_slope(self, source: Channel, slope: TriggerSlope) -> None:
        self.send(f"{source}:TRIG_SLOPE {slope}")

    def set_trigger_coupling(self, source: Channel, coupling: TriggerCoupling) -> None:
        """Set the coupling of the trigger path fed by ``source``.

        Distinct from that channel's own vertical coupling — see
        :meth:`set_coupling`.
        """
        self.send(f"{source}:TRIG_COUPLING {coupling}")

    def set_time_per_div(self, seconds_per_div: float) -> None:
        """Set the horizontal scale (timebase), in seconds/division."""
        if _finite(seconds_per_div, "seconds_per_div") <= 0:
            raise ValueError(f"seconds_per_div must be positive, got {seconds_per_div!r}")
        self.send(f"TIME_DIV {seconds_per_div}")

    def get_time_per_div(self) -> str:
        """Return the device's raw response to a time/div query (see
        :meth:`query` for why this isn't parsed further)."""
        return self.query("TIME_DIV?")

    def get_trigger_mode(self) -> str:
        return self.query("TRIG_MODE?")

    def get_trigger_source(self) -> str:
        """Return the device's raw response to ``TRIG_SELECT?``.

        Unlike the other trigger getters, this reads back a whole
        multi-field line (trigger type, source, and qualifiers), not a
        single value — LeCroy's ``TRIG_SELECT?`` is the only query that
        reports which channel :meth:`set_trigger_source` last armed, and
        it echoes the full trigger-type description that command family
        uses. :func:`iyzee.scope_workflows.read_trigger_settings` parses
        out just the source field, assuming the ``EDGE,SR,<source>,...``
        shape :meth:`set_trigger_source` itself writes.
        """
        return self.query("TRIG_SELECT?")

    def get_trigger_level(self, source: Channel) -> str:
        """Return the device's raw response to a trigger-level query (see
        :meth:`query` for why this isn't parsed further)."""
        return self.query(f"{source}:TRIG_LEVEL?")

    def get_trigger_slope(self, source: Channel) -> str:
        return self.query(f"{source}:TRIG_SLOPE?")

    def get_trigger_coupling(self, source: Channel) -> str:
        return self.query(f"{source}:TRIG_COUPLING?")

    # ------------------------------------------------------------------
    # Waveform download
    # ------------------------------------------------------------------
    def _read_words(self, channel: str, block: str) -> np.ndarray:
        """Download one waveform as signed 16-bit codes (little-endian on the wire)."""
        channel, block = _ident(channel, "channel"), _ident(block, "block")
        with self._io() as inst:
            # Format and byte order must be in force *before* the waveform is
            # requested: the scope encodes the WF? reply when it executes it.
            inst.write("CFMT DEF9,WORD,BIN")
            inst.write("CORD LO")
            inst.write(f"{channel}:WF? {block}")
            raw = inst.read_raw()
        data = _definite_block(raw)
        if len(data) % 2:
            raise LeCroyProtocolError(f"odd number of waveform bytes received: {len(data)}")
        return np.frombuffer(data, dtype="<i2").astype(np.int16)

    def getDataFloatsDetailed(self, channel="C1", block="DAT1"):
        """Return calibrated waveform data together with raw ADC codes."""
        with self._lock:
            word_values = self._read_words(channel, block)
            vertical_offset = self._inspect_number(channel, "VERTICAL_OFFSET")
            vertical_gain = self._inspect_number(channel, "VERTICAL_GAIN")
            unit = self._inspect_unit(channel, "VERTUNIT")
            values = vertical_gain * word_values.astype(np.float64) - vertical_offset
            return {
                "unit": unit,
                "values": values,
                "raw_codes": word_values,
                "vertical_gain": vertical_gain,
                "vertical_offset": vertical_offset,
            }

    def getHorProperties(self, channel="C1"):
        """Return the horizontal unit, offset, and sample interval."""
        with self._lock:
            horunit = self._inspect_unit(channel, "HORUNIT")
            offset = self._inspect_number(channel, "HORIZ_OFFSET")
            interval = self._inspect_number(channel, "HORIZ_INTERVAL")
            return horunit, offset, interval
