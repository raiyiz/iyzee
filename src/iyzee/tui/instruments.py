"""Uniform instrument handles for the Connect screen.

The lab's devices are deliberately heterogeneous at the driver level —
:class:`~iyzee.mxa.KeysightMXA` and :class:`~iyzee.power.PSU` are VISA
(:class:`~iyzee.base.BaseDevice`), the wavemeter is a stateless HTTP API,
and the scope is a raw-socket legacy driver. Rather than teaching the TUI
about each of those, every device is wrapped in a small adapter that
implements the same lifecycle operations: ``connect()``, ``disconnect()``,
and ``probe()`` (a cheap call that both confirms the link is alive and returns
a short human-readable status string).

Adding a new instrument to the Connect screen is: write one adapter class
here, add one :class:`InstrumentSpec` to ``INSTRUMENTS`` below. No screen
code changes required.
All handles also expose ``.lock`` for shared serialization. Handles with a
live driver expose that driver as ``.device``; stateless adapters may return ``None``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from ..base import CH, IP
from ..mxa import KeysightMXA
from ..power import ShutterControl
from ..scope import LeCroy, LeCroyTimeoutError
from ..wavemeter_readout import WavemeterReadoutError, single_readout


class InstrumentHandle(Protocol):
    """What the Connect screen — and anything else that talks to this
    instrument, TUI or not — needs from any device adapter."""

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

    @property
    def device(self) -> Any | None:
        """The live underlying device, if this handle exposes one.

        Stateless adapters such as the wavemeter return ``None``; the
        Connect/Sweep/Scope/console paths only use handles with a live device.
        """
        ...

    @property
    def lock(self) -> threading.Lock | threading.RLock:
        """Serializes every call to this instrument's hardware, from
        whichever caller — a screen's background worker, the IPython
        console (via :class:`LockedProxy`), or a plain script holding this
        same handle directly.

        Owned by the handle, not by :class:`~iyzee.tui.app.IyzeeApp`
        (which used to keep a separate ``dict[str, threading.Lock]``
        alongside ``handles``): a handle built and connected outside any
        running app — the whole point of a script/console-first design —
        still comes with correct synchronization for free, rather than
        safety being something only the TUI happens to provide.
        """
        ...


class _LockedHandle:
    """Base for handles whose instrument access must be serialized.

    Every concrete adapter gets one lock owned by the handle itself; callers
    do not need a second lock table to coordinate access to the same device.
    """

    def __init__(self) -> None:
        # Re-entrant: a caller holding the handle lock (a workflow batch) may go
        # through a LockedProxy that takes the same lock again without deadlocking.
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        return self._lock


class _VisaHandle(_LockedHandle):
    """Adapter for any :class:`~iyzee.base.BaseDevice` (MXA, raw PSU)."""

    def __init__(self, device) -> None:
        super().__init__()
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


class ShutterHandle(_LockedHandle):
    """Adapter for :class:`~iyzee.power.ShutterControl`."""

    def __init__(self, chan: CH = CH.THREE, ip: IP = IP.POWER_SUPPLY) -> None:
        super().__init__()
        self._chan = chan
        self._ip = ip
        self._shutter: ShutterControl | None = None

    def connect(self) -> None:
        if self._shutter is None:
            self._shutter = ShutterControl(chan=self._chan, ip=self._ip)
            self._shutter.connect()

    def disconnect(self) -> None:
        if self._shutter is not None:
            self._shutter.disconnect()
            self._shutter = None

    def probe(self) -> str:
        return f"shutter ready on CH{int(self._chan)}"

    @property
    def device(self) -> ShutterControl | None:
        """The underlying live shutter controller, once connected."""
        return self._shutter

    @property
    def shutter(self) -> ShutterControl | None:
        """The live :class:`ShutterControl`, once connected."""
        return self.device


class WavemeterHandle(_LockedHandle):
    """Adapter for the wavemeter's stateless HTTP API.

    There is no persistent connection to open — ``connect()`` is a no-op,
    and ``probe()`` does one real HTTP readout to confirm the switch and
    network path are actually reachable.
    """

    def __init__(self, channel: int = 0) -> None:
        super().__init__()
        self._channel = channel

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    @property
    def device(self) -> None:
        """The wavemeter has no persistent live device object."""
        return None

    def probe(self) -> str:
        try:
            freq = single_readout(self._channel, printing=False)
        except WavemeterReadoutError as exc:
            raise ConnectionError(str(exc)) from exc
        return f"ch{self._channel} = {freq:.6f} THz"


class ScopeHandle(_LockedHandle):
    """Adapter for the legacy :class:`~iyzee.scope.LeCroy` raw-socket driver."""

    #: Seconds the scope gets to answer its first query after connecting.
    FIRST_RESPONSE_TIMEOUT = 15.0

    def __init__(self, ip: IP = IP.SCOPE) -> None:
        super().__init__()
        self._ip = ip
        self._scope = LeCroy()

    def connect(self) -> None:
        self._scope.connect(str(self._ip))

    def disconnect(self) -> None:
        self._scope.disconnect()

    def probe(self) -> str:
        """Prove the scope *answers*, not just that port 1861 accepted a TCP connection.

        The first reply after connecting can be slow, and a timeout is fatal to
        the VICP stream, so this one query gets a wider bound than steady state.
        """
        try:
            return self._scope.idn(timeout=self.FIRST_RESPONSE_TIMEOUT)
        except LeCroyTimeoutError as exc:
            raise ConnectionError(
                f"{self._ip} accepted the connection but did not answer *IDN? within "
                f"{self.FIRST_RESPONSE_TIMEOUT:g}s (is another VICP client holding the scope?)"
            ) from exc

    @property
    def lock(self) -> threading.RLock:
        """The driver's own transaction lock.

        One lock guards the scope: the handle, the console's ``LockedProxy``,
        workflow batches and the driver's compound transfers all share it, so
        there is no second lock to order against or to deadlock on.
        """
        return self._scope.transaction_lock

    @property
    def device(self) -> LeCroy:
        """The underlying live LeCroy driver."""
        return self._scope

    @property
    def scope(self) -> LeCroy:
        return self._scope


@dataclass(frozen=True)
class InstrumentSpec:
    """One row in the Connect screen: a name plus how to build its handle."""

    key: str
    label: str
    make: Callable[[], InstrumentHandle]
    # Compact name for the narrow nav rail, where the full label wraps and
    # strands the status dot on a line of its own. Defaults to ``label``.
    short: str = ""

    @property
    def short_label(self) -> str:
        return self.short or self.label

    def build(self) -> InstrumentHandle:
        return self.make()


class LockedProxy:
    """Serializes calls to a live device shared between a screen worker
    and the IPython console.

    The console is handed the same live ``mx``/``shutter``/``scope``
    objects a screen's background worker calls directly (see
    ``ipython.namespace_from_handles``). Without this, a console cell
    calling e.g. ``mx.single_sweep_wait()`` could run at the same moment
    ``SweepScreen`` is mid-sweep on that same MXA — two threads issuing
    commands to one VISA resource concurrently, which PyVISA does not
    guarantee is safe.

    Wraps a device so attribute access passes straight through, but
    calling any method acquires ``lock`` for the call's duration — the
    same lock a screen holds around its own hardware
    calls to this instrument (the owning handle's own ``.lock``; see
    :class:`InstrumentHandle`).

    Deliberately does *not* forward dunder methods such as ``__enter__``
    or ``__getitem__``: connection lifecycle belongs to the Connect
    screen, and console code reaching for ``with mx:`` would close the
    device out from under the app's own bookkeeping (the Connect screen's
    table would still say "connected" while the underlying VISA resource
    was actually closed).
    """

    def __init__(self, target: Any, lock: AbstractContextManager[Any]) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_lock", lock)

    def __getattr__(self, name: str) -> Any:
        target = object.__getattribute__(self, "_target")
        value = getattr(target, name)
        if not callable(value):
            return value
        lock = object.__getattribute__(self, "_lock")

        def _locked_call(*args: Any, **kwargs: Any) -> Any:
            with lock:
                return value(*args, **kwargs)

        return _locked_call

    def __dir__(self) -> list[str]:
        return dir(object.__getattribute__(self, "_target"))

    def __repr__(self) -> str:
        return repr(object.__getattribute__(self, "_target"))


INSTRUMENTS: list[InstrumentSpec] = [
    InstrumentSpec("mxa", "Keysight MXA", lambda: _VisaHandle(KeysightMXA()), short="MXA"),
    InstrumentSpec("shutter", "Shutter (PSU CH3)", lambda: ShutterHandle(), short="Shutter"),
    InstrumentSpec("wavemeter", "Wavemeter (WS-7)", lambda: WavemeterHandle(), short="Wavemeter"),
    InstrumentSpec("scope", "LeCroy scope [legacy]", lambda: ScopeHandle(), short="Scope"),
]
