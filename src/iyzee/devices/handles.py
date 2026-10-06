"""Uniform lifecycle adapters for the lab's devices.

The drivers are deliberately heterogeneous: :class:`~iyzee.devices.mxa.KeysightMXA`
and :class:`~iyzee.devices.power.PSU` are VISA
(:class:`~iyzee.devices.base.BaseDevice`), the wavemeter is a stateless HTTP API,
and the scope is a raw-socket driver. Each is wrapped in a small handle with the
same operations: ``connect()``, ``disconnect()``, ``probe()`` (a cheap call that
confirms the link is alive and returns a short status string), ``alive``,
``device`` and a ``lock`` that serializes every call to that instrument.

Nothing here knows about the TUI: scripts, the console and the TUI all go
through the same handles (see :class:`iyzee.lab.Lab`). :class:`LockedProxy`
hands a device out with every method call taking that lock.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from contextlib import AbstractContextManager
from typing import Any, Protocol

from ..config import IP, address
from .base import CH
from .power import ShutterControl
from .scope import LeCroy, LeCroyTimeoutError
from .wavemeter import Wavemeter

log = logging.getLogger("iyzee.instruments")


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
        """The underlying device or client exposed by this handle.

        Stateless adapters such as the wavemeter still expose their client;
        there is simply no persistent connection behind it.
        """
        ...

    @property
    def alive(self) -> bool:
        """Whether the link is still believed usable. No instrument I/O; cheap.

        ``False`` once the driver has dropped the connection (a timeout, the
        peer closing it, a keepalive failure). Adapters that cannot tell
        report ``True``. The app polls this so a lost link is shown as lost
        instead of staying "connected" until the next command fails.
        """
        ...

    @property
    def lock(self) -> threading.Lock | threading.RLock:
        """Serializes every call to this instrument's hardware, from
        whichever caller — a screen's background worker, the IPython
        console (via :class:`LockedProxy`), or a plain script holding this
        same handle directly.

        Owned by the handle, not by :class:`~iyzee.lab.Lab`
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

    @property
    def alive(self) -> bool:
        # Overridden by adapters whose driver can tell (see ScopeHandle).
        return True


class _VisaHandle(_LockedHandle):
    """Adapter for any :class:`~iyzee.devices.base.BaseDevice` (MXA, raw PSU)."""

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
        """The wrapped device (e.g. a live :class:`~iyzee.devices.mxa.KeysightMXA`)."""
        return self._device


class ShutterHandle(_LockedHandle):
    """Adapter for :class:`~iyzee.devices.power.ShutterControl`."""

    def __init__(self, chan: CH = CH.THREE, ip: str | None = None) -> None:
        super().__init__()
        self._chan = chan
        self._ip = ip or address(IP.POWER_SUPPLY)
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


class WavemeterHandle(_LockedHandle):
    """Adapter for the wavemeter's stateless HTTP API.

    There is no persistent connection to open. The handle provides the shared
    instrument lock, performs one real readout when probing, and hands the
    console a stateless :class:`~iyzee.devices.wavemeter.Wavemeter` client.
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = Wavemeter()

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    @property
    def device(self) -> Wavemeter:
        """The HTTP client (no connection behind it, so always available)."""
        return self._client

    def probe(self) -> str:
        freq = self._client.read_frequency()
        return f"wavemeter = {freq:.6f} THz"


class ScopeHandle(_LockedHandle):
    """Adapter for the legacy :class:`~iyzee.devices.scope.LeCroy` raw-socket driver."""

    #: Seconds the scope gets to answer its first query after connecting.
    FIRST_RESPONSE_TIMEOUT = 15.0

    def __init__(self, ip: str | None = None) -> None:
        super().__init__()
        self._ip = ip or address(IP.SCOPE)
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
        started = time.monotonic()
        try:
            identity = self._scope.idn(timeout=self.FIRST_RESPONSE_TIMEOUT)
        except LeCroyTimeoutError as exc:
            raise ConnectionError(
                f"{self._ip} accepted the connection but did not answer *IDN? within "
                f"{self.FIRST_RESPONSE_TIMEOUT:g}s (is another VICP client holding the scope?)"
            ) from exc
        log.info("scope answered *IDN? after %.2fs", time.monotonic() - started)
        return identity

    @property
    def alive(self) -> bool:
        return self._scope.check_link()

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

        @functools.wraps(value)  # keep name/signature/docstring: `lab.mx.method?` must still help
        def _locked_call(*args: Any, **kwargs: Any) -> Any:
            with lock:
                return value(*args, **kwargs)

        return _locked_call

    def __dir__(self) -> list[str]:
        return dir(object.__getattribute__(self, "_target"))

    def __repr__(self) -> str:
        return repr(object.__getattribute__(self, "_target"))
