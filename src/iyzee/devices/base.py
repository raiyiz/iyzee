"""Shared device infrastructure for laboratory instruments."""

from collections.abc import Callable
from typing import Any

import pyvisa

from ..config import IP

SHUTTER_CHANNEL_DEFAULT = 3


class InstrumentError(RuntimeError):
    """The instrument reported an error, or answered in a way that cannot be trusted."""


class BaseDevice:
    """Common VISA connection handling for laboratory instruments.

    **I/O failure policy** (one for every driver): after a failed exchange the reply stream
    may be out of step with the commands (a late answer would be handed to the *next*
    query, and every later reply would then be shifted by one). So a failed exchange never
    leaves a possibly-desynchronised link in use: :meth:`_exchange` drops it, so the device
    reads as not connected (``connected`` is False and calls raise "not connected") until
    it is connected again. A timeout that is a normal outcome (a sweep that is slow) is the
    one case a driver may handle itself, through :meth:`_resync`, which must either prove
    the stream is back in step or drop the link.
    """

    def __init__(
        self,
        ip: IP | str | None = None,
        resource_manager=None,
        timeout_ms: int = 10_000,
        read_termination: str | None = None,
        write_termination: str | None = None,
    ):
        self.ip = ip
        self.timeout_ms = timeout_ms
        self.read_termination = read_termination
        self.write_termination = write_termination
        self.rm = resource_manager or pyvisa.ResourceManager()
        self.instrument: Any = None
        self.visa_address = f"TCPIP0::{self.ip}::inst0::INSTR"

    def open(self):
        return self.rm.open_resource(self.visa_address)

    def connect(self) -> None:
        """Open the device once; repeated calls reuse the existing resource."""
        if self.instrument is not None:
            return
        self.instrument = self.open()
        self.instrument.timeout = self.timeout_ms
        if self.read_termination is not None:
            self.instrument.read_termination = self.read_termination
        if self.write_termination is not None:
            self.instrument.write_termination = self.write_termination

    @property
    def connected(self) -> bool:
        """Whether the link is open and has not been dropped by a failed exchange."""
        return self.instrument is not None

    def _drop(self) -> None:
        """Close the link after a transport failure; best effort, never raises."""
        try:
            self.close()
        except Exception:  # noqa: BLE001 - a link that already failed may fail to close too
            self.instrument = None

    def _exchange[T](self, call: Callable[[Any], T]) -> T:
        """Run one VISA exchange on the open resource; a VISA failure drops the link."""
        inst = self.instrument
        if inst is None:
            raise RuntimeError("Instrument not connected")
        try:
            return call(inst)
        except pyvisa.errors.VisaIOError:
            self._drop()
            raise

    def _resync(self) -> bool:
        """Get the reply stream back in step after a timeout, or drop the link.

        Returns True only if the stream is known to be in step. The default cannot know, so
        it drops the link; a driver overrides this with a check that proves it.
        """
        self._drop()
        return False

    def close(self) -> None:
        """Close the VISA resource, if it is open."""
        if self.instrument is not None:
            self.instrument.close()
            self.instrument = None

    def __enter__(self):
        """Open the device at the explicit resource boundary."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the VISA resource when leaving the context."""
        self.close()
        return False


__all__ = ["IP", "BaseDevice"]
