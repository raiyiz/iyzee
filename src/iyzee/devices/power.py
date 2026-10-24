import math
import typing
from collections.abc import Iterable
from dataclasses import dataclass

from ..config import IP, address
from .base import SHUTTER_CHANNEL_DEFAULT, BaseDevice

#: The HMP4040 has four channels.
CHANNELS = (1, 2, 3, 4)

#: What one HMP4040 channel can be asked for (32 V, 10 A; not both at once). Callers that take
#: numbers from a person use these to refuse a typo before it reaches the instrument.
VOLTAGE_MAX = 32.0
CURRENT_MAX = 10.0


@dataclass(frozen=True)
class ChannelState:
    """What the supply itself reports for one channel (nothing here is remembered by us)."""

    channel: int
    voltage_set: float  # V, the setpoint
    current_set: float  # A, the current limit
    voltage_measured: float  # V, at the terminals
    current_measured: float  # A, drawn right now
    selected: bool  # ``OUTP:SEL``: the channel is armed to follow the master output


@dataclass(frozen=True)
class PSUState:
    """A snapshot of the supply: the master output and the channels asked for."""

    general_output: bool  # ``OUTP:GEN``: the master output switch
    channels: tuple[ChannelState, ...]

    def channel(self, number: int) -> ChannelState:
        for state in self.channels:
            if state.channel == number:
                return state
        raise KeyError(
            f"channel {number} was not retrieved (have {[c.channel for c in self.channels]})"
        )

    def is_live(self, number: int) -> bool:
        """Whether channel ``number`` is actually powered: armed *and* the master output is on."""
        return self.general_output and self.channel(number).selected


class PSU(BaseDevice):
    """Rohde & Schwarz HMP4040 power-supply controller."""

    def __init__(
        self,
        ip: str | None = None,
        resource_manager: typing.Any = None,
        timeout_ms: int = 10_000,
    ) -> None:
        super().__init__(
            ip=ip or address(IP.POWER_SUPPLY),
            resource_manager=resource_manager,
            timeout_ms=timeout_ms,
        )

    def open(self) -> typing.Any:
        port = 5025
        resource: typing.Any = self.rm.open_resource(f"TCPIP::{self.ip}::{port}::SOCKET")
        # A raw socket has no message framing of its own. Without this a query would wait for
        # the VISA timeout instead of returning at the end of the reply line.
        resource.read_termination = "\n"
        return resource

    def _query(self, command: str) -> str:
        return str(self.instrument.query(command)).strip()

    def idn(self) -> str:
        """The supply's identification string (also proves it answers)."""
        return self._query("*IDN?")

    def _number(self, command: str) -> float:
        reply = self._query(command)
        try:
            value = float(reply)
        except ValueError:
            value = math.nan
        if not math.isfinite(value):
            raise ValueError(f"PSU answered {reply!r} to {command!r}, expected a number")
        return value

    def _flag(self, command: str) -> bool:
        reply = self._query(command)
        if reply not in ("0", "1"):
            raise ValueError(f"PSU answered {reply!r} to {command!r}, expected 0 or 1")
        return reply == "1"

    def retrieve_state(self, channels: Iterable[int] = CHANNELS) -> PSUState:
        """Ask the supply what it is doing: setpoints, measurements, and what is switched on.

        Reading a channel means selecting it, so the channel that was selected is selected
        again afterwards. A reply that is not what it should be raises ``ValueError``
        naming the command; it never becomes a number in the result.
        """
        wanted = tuple(channels)
        if bad := [n for n in wanted if n not in CHANNELS]:
            raise ValueError(f"channels must be in {CHANNELS}, got {bad}")
        general = self._flag("OUTP:GEN?")
        previously_selected = int(self._number("INST:NSEL?"))
        try:
            states = []
            for number in wanted:
                self.instrument.write(f"INST:NSEL {number}")
                states.append(
                    ChannelState(
                        channel=number,
                        voltage_set=self._number("VOLT?"),
                        current_set=self._number("CURR?"),
                        voltage_measured=self._number("MEAS:VOLT?"),
                        current_measured=self._number("MEAS:CURR?"),
                        selected=self._flag("OUTP:SEL?"),
                    )
                )
        finally:
            self.instrument.write(f"INST:NSEL {previously_selected}")
        return PSUState(general_output=general, channels=tuple(states))

    def set_voltage(self, voltage: float, channel: int) -> None:
        self.instrument.write(f"INST:NSEL {channel}")
        self.instrument.write(f"VOLT {voltage}")

    def enable_output(self, channel: int) -> None:
        self.instrument.write(f"INST OUT{channel}")
        self.instrument.write("OUTP:SEL 1")

    def disable_output(self, channel: int) -> None:
        self.instrument.write(f"INST OUT{channel}")
        self.instrument.write("OUTP:SEL 0")

    def set_current(self, current: float, channel: int) -> None:
        self.instrument.write(f"INST:NSEL {channel}")
        self.instrument.write(f"CURR {current}")

    def enable_global_output(self) -> None:
        self.instrument.write("OUTP:GEN 1")

    def disable_global_output(self) -> None:
        self.instrument.write("OUTP:GEN 0")


class ShutterControl:
    """The optical shutter: one channel of a :class:`PSU`, which it borrows.

    The supply is the instrument; the shutter is what is plugged into one of its channels.
    So this class neither opens nor closes the PSU's connection and holds no lock of its
    own: whoever owns the PSU (the lab session, a ``with PSU() as psu:`` block) decides
    when it connects, and the shutter's operations are just channel operations on it.
    Constructing a ``ShutterControl`` sends nothing; :meth:`configure` sets the channel up.

    ::

        with PSU() as psu:
            shutter = ShutterControl(psu)
            shutter.configure()
            shutter.open()
            ...
            shutter.close()
    """

    #: The shutter's trigger levels, hard-coded rather than configurable.
    VOLTAGE = 1.7  # V
    CURRENT_LIMIT = 0.01  # A

    def __init__(self, psu: PSU, chan: int = SHUTTER_CHANNEL_DEFAULT):
        if chan not in CHANNELS:
            raise ValueError(f"the shutter channel must be in {CHANNELS}, got {chan}")
        self.psu = psu
        self.chan = chan

    def configure(self) -> None:
        """Set the shutter's channel to its trigger levels. The PSU must be connected."""
        if self.psu.instrument is None:
            raise ConnectionError("the shutter is a channel of the PSU, which is not connected")
        self.psu.set_voltage(self.VOLTAGE, self.chan)
        self.psu.set_current(self.CURRENT_LIMIT, self.chan)

    def retrieve_state(self) -> PSUState:
        """The supply's own report, limited to the shutter's channel."""
        return self.psu.retrieve_state([self.chan])

    def is_open(self) -> bool:
        """Whether the shutter's channel is powered, according to the supply (not our memory)."""
        return self.retrieve_state().is_live(self.chan)

    def open(self) -> typing.Any:
        self.psu.enable_output(self.chan)

    def close(self) -> None:
        self.psu.disable_output(self.chan)
