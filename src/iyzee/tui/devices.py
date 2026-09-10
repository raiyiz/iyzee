"""Thread-safe ownership of hardware handles used by the TUI."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from ..mxa import KeysightMXA
from ..power import ShutterControl
from ..scope import LeCroy


class DeviceManager:
    """Own lazily-created devices and serialize access from worker threads."""

    def __init__(self) -> None:
        self._mxa: KeysightMXA | None = None
        self._shutter: ShutterControl | None = None
        self._scope: LeCroy | None = None
        self._lock = RLock()

    @property
    def mxa_connected(self) -> bool:
        return self._mxa is not None and self._mxa.instrument is not None

    @property
    def shutter_connected(self) -> bool:
        return self._shutter is not None and self._shutter.psu.instrument is not None

    @property
    def scope_connected(self) -> bool:
        return self._scope is not None and self._scope.connected

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

    def connect_scope(self) -> LeCroy:
        with self._lock:
            if self._scope is None:
                self._scope = LeCroy()
            self._scope.connect()
            return self._scope

    @contextmanager
    def mxa_for_operation(self) -> Iterator[KeysightMXA]:
        """Hold the hardware lock while an MXA operation is in progress."""
        with self._lock:
            yield self.connect_mxa()

    @contextmanager
    def scope_for_operation(self) -> Iterator[LeCroy]:
        """Hold the hardware lock while a scope operation is in progress."""
        with self._lock:
            if self._scope is None or not self._scope.connected:
                raise RuntimeError("LeCroy scope is not connected")
            yield self._scope

    @contextmanager
    def frequency_sweep_devices(self) -> Iterator[tuple[KeysightMXA, ShutterControl]]:
        """Hold the hardware lock while a frequency sweep is in progress."""
        with self._lock:
            yield self.connect_mxa(), self.connect_shutter()

    def close_scope(self) -> None:
        """Close and forget the LeCroy scope, if present."""
        with self._lock:
            if self._scope is not None:
                self._scope.close()
                self._scope = None

    def close_all(self) -> None:
        """Close all currently-open devices, best-effort and idempotently."""
        with self._lock:
            errors: list[Exception] = []

            if self._scope is not None:
                try:
                    self._scope.close()
                except Exception as exc:
                    errors.append(exc)
                finally:
                    self._scope = None

            if self._shutter is not None:
                try:
                    self._shutter.close()
                except Exception as exc:
                    errors.append(exc)
                finally:
                    self._shutter.psu.close()
                self._shutter = None

            if self._mxa is not None:
                try:
                    self._mxa.close()
                except Exception as exc:
                    errors.append(exc)
                finally:
                    self._mxa = None

            if errors:
                raise errors[0]
