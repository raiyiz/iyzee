import math
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

    def open(self):
        port = 5025
        resource = self.rm.open_resource(f"TCPIP::{self.ip}::{port}::SOCKET")
        # A raw socket has no message framing of its own. Without this a query would wait for
        # the VISA timeout instead of returning at the end of the reply line.
        resource.read_termination = "\n"
        return resource

    def _query(self, command: str) -> str:
        return str(self.instrument.query(command)).strip()

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

    def set_voltage(self, voltage: float, channel: int):
        self.instrument.write(f"INST:NSEL {channel}")
        self.instrument.write(f"VOLT {voltage}")

    def enable_output(self, channel: int):
        self.instrument.write(f"INST OUT{channel}")
        self.instrument.write("OUTP:SEL 1")

    def disable_output(self, channel: int):
        self.instrument.write(f"INST OUT{channel}")
        self.instrument.write("OUTP:SEL 0")

    def set_current(self, current: float, channel: int):
        self.instrument.write(f"INST:NSEL {channel}")
        self.instrument.write(f"CURR {current}")

    def enable_global_output(self):
        self.instrument.write("OUTP:GEN 1")

    def disable_global_output(self):
        self.instrument.write("OUTP:GEN 0")


class ShutterControl:
    """Control the optical shutter connected to a PSU channel."""

    def __init__(
        self,
        chan: int = SHUTTER_CHANNEL_DEFAULT,
        ip: str | None = None,
        resource_manager=None,
    ):
        self.psu = PSU(ip=ip or address(IP.POWER_SUPPLY), resource_manager=resource_manager)
        self.chan = chan

    def connect(self):
        """Connect the PSU and configure the shutter channel."""
        if self.psu.instrument is not None:
            return
        try:
            self.psu.connect()
            self.psu.set_voltage(1.7, self.chan)
            self.psu.set_current(0.01, self.chan)
        except Exception:
            self.psu.close()
            raise

    def disconnect(self):
        """Close the shutter, then release the PSU connection."""
        try:
            if self.psu.instrument is not None:
                self.close()
        finally:
            self.psu.close()

    def __enter__(self):
        """Connect and return the shutter controller for use in a managed context."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the shutter and release the underlying PSU connection."""
        self.disconnect()
        return False

    def retrieve_state(self) -> PSUState:
        """The supply's own report, limited to the shutter's channel."""
        return self.psu.retrieve_state([self.chan])

    def is_open(self) -> bool:
        """Whether the shutter's channel is powered, according to the supply (not our memory)."""
        return self.retrieve_state().is_live(self.chan)

    def open(self):
        self.psu.enable_output(self.chan)

    def close(self):
        self.psu.disable_output(self.chan)
