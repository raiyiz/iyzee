"""A VISA-level stand-in for the Keysight MXA that behaves like the real transport.

The hand-written fakes elsewhere in the suite answer every query at once and never
time out, so they cannot show the failures that matter on the bench. This one models the
part of a message-based VISA session that does:

* the instrument has **one output queue**, first in, first out;
* a query whose reply is not ready within ``timeout`` raises a VISA timeout, but **the
  reply stays queued** and is delivered, late, to whoever reads next;
* ``clear()`` (VXI-11 device clear) empties the queue and aborts the running sweep.

Time is virtual: waiting costs no wall-clock time, so a 30 s ``*OPC?`` timeout is
instant in a test.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import pyvisa.errors
from pyvisa.constants import StatusCode


@dataclass
class VirtualClock:
    now: float = 0.0


@dataclass
class SimulatedMXA:
    """What ``pyvisa`` hands out for ``TCPIP0::<ip>::inst0::INSTR``, as far as the app cares."""

    sweep_s: float = 0.010  # SWE:TIME
    averages: int = 150  # AVER:COUN
    points: int = 1001  # SWE:POIN
    #: Extra seconds the analyzer is busy at the Nth ``INIT:IMM`` (0-based), e.g. an
    #: automatic alignment: ``{2: 45.0}`` stalls the third acquisition for 45 s.
    stalls: dict[int, float] = field(default_factory=dict)
    clock: VirtualClock = field(default_factory=VirtualClock)

    timeout: int = 5000  # ms, as pyvisa's ``resource.timeout``
    read_termination: str | None = None
    write_termination: str | None = None

    def __post_init__(self) -> None:
        self.commands: list[str] = []
        self.queue: deque[tuple[float, str]] = deque()  # (ready_at, reply)
        self.busy_until = 0.0
        self.inits = 0
        self.cleared = 0
        self.closed = False

    # -- the pyvisa surface the driver uses -------------------------------------------------

    def write(self, command: str) -> None:
        self._check_open()
        self.commands.append(command)
        self._execute(command)

    def read(self) -> str:
        self._check_open()
        if not self.queue:
            self.clock.now += self.timeout / 1000
            raise pyvisa.errors.VisaIOError(StatusCode.error_timeout)
        ready_at, reply = self.queue[0]
        if ready_at - self.clock.now > self.timeout / 1000:
            self.clock.now += self.timeout / 1000
            raise pyvisa.errors.VisaIOError(StatusCode.error_timeout)  # reply stays queued
        self.queue.popleft()
        self.clock.now = max(self.clock.now, ready_at)
        return reply

    def query(self, command: str) -> str:
        self.write(command)
        return self.read()

    def query_binary_values(self, command: str, **kwargs: object) -> list[float]:
        return [float(x) for x in self.query(command).split(",")]

    def clear(self) -> None:
        self.cleared += 1
        self.queue.clear()
        self.busy_until = self.clock.now

    def close(self) -> None:
        self.closed = True

    # -- the instrument ---------------------------------------------------------------------

    def _check_open(self) -> None:
        if self.closed:
            raise pyvisa.errors.InvalidSession

    def _reply(self, text: str, *, after: float | None = None) -> None:
        self.queue.append((self.clock.now if after is None else after, text))

    def _trace(self) -> str:
        return _trace_text(self.inits, self.busy_until <= self.clock.now, self.points)

    def _execute(self, command: str) -> None:
        upper = command.strip().upper()
        if upper == "INIT:IMM":
            start = max(self.clock.now, self.busy_until)
            self.busy_until = start + self.averages * self.sweep_s + self.stalls.get(self.inits, 0)
            self.inits += 1
        elif upper == "ABOR":
            self.busy_until = self.clock.now
        elif upper == "*OPC?":
            self._reply("1", after=max(self.clock.now, self.busy_until))
        elif upper.startswith("SWE:TIME ") and not upper.endswith("?"):
            self.sweep_s = _seconds(upper.split(maxsplit=1)[1])
        elif upper.startswith("AVER:COUN ") and not upper.endswith("?"):
            self.averages = int(upper.split()[1])
        elif upper == "SWE:TIME?":
            self._reply(f"{self.sweep_s:.6E}")
        elif upper == "AVER:COUN?":
            self._reply(str(self.averages))
        elif upper == "SWE:POIN?":
            self._reply(str(self.points))
        elif upper == "SYST:ERR?":
            self._reply('+0,"No error"')
        elif upper == "*IDN?":
            self._reply("Keysight Technologies,N9020B,SIM,A.00")
        elif upper.startswith(":TRACE") and upper.endswith(("TRACE1", "TRACE2")):
            self._reply(self._trace())
        # everything else (configuration writes) is accepted silently


def _trace_text(sweep: int, complete: bool, points: int) -> str:
    # The first sample says which sweep this is and whether it had finished averaging, so a
    # test can tell a good trace from a stale or half-averaged one: -70 - sweep, then .5 if partial.
    first = -70.0 - sweep - (0.0 if complete else 0.5)
    return ",".join(f"{first + 0.001 * i:.3f}" for i in range(points))


def _seconds(text: str) -> float:
    text = text.strip().lower()
    for suffix, scale in (("ms", 1e-3), ("us", 1e-6), ("s", 1.0)):
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * scale
    return float(text)
