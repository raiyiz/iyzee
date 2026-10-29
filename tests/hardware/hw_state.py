"""Snapshot / restore helpers for the real-hardware tests.

A test that changes an instrument must leave it as it found it. These helpers read a
fixed list of settings before the test and write them back afterwards. The lists are
deliberately explicit (not "everything"): they cover what ``prepare_analyzer`` and the
sweeps touch, which is what the tests change.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from iyzee.devices.power import PSU


@dataclass(frozen=True)
class Setting:
    """One instrument setting: how to read it and how to write that reading back."""

    name: str
    query: str
    restore: str  # a command template; ``{}`` is replaced by the query's reply


def _trace_settings(trace: int) -> list[Setting]:
    return [
        Setting(f"trace{trace}_type", f":TRAC{trace}:TYPE?", f":TRAC{trace}:TYPE {{}}"),
        Setting(f"trace{trace}_display", f":TRAC{trace}:DISP?", f":TRAC{trace}:DISP {{}}"),
        Setting(f"trace{trace}_update", f":TRAC{trace}:UPD?", f":TRAC{trace}:UPD {{}}"),
    ]


#: Order matters on restore: a manual value first, then its ``AUTO`` flag (writing a
#: manual value switches auto off, so the flag has to come after), ``INIT:CONT`` last.
MXA_SETTINGS: tuple[Setting, ...] = (
    Setting("center", "FREQ:CENT?", "FREQ:CENT {}"),
    # Zero span conflicts with auto-coupled sweep time. Force manual mode before
    # restoring the span, then restore the original auto flag after the time value.
    Setting("sweep_time_auto_off", "SWE:TIME:AUTO?", "SWE:TIME:AUTO OFF"),
    Setting("span", "FREQ:SPAN?", "FREQ:SPAN {}"),
    Setting("rbw", "BWID?", "BWID {}"),
    Setting("rbw_auto", "BWID:AUTO?", "BWID:AUTO {}"),
    Setting("vbw", "BWID:VID?", "BWID:VID {}"),
    Setting("vbw_auto", "BWID:VID:AUTO?", "BWID:VID:AUTO {}"),
    Setting("sweep_time", "SWE:TIME?", "SWE:TIME {}"),
    Setting("sweep_time_auto", "SWE:TIME:AUTO?", "SWE:TIME:AUTO {}"),
    Setting("attenuation", "POW:ATT?", "POW:ATT {}"),
    Setting("attenuation_auto", "POW:ATT:AUTO?", "POW:ATT:AUTO {}"),
    Setting("average_count", "AVER:COUN?", "AVER:COUN {}"),
    Setting("average_type", "AVER:TYPE?", "AVER:TYPE {}"),
    Setting("trigger_source", "TRIG:SOUR?", "TRIG:SOUR {}"),
    *_trace_settings(1),
    *_trace_settings(2),
    Setting("continuous", "INIT:CONT?", "INIT:CONT {}"),
)


def snapshot(query: Callable[[str], str], settings: Sequence[Setting]) -> dict[str, str]:
    """Read every setting; the replies are kept verbatim (stripped) for writing back."""
    return {setting.name: query(setting.query).strip() for setting in settings}


def restore(
    write: Callable[[str], object],
    settings: Sequence[Setting],
    values: dict[str, str],
) -> list[str]:
    """Write the snapshot back. Returns one message per setting that could not be written.

    Never stops at the first failure: restoring as much as possible matters more than
    reporting the first problem. The caller decides how loudly to report the rest.
    """
    failures: list[str] = []
    for setting in settings:
        try:
            write(setting.restore.format(values[setting.name]))
        except Exception as exc:  # noqa: BLE001 - collected and reported by the caller
            failures.append(f"{setting.name}: {type(exc).__name__}: {exc}")
    return failures


# -- the PSU behind the shutter -------------------------------------------------------------


def psu_query(psu: PSU, command: str) -> str:
    """Query the PSU's raw socket.

    ``PSU.open`` returns a raw SOCKET resource, which has no default read termination;
    without one a query would wait for the VISA timeout instead of reading the reply.
    """
    instrument = psu.instrument
    if instrument is None:
        raise RuntimeError("PSU is not connected")
    instrument.read_termination = "\n"
    return str(instrument.query(command)).strip()


def psu_output_is_on(psu: PSU, channel: int) -> bool:
    """Whether output ``channel`` is switched on (for the shutter: whether it is open)."""
    instrument = psu.instrument
    if instrument is None:
        raise RuntimeError("PSU is not connected")
    instrument.write(f"INST OUT{channel}")
    return psu_query(psu, "OUTP:SEL?") == "1"
