"""The lab session: which instruments exist, which are connected, and their locks.

``Lab`` is the core object the TUI, the console and plain scripts share. It owns
the connected handles (see :mod:`iyzee.devices.handles`), so connecting,
disconnecting, noticing a dead link and closing everything on exit are
implemented once, without Textual::

    lab = Lab()
    lab.connect("scope")            # opens, probes, registers; raises on failure
    scope = lab.device("scope")     # the driver; take lab["scope"].lock around calls
    lab.close_all()

Adding an instrument: write a handle in ``devices/handles.py`` and add one
:class:`InstrumentSpec` to ``INSTRUMENTS`` below. No screen code changes.

Some "instruments" are really a part of another: the shutter is one channel of the power
supply. Such a spec names its ``parent``; its handle is built from the parent's handle (and
shares its link and lock), connecting it connects the parent first if needed, disconnecting
the parent disconnects it first, and on exit it is closed before the parent, so the
shutter is never left open by the supply going away.

Guide: :guide:`arch-lab`
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .devices.handles import (
    InstrumentHandle,
    ScopeHandle,
    ShutterHandle,
    WavemeterHandle,
    _VisaHandle,
)
from .devices.mxa import KeysightMXA
from .devices.power import PSU

log = logging.getLogger("iyzee.lab")


@dataclass(frozen=True)
class InstrumentSpec:
    """One row in the Connect screen: a name plus how to build its handle.

    ``make()`` builds the handle; for a spec with a ``parent`` it is called with the
    parent's connected handle instead (``make(parent_handle)``).
    """

    key: str
    label: str
    make: Callable[..., InstrumentHandle]
    # Compact name for the narrow nav rail, where the full label wraps and
    # strands the status dot on a line of its own. Defaults to ``label``.
    short: str = ""
    # Key of the instrument this one is a part of (see the module docstring).
    parent: str = ""


INSTRUMENTS: list[InstrumentSpec] = [
    InstrumentSpec("mxa", "Keysight MXA", lambda: _VisaHandle(KeysightMXA()), short="MXA"),
    InstrumentSpec("psu", "Power supply (HMP4040)", lambda: _VisaHandle(PSU()), short="PSU"),
    InstrumentSpec(
        "shutter",
        "Shutter (PSU CH3)",
        lambda psu: ShutterHandle(psu),
        short="Shutter",
        parent="psu",
    ),
    InstrumentSpec("wavemeter", "Wavemeter (WS-7)", lambda: WavemeterHandle(), short="Wavemeter"),
    InstrumentSpec("scope", "LeCroy scope [legacy]", lambda: ScopeHandle(), short="Scope"),
]


class NotConnectedError(LookupError):
    """An instrument was asked for that is not (or no longer) connected."""


class Lab:
    """Connected instruments of one session, with their lifecycle."""

    def __init__(self, specs: list[InstrumentSpec] | None = None) -> None:
        self.specs = specs if specs is not None else INSTRUMENTS
        #: key -> handle. "In here" means "connected and usable".
        self.handles: dict[str, InstrumentHandle] = {}
        #: key -> the probe's status string from when it connected (for display).
        self.details: dict[str, str] = {}
        self._key_locks: dict[str, threading.RLock] = {}
        self._key_locks_guard = threading.Lock()

    def _key_lock(self, key: str) -> threading.RLock:
        """One re-entrant lock per instrument key: a connect of it, and of anything that
        needs it as a parent, never run twice at once (two sockets to one supply)."""
        with self._key_locks_guard:
            return self._key_locks.setdefault(key, threading.RLock())

    def _parent_of(self, key: str) -> str:
        return next((spec.parent for spec in self.specs if spec.key == key), "")

    def dependents(self, key: str) -> list[str]:
        """Keys of the connected instruments that are parts of ``key``."""
        return [s.key for s in self.specs if s.parent == key and s.key in self.handles]

    # -- lookup -----------------------------------------------------------

    def spec(self, key: str) -> InstrumentSpec:
        for spec in self.specs:
            if spec.key == key:
                return spec
        raise KeyError(f"unknown instrument {key!r}; known: {[s.key for s in self.specs]}")

    @property
    def connected(self) -> tuple[str, ...]:
        return tuple(self.handles)

    def __getitem__(self, key: str) -> InstrumentHandle:
        try:
            return self.handles[key]
        except KeyError:
            raise NotConnectedError(f"{key} is not connected") from None

    def device(self, key: str) -> Any:
        """The live driver/client of a connected instrument."""
        device = self[key].device
        if device is None:
            raise NotConnectedError(f"{key} is connected but exposes no device")
        return device

    # -- lifecycle --------------------------------------------------------

    def connect(self, spec: InstrumentSpec | str) -> str:
        """Build, open and probe an instrument, then register it.

        Returns the probe's status string. On any failure the half-open link is
        closed and the exception propagates; nothing is registered. An instrument that
        is a part of another (``spec.parent``) connects that one first if it is not.
        Connecting what is already connected (say the supply, which connecting the shutter
        just did) opens nothing a second time and returns the status it connected with.
        """
        spec = self.spec(spec) if isinstance(spec, str) else spec
        with self._key_lock(spec.key):
            if spec.key in self.handles:
                return self.details.get(spec.key, "")
            parent: InstrumentHandle | None = None
            if spec.parent:
                with self._key_lock(spec.parent):
                    if spec.parent not in self.handles:
                        self.connect(spec.parent)
                parent = self[spec.parent]
            handle = spec.make(parent) if parent is not None else spec.make()
            with handle.lock:
                try:
                    handle.connect()
                    detail = handle.probe()
                except Exception:
                    # connect() may have half-succeeded (or probe() failed after it
                    # did): close rather than leak a link nothing references.
                    with contextlib.suppress(Exception):
                        handle.disconnect()
                    raise
            self.handles[spec.key] = handle
            self.details[spec.key] = detail
        log.info("connected %s (%s)", spec.key, detail)
        return detail

    def disconnect(self, key: str) -> None:
        """Unregister and close one instrument (a no-op if it isn't connected).

        Instruments that are parts of it go first (the shutter is closed before the
        supply is released). A failure closing one of them does not stop the rest.
        """
        for child in self.dependents(key):
            try:
                self.disconnect(child)
            except Exception:  # the parent must still be released
                log.exception("error closing %s", child)
        handle = self.handles.pop(key, None)
        self.details.pop(key, None)
        if handle is not None:
            with handle.lock:
                handle.disconnect()
        log.info("disconnected %s", key)

    def drop_dead_links(self) -> list[str]:
        """Unregister every handle whose link has died; return their keys.

        Cheap (``alive`` does no instrument I/O). Handles without the attribute
        count as alive. What is left of a dead link is released on a background
        thread, because the driver's disconnect takes the instrument lock.
        """
        lost = []
        for key, handle in list(self.handles.items()):
            if getattr(handle, "alive", True):
                continue
            if self.handles.get(key) is handle:
                del self.handles[key]
                self.details.pop(key, None)
                lost.append(key)
                log.warning("%s: connection lost; dropped it so it can be reconnected", key)
                threading.Thread(
                    target=self._release_dead_link,
                    args=(key, handle),
                    name=f"release-{key}",
                    daemon=True,
                ).start()
        return lost

    @staticmethod
    def _release_dead_link(key: str, handle: InstrumentHandle) -> None:
        try:
            with handle.lock:
                handle.disconnect()
        except Exception:  # the link is already dead; just don't leak
            log.debug("releasing dead link %s failed", key, exc_info=True)

    def close_all(self, timeout: float = 5.0) -> None:
        """Disconnect every instrument within ``timeout``, parts before their parents.

        Instruments at the same level close in parallel. Each disconnect takes that
        instrument's lock first, so it waits for an in-flight sweep step or console call
        instead of tearing the link down under it. An instrument still busy at the
        deadline is skipped (and logged) rather than blocking the exit; the OS reclaims
        its sockets when the process ends anyway. Parts go first because closing the
        shutter needs the supply's link, and must not be skipped by the supply closing.
        """
        handles = dict(self.handles)
        self.handles.clear()
        self.details.clear()
        if not handles:
            return
        deadline = time.monotonic() + timeout

        def depth(key: str) -> int:
            level, parent = 0, self._parent_of(key)
            while parent and level < 8:
                level, parent = level + 1, self._parent_of(parent)
            return level

        def close(key: str, handle: InstrumentHandle) -> None:
            lock = handle.lock
            if not lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                log.warning("shutdown: %s still busy after %.0fs, not disconnecting", key, timeout)
                return
            try:
                handle.disconnect()
            except Exception:  # one bad instrument mustn't stop the rest
                log.exception("shutdown: error closing %s", key)
            finally:
                lock.release()

        for level in sorted({depth(key) for key in handles}, reverse=True):
            threads = [
                threading.Thread(target=close, args=item, name=f"close-{item[0]}", daemon=True)
                for item in handles.items()
                if depth(item[0]) == level
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(max(0.0, deadline - time.monotonic()))
