"""Uniform instrument handles for the Connect screen.

The lab's devices are deliberately heterogeneous at the driver level —
:class:`~iyzee.mxa.KeysightMXA` and :class:`~iyzee.power.PSU` are VISA
(:class:`~iyzee.base.BaseDevice`), the wavemeter is a stateless HTTP API,
and the scope is a raw-socket legacy driver. Rather than teaching the TUI
about each of those, every device is wrapped in a small adapter that
implements the same three operations: ``connect()``, ``disconnect()``, and
``probe()`` (a cheap call that both confirms the link is alive and returns
a short human-readable status string).

Adding a new instrument to the Connect screen is: write one adapter class
here, add one :class:`InstrumentSpec` to ``INSTRUMENTS`` below. No screen
code changes required.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..base import CH, IP
from ..mxa import KeysightMXA
from ..power import ShutterControl
from ..scope import LeCroy
from ..wavemeter_readout import WavemeterReadoutError, single_readout


class InstrumentHandle(Protocol):
    """What the Connect screen needs from any device adapter."""

    def connect(self) -> None:
        """Open the link. Raises on failure."""
        ...

    def disconnect(self) -> None:
        """Close the link. Safe to call even if never connected."""
        ...

    def probe(self) -> str:
        """Return a short status string proving the link is alive.

        Called once right after ``connect()`` succeeds, so it doubles as
        the "did this actually work" check for devices (like the PSU) that
        will happily open a socket to nothing in particular.
        """
        ...


class _VisaHandle:
    """Adapter for any :class:`~iyzee.base.BaseDevice` (MXA, raw PSU)."""

    def __init__(self, device) -> None:
        self._device = device

    def connect(self) -> None:
        self._device.connect()

    def disconnect(self) -> None:
        self._device.close()

    def probe(self) -> str:
        idn = getattr(self._device, "idn", None)
        if idn is not None:
            return idn()
        return f"connected @ {self._device.ip}"

    @property
    def device(self):
        """The wrapped device (e.g. a live :class:`~iyzee.mxa.KeysightMXA`)."""
        return self._device


class ShutterHandle:
    """Adapter for :class:`~iyzee.power.ShutterControl`.

    ``ShutterControl.__init__`` opens the PSU connection eagerly (it has
    to, to set the shutter's trigger voltage/current), so ``connect()``
    here is really "construct it" and ``disconnect()`` releases the PSU.
    """

    def __init__(self, chan: CH = CH.THREE, ip: IP = IP.POWER_SUPPLY) -> None:
        self._chan = chan
        self._ip = ip
        self._shutter: ShutterControl | None = None

    def connect(self) -> None:
        self._shutter = ShutterControl(chan=self._chan, ip=self._ip)

    def disconnect(self) -> None:
        if self._shutter is not None:
            self._shutter.psu.close()
            self._shutter = None

    def probe(self) -> str:
        return f"shutter ready on CH{int(self._chan)}"

    @property
    def shutter(self) -> ShutterControl | None:
        """The live :class:`ShutterControl`, once connected."""
        return self._shutter


class WavemeterHandle:
    """Adapter for the wavemeter's stateless HTTP API.

    There is no persistent connection to open — ``connect()`` is a no-op,
    and ``probe()`` does one real HTTP readout to confirm the switch and
    network path are actually reachable.
    """

    def __init__(self, channel: int = 0) -> None:
        self._channel = channel

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def probe(self) -> str:
        try:
            freq = single_readout(self._channel, printing=False)
        except WavemeterReadoutError as exc:
            raise ConnectionError(str(exc)) from exc
        return f"ch{self._channel} = {freq:.6f} THz"


class ScopeHandle:
    """Adapter for the legacy :class:`~iyzee.scope.LeCroy` raw-socket driver.

    This driver predates :class:`~iyzee.base.BaseDevice` and has no
    ``*IDN?``-style query, so ``probe()`` can only report that the TCP
    handshake succeeded, not identify the instrument.
    """

    def __init__(self, ip: IP = IP.SCOPE) -> None:
        self._ip = ip
        self._scope = LeCroy()

    def connect(self) -> None:
        self._scope.connect(str(self._ip))

    def disconnect(self) -> None:
        self._scope.disconnect()

    def probe(self) -> str:
        return f"socket connected @ {self._ip}"

    @property
    def scope(self) -> LeCroy:
        return self._scope


@dataclass(frozen=True)
class InstrumentSpec:
    """One row in the Connect screen: a name plus how to build its handle."""

    key: str
    label: str
    make: Callable[[], InstrumentHandle]

    def build(self) -> InstrumentHandle:
        return self.make()


INSTRUMENTS: list[InstrumentSpec] = [
    InstrumentSpec("mxa", "Keysight MXA", lambda: _VisaHandle(KeysightMXA())),
    InstrumentSpec("shutter", "Shutter (PSU CH3)", lambda: ShutterHandle()),
    InstrumentSpec("wavemeter", "Wavemeter (WS-7)", lambda: WavemeterHandle()),
    InstrumentSpec("scope", "LeCroy scope [legacy]", lambda: ScopeHandle()),
]
