import socket
import struct
from contextlib import contextmanager
from enum import StrEnum
from typing import Iterator

import numpy as np

from .vicp import VICPProtocolError, VICPTimeoutError, VICPTransport, recv_exact


class LeCroyTimeoutError(VICPTimeoutError):
    """The scope didn't respond (or accept data) within the configured
    socket timeout.

    Before a socket timeout was actually applied (see ``LeCroy.connect``),
    a dead IP, a black-holed connection, or an instrument that simply
    stopped answering mid-transfer would all hang the calling thread
    forever instead of raising anything. This is a ``TimeoutError``
    subclass — exactly what ``socket.timeout`` already is as of Python
    3.10 — so any existing ``except TimeoutError``/``except OSError``/
    ``except Exception`` handler still catches it; the point of a
    dedicated subclass is giving callers something specific to catch, and
    a message with real numbers in it (how long, how far into the
    transfer) instead of a bare, contextless timeout.
    """


class Channel(StrEnum):
    """Analog input channel identifiers, as used in a command's header path
    (e.g. ``C1:VOLT_DIV 0.5``)."""

    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"


class MathChannel(StrEnum):
    """Math-function trace identifiers.

    F5-F8 exist on some models (in addition to F1-F4) but aren't covered
    here; pass their name as a plain string to the methods below if needed
    — they all take ``Channel | MathChannel | str``.
    """

    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    F4 = "F4"


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
    "LeCroyTimeoutError",
    "MathChannel",
    "TriggerCoupling",
    "TriggerMode",
    "TriggerSlope",
    "VICPProtocolError",
    "VICPTransport",
]


class LeCroy:
    """
    Class for remote control and download of LeCroy oscilloscope data
    tested for WaveSurfer 452
    Methods
    ------------
    connect(IP) : after initializing to connect
    disconnect() : to end communication
    send(message) : message to device (commands, etc.)
    readAll() : read a full framed response from the device, returns ascii string
    query(message) : send(message) + readAll(), returns the trimmed response text

    getDataBytes(channel="C1", block="DAT1"): binary data download, 8-bit
    getDataWords(channel="C1", block="DAT1"): binary data download, 16-bit
    getDataFloats(channel="C1", block="DAT1"): unit, vertical data (downloads 16-bit binary)
    getHorProperties(channel="C1") : returns (unit, offset, interval) in time dir.

    Also provides channel (vertical), trigger, and math-function control —
    see the "Channel", "Trigger", and "Math" sections below. Those commands
    follow the classic LeCroy/Teledyne LeCroy IEEE-488.2-style command set
    shared by the WaveSurfer, WaveAce, and X-Stream families (documented in
    Teledyne LeCroy's Remote Control Command Reference manuals), the same
    family this driver already targets for waveform download (its
    ``INSPECT?``-based methods above use that exact dialect). They have not
    been exercised against real hardware in this environment, only checked
    against that documentation, so verify against your instrument (many
    commands accept a ``?`` query form to read back what was just set) before
    relying on them for anything safety-critical.
    """

    MAX_TCP_CONNECT = 5  # time in s. to get a conn
    MAC_TCP_READ = 3  # time in s. to wait for the DSO to respond
    LECROY_SERVER_PORT = 1861  # as defined by LeCroy
    CMD_BUF_LEN = 8192
    LECROY_EOI_FLAG = 0x01
    LECROY_DATA_FLAG = 0x80

    def __init__(self):
        self._transport = VICPTransport(
            port=self.LECROY_SERVER_PORT,
            connect_timeout=self.MAX_TCP_CONNECT,
            io_timeout=self.MAC_TCP_READ,
            max_command_length=self.CMD_BUF_LEN,
            timeout_error=LeCroyTimeoutError,
        )

    @property
    def connected(self) -> bool:
        return self._transport.connected

    @property
    def CONNECTED(self) -> bool:
        """Compatibility alias for the historical all-caps state attribute."""
        return self.connected

    @property
    def address(self) -> str | None:
        return self._transport.address

    @property
    def SOCK_TIMEOUT(self) -> float:
        return self._transport.io_timeout

    @property
    def s(self) -> socket.socket | object | None:
        """Compatibility access to the underlying socket for existing fakes."""
        return self._transport.socket

    @s.setter
    def s(self, sock: object) -> None:
        self._transport.attach_socket(sock)

    @staticmethod
    def _recv_exact(sock: object, num_bytes: int) -> bytes:
        return recv_exact(sock, num_bytes, timeout_error=LeCroyTimeoutError)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize a complete logical operation on the Scope connection."""
        with self._transport.transaction():
            yield

    def connect(self, IP, delayval=None, connect_timeout=None):
        """Connect to the IP with bounded handshake and I/O timeouts."""
        if self.connected:
            print("Already connected!")
            return -2

        delayval = self.MAC_TCP_READ if delayval is None else delayval
        connect_timeout = self.MAX_TCP_CONNECT if connect_timeout is None else connect_timeout
        self._transport.connect(
            IP,
            connect_timeout=connect_timeout,
            io_timeout=delayval,
        )

    def disconnect(self):
        """Disconnect from the Scope and clear connection state."""
        if not self.connected:
            return -2
        self._transport.close()

    def send(self, message):
        """Send one VICP command frame."""
        self._transport.send_command(message)

    def readAll(self):
        """Read all response frames through EOI and return ``(flags, text)``."""
        flag, data = self._transport.read_message()
        try:
            return flag, data.decode("ascii")
        except UnicodeDecodeError as exc:
            raise VICPProtocolError("VICP response was not valid ASCII") from exc

    def query(self, message: str) -> str:
        """Send ``message`` and atomically return the trimmed response text."""
        return self._transport.query(message)

    # ------------------------------------------------------------------
    # Channel (vertical) control
    # ------------------------------------------------------------------
    def set_volts_per_div(self, channel: Channel, volts_per_div: float) -> None:
        """Set the vertical scale for ``channel``, in volts/division."""
        self.send(f"{channel}:VOLT_DIV {volts_per_div}")

    def set_offset(self, channel: Channel, offset_volts: float) -> None:
        """Set the vertical offset for ``channel``, in volts."""
        self.send(f"{channel}:OFFSET {offset_volts}")

    def set_coupling(self, channel: Channel, coupling: Coupling) -> None:
        """Set the input coupling and termination impedance for ``channel``.

        Note this is the channel's own vertical coupling — the signal path
        that gets displayed and digitized. To couple the *trigger* path
        instead (which may be a different channel, and can differ from its
        own vertical coupling), use :meth:`set_trigger_coupling`.
        """
        self.send(f"{channel}:COUPLING {coupling}")

    def set_attenuation(self, channel: Channel, factor: float) -> None:
        """Tell the scope the probe attenuation factor on ``channel`` (e.g.
        1, 10, or 100), so its vertical readings are scaled correctly."""
        self.send(f"{channel}:ATTENUATION {factor}")

    def set_bandwidth_limit(self, channel: Channel, limit: str) -> None:
        """Set the bandwidth limit for ``channel``.

        ``limit`` is a free string, not an enum: ``"OFF"`` and ``"ON"`` are
        universal, but the specific reduced-bandwidth values it accepts
        (e.g. ``"20MHZ"``, ``"200MHZ"``, ``"25MHZ"``) are model dependent.
        Check ``<channel>:BANDWIDTH_LIMIT?`` on the instrument, or its
        datasheet, for the values it actually supports.
        """
        self.send(f"{channel}:BANDWIDTH_LIMIT {limit}")

    def set_trace_display(self, channel: Channel | MathChannel | str, state: bool) -> None:
        """Show or hide ``channel``'s trace on the display.

        Works for an analog channel or a math trace (anything with a
        header-path prefix), which is why this accepts ``Channel |
        MathChannel`` rather than only ``Channel``.
        """
        self.send(f"{channel}:TRACE {'ON' if state else 'OFF'}")

    def set_invert(self, channel: Channel | MathChannel | str, state: bool) -> None:
        """Invert (or un-invert) ``channel``'s waveform."""
        self.send(f"{channel}:INVERT_SET {'ON' if state else 'OFF'}")

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

    def get_trace_display(self, channel: Channel | MathChannel | str) -> str:
        return self.query(f"{channel}:TRACE?")

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
        self.send(f"{source}:TRIG_LEVEL {level_volts}")

    def set_trigger_slope(self, source: Channel, slope: TriggerSlope) -> None:
        self.send(f"{source}:TRIG_SLOPE {slope}")

    def set_trigger_coupling(self, source: Channel, coupling: TriggerCoupling) -> None:
        """Set the coupling of the trigger path fed by ``source``.

        Distinct from that channel's own vertical coupling — see
        :meth:`set_coupling`.
        """
        self.send(f"{source}:TRIG_COUPLING {coupling}")

    def set_trigger_delay(self, delay_seconds: float) -> None:
        """Position the trigger point in time relative to the acquisition.

        A negative value delays the trigger point (showing more pre-trigger
        data); a positive value shows less pre-trigger data, or none.
        """
        self.send(f"TRIG_DELAY {delay_seconds}")

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
    # Math function control
    # ------------------------------------------------------------------
    def set_math_equation(self, math_channel: MathChannel, equation: str) -> None:
        """Define what ``math_channel`` computes.

        ``equation`` is written in the instrument's own expression syntax,
        e.g. ``"C1-C2"`` for a difference, ``"AVG(C1)"`` for averaging,
        ``"FFT(C1)"`` for a spectrum. The exact set of supported operators
        and functions (and their exact spelling) is model and firmware
        dependent — see your instrument's math chapter, or read back
        ``<math_channel>:DEFINE?`` after setting one up on the front panel to
        see the syntax it produces for a given operation. This method only
        forwards the string; :meth:`set_math_difference`,
        :meth:`set_math_average`, and :meth:`set_math_fft` are thin
        convenience wrappers around the three operations mentioned above.
        """
        self.send(f"{math_channel}:DEFINE EQN,'{equation}'")

    def set_math_difference(
        self, math_channel: MathChannel, minuend: Channel, subtrahend: Channel
    ) -> None:
        """``math_channel`` = ``minuend`` - ``subtrahend``."""
        self.set_math_equation(math_channel, f"{minuend}-{subtrahend}")

    def set_math_average(self, math_channel: MathChannel, source: Channel) -> None:
        """``math_channel`` = a running average of ``source``."""
        self.set_math_equation(math_channel, f"AVG({source})")

    def set_math_fft(self, math_channel: MathChannel, source: Channel) -> None:
        """``math_channel`` = the FFT (spectrum) of ``source``."""
        self.set_math_equation(math_channel, f"FFT({source})")

    def get_math_equation(self, math_channel: MathChannel) -> str:
        return self.query(f"{math_channel}:DEFINE?")

    def getDataBytes(self, channel="C1", block="DAT1"):
        """Return waveform samples as signed 8-bit values."""
        with self.transaction():
            self.send("CFMT DEF9,BYTE,BIN")
            self.send(f"{channel}:WF? {block}")
            self._recv_exact(self.s, 38)
            data = self._transport.read_data_until_eoi()
            return list(struct.iter_unpack("b", data))

    def getDataWords(self, channel="C1", block="DAT1"):
        """Return waveform samples as signed 16-bit values."""
        with self.transaction():
            self.send("CFMT DEF9,WORD,BIN")
            self.send(f"{channel}:WF? {block}")
            self.send("CORD LO")
            rethead = self._recv_exact(self.s, 38)

            if rethead[-11:-9] != b"#9":
                raise RuntimeError("incorrectly returned header")
            try:
                exp_bytes = int(rethead[-9:].decode("ascii"))
            except ValueError as exc:
                raise VICPProtocolError("invalid binary waveform block length") from exc
            if exp_bytes % 2:
                raise VICPProtocolError(f"odd number of waveform bytes expected: {exp_bytes}")

            data = self._transport.read_data_until_eoi()
            if len(data) != exp_bytes:
                raise VICPProtocolError(f"Expected {exp_bytes} bytes, got {len(data)}")
            return struct.unpack(f"<{len(data) // 2}h", data)

    def getDataFloatsDetailed(self, channel="C1", block="DAT1"):
        """Return calibrated waveform data together with raw ADC codes."""
        with self.transaction():
            word_values = np.array(
                self.getDataWords(channel=channel, block=block), dtype=np.int16
            )
            self.send(f'{channel}:INSPECT? "VERTICAL_OFFSET"')
            _r1, r2 = self.readAll()
            vertical_offset = float(r2.split(":")[-1].split('"\n')[0].strip(" "))
            self.send(f'{channel}:INSPECT? "VERTICAL_GAIN"')
            _r1, r2 = self.readAll()
            vertical_gain = float(r2.split(":")[-1].split('"\n')[0].strip(" "))
            self.send(f'{channel}:INSPECT? "VERTUNIT"')
            _r1, r2 = self.readAll()
            unit = r2.split("Unit Name = ")[-1].split('"\n')[0]
            values = vertical_gain * word_values.astype(np.float64) - vertical_offset
            return {
                "unit": unit,
                "values": values,
                "raw_codes": word_values,
                "vertical_gain": vertical_gain,
                "vertical_offset": vertical_offset,
            }

    def getDataFloats(self, channel="C1", block="DAT1"):
        """Return one waveform in engineering units as ``(unit, values)``.

        The detailed acquisition path is shared with scientific recording so
        callers never need to download the same waveform twice just to retain
        calibration metadata.
        """
        data = self.getDataFloatsDetailed(channel=channel, block=block)
        return data["unit"], data["values"]

    def getHorProperties(self, channel="C1"):
        """Return the horizontal unit, offset, and sample interval."""
        with self.transaction():
            self.send(f'{channel}:INSPECT? "HORUNIT"')
            _r1, r2 = self.readAll()
            horunit = r2.split("Unit Name = ")[-1].split('"\n')[0]

            self.send(f'{channel}:INSPECT? "HORIZ_OFFSET"')
            _r1, r2 = self.readAll()
            offset = float(r2.split(":")[-1].split('"\n')[0].strip(" "))

            self.send(f'{channel}:INSPECT? "HORIZ_INTERVAL"')
            _r1, r2 = self.readAll()
            interval = float(r2.split(":")[-1].split('"\n')[0].strip(" "))

            return horunit, offset, interval

