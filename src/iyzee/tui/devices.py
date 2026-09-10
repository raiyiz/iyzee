"""Thread-safe ownership of hardware handles used by the TUI."""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from collections.abc import Iterator

from ..mxa import KeysightMXA
from ..power import ShutterControl


class DeviceManager:
    """Own lazily-created devices and serialize access from worker threads.

    Device objects are created and connected inside Textual worker threads,
    so potentially blocking PyVISA setup never runs on the UI event loop.
    The same lock is held for an entire experiment run to prevent two worker
    threads from talking to the same VISA resource concurrently.
    """

    def __init__(self) -> None:
        self._mxa: KeysightMXA | None = None
        self._shutter: ShutterControl | None = None
        self._lock = RLock()

    @property
    def mxa_connected(self) -> bool:
        return self._mxa is not None and self._mxa.instrument is not None

    @property
    def shutter_connected(self) -> bool:
        return self._shutter is not None and self._shutter.psu.instrument is not None

    def connect_mxa(self) -> KeysightMXA:
        with self._lock:
            if self._mxa is None:
                self._mxa = KeysightMXA()
            self._mxa.connect()
            return self._mxa

    def connect_shutter(self) -> ShutterControl:
        with self._lock:
            if self._shutter is None:
                self._shutter = ShutterControl()
            elif self._shutter.psu.instrument is None:
                self._shutter.psu.connect()
            return self._shutter

    @contextmanager
    def mxa_for_operation(self) -> Iterator[KeysightMXA]:
        """Hold the hardware lock while an MXA operation is in progress."""
        with self._lock:
            yield self.connect_mxa()

    @contextmanager
    def frequency_sweep_devices(self) -> Iterator[tuple[KeysightMXA, ShutterControl]]:
        """Hold the hardware lock while a frequency sweep is in progress."""
        with self._lock:
            yield self.connect_mxa(), self.connect_shutter()

    def close_all(self) -> None:
        """Close all currently-open devices, best-effort and idempotently."""
        with self._lock:
            if self._shutter is not None:
                try:
                    self._shutter.close()
                finally:
                    self._shutter.psu.close()
                self._shutter = None

            if self._mxa is not None:
                self._mxa.close()
                self._mxa = None
