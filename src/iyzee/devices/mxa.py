import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pyvisa
from pyvisa.constants import StatusCode

from ..config import IP, address
from .base import BaseDevice

log = logging.getLogger("iyzee.mxa")

#: A sweep gets this many times its nominal duration (sweep time x averages) before it is
#: called stuck, and never less than ``MIN_SWEEP_TIMEOUT_S``: the analyzer may also be busy
#: with something else (an automatic alignment) when the sweep is started.
SWEEP_TIMEOUT_MARGIN = 3.0
MIN_SWEEP_TIMEOUT_S = 30.0
#: ``get_errors`` gives up after this many reads rather than loop on a misbehaving queue.
MAX_ERROR_QUEUE_READS = 100


class MXAError(RuntimeError):
    """The analyzer did something other than what was asked."""


class MXATimeoutError(MXAError, TimeoutError):
    """The analyzer did not answer in time.

    By the time this is raised the session has been cleared (see
    :meth:`KeysightMXA._resync`): the late reply that is still on its way can no longer be
    mistaken for the answer to the next command. It is a ``TimeoutError`` (so an ``OSError``),
    which the experiment runner treats as worth a retry.
    """


class MXAProtocolError(MXAError):
    """A reply that cannot be what was asked for (wrong length, not numbers, not finite)."""


class KeysightMXA(BaseDevice):
    """
    PyVISA controller for Keysight X-Series MXA Signal Analyzers.
    Optimized for noise analysis workflows: trace control, markers,
    triggering synchronization, and binary data transfer.
    """

    def __init__(self, ip: str | None = None, timeout_ms: int = 5_000, resource_manager=None):
        super().__init__(
            ip=ip or address(IP.NOISE_ANALYZER),
            resource_manager=resource_manager,
            timeout_ms=timeout_ms,
            read_termination="\n",
            write_termination="\n",
        )

    def disconnect(self) -> None:
        """Backward-compatible alias for :meth:`close`."""
        self.close()

    @contextmanager
    def _io(self, what: str) -> Iterator[Any]:
        """One VISA exchange. A timeout leaves the reply queued on the analyzer, so it is
        cleared here and reported as :class:`MXATimeoutError` instead of being left to
        answer the next, unrelated, query."""
        inst = self.instrument
        if inst is None:
            raise RuntimeError("Instrument not connected")
        try:
            yield inst
        except pyvisa.errors.VisaIOError as exc:
            if exc.error_code != StatusCode.error_timeout:
                raise
            seconds = (getattr(inst, "timeout", None) or 0) / 1000
            self._resync(what)
            raise MXATimeoutError(
                f"{getattr(self, 'ip', 'the MXA')} did not answer {what} within {seconds:g}s"
            ) from exc

    def _resync(self, what: str) -> None:
        """Get the session back in step after a missed or malformed reply.

        A reply that arrives after its query timed out stays in the output queue and is
        returned by the *next* read: every later answer is then one behind, a stale ``*OPC?``
        ends the wait for a sweep that is still averaging, and trace data comes back
        half-finished with no error. A device clear empties both buffers and ``ABOR`` stops a
        sweep still running. If even that fails the link is dropped, so the app shows it as
        lost and the next run starts from a fresh connection instead of a skewed one.
        """
        inst = self.instrument
        log.warning("MXA: %s failed or was not answered; clearing the session", what)
        try:
            inst.clear()
            inst.write("ABOR")
        except Exception:  # whatever it was, the session cannot be trusted
            log.exception("MXA: could not clear the session; dropping the connection")
            self._drop()

    def _drop(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - best effort after a transport failure
            self.instrument = None

    def write(self, cmd: str):
        with self._io(cmd) as inst:
            inst.write(cmd)

    def query(self, cmd: str) -> str:
        with self._io(cmd) as inst:
            return inst.query(cmd)

    def query_binary(
        self, cmd: str, datatype: str = "f", is_big_endian: bool = True
    ) -> list[float]:
        """
        Query binary IEEE 488.2 block data.
        datatype='f' (float32) or 'd' (float64).
        """
        with self._io(cmd) as inst:
            return inst.query_binary_values(
                cmd, datatype=datatype, is_big_endian=is_big_endian, container=list
            )

    # ------------------------------------------------------------------
    # System & Synchronization
    # ------------------------------------------------------------------
    def idn(self) -> str:
        return self.query("*IDN?")

    def reset(self):
        """Factory default and clear status."""
        self.write("*RST")
        self.write("*CLS")

    def wait_opc(self, timeout_sec: float = MIN_SWEEP_TIMEOUT_S) -> bool:
        """Block until ``*OPC?`` returns 1; ``False`` if it did not come within ``timeout_sec``.

        On a timeout the session is cleared first (see :meth:`_resync`), so ``False`` also
        means "the analyzer was aborted and the link is back in step", not just "unknown".
        """
        inst = self.instrument
        if inst is None:
            raise RuntimeError("Instrument not connected")
        previous = inst.timeout
        inst.timeout = int(timeout_sec * 1000)
        try:
            return self.query("*OPC?").strip() == "1"
        except MXATimeoutError:
            return False
        finally:
            if self.instrument is inst:  # not dropped meanwhile
                inst.timeout = previous

    def get_errors(self, limit: int = MAX_ERROR_QUEUE_READS) -> list[str]:
        """Drain the SCPI error queue (at most ``limit`` reads)."""
        errs: list[str] = []
        for _ in range(limit):
            msg = self.query("SYST:ERR?")
            if msg.startswith(("+0,", "0,")):
                return errs
            errs.append(msg)
        raise MXAProtocolError(f"the error queue still was not empty after {limit} reads")

    def set_display_update(self, state: bool):
        """Disable display updates to improve remote measurement speed."""
        self.write(f"DISP:ENAB {'ON' if state else 'OFF'}")

    # ------------------------------------------------------------------
    # Frequency / Amplitude / Bandwidth
    # ------------------------------------------------------------------
    def set_center_freq(self, freq_hz: float):
        self.write(f"FREQ:CENT {freq_hz}")

    def get_center_freq(self) -> float:
        """Read the current center frequency in Hz."""
        return float(self.query("FREQ:CENT?"))

    def set_span(self, span_hz: float):
        self.write(f"FREQ:SPAN {span_hz}")

    def get_span(self) -> float:
        """Read the current frequency span in Hz."""
        return float(self.query("FREQ:SPAN?"))

    def set_start_stop(self, start_hz: float, stop_hz: float):
        self.write(f"FREQ:STAR {start_hz}")
        self.write(f"FREQ:STOP {stop_hz}")

    def set_ref_level(self, level_dbm: float):
        self.write(f"DISP:WIND:TRAC:Y:RLEV {level_dbm} dBm")

    def set_attenuation(self, att_db: float):
        """
        Set mechanical attenuation manually.
        **Manual attenuation is strongly recommended** for triggered /
        single-burst noise measurements to avoid extra acquisition cycles.
        """
        self.write(f"POW:ATT {att_db}")

    def set_attenuation_auto(self, state: bool = True):
        self.write(f"POW:ATT:AUTO {'ON' if state else 'OFF'}")

    def set_rbw(self, rbw_hz: float | None = None, auto: bool = False):
        if auto or rbw_hz is None:
            self.write("BWID:AUTO ON")
        else:
            self.write(f"BWID {rbw_hz}")

    def set_vbw(self, vbw_hz: float | None = None, auto: bool = False):
        if auto or vbw_hz is None:
            self.write("BWID:VID:AUTO ON")
        else:
            self.write(f"BWID:VID {vbw_hz}")

    def set_detector(self, detector: str):
        """
        Common detectors:
        - AVER : RMS/Average (best for noise-like signals)
        - NORM : Normal/Auto
        - POS  : Positive Peak
        - NEG  : Negative Peak
        - SAMP : Sample
        """
        self.write(f"DET {detector}")

    # ------------------------------------------------------------------
    # Sweep / Acquisition Control
    # ------------------------------------------------------------------
    def set_sweep_duration(self, time: int):
        self.write(f"SWE:TIME {time}ms")

    def get_sweep_duration_ms(self) -> int:
        """Read the sweep duration in milliseconds (SCPI reports seconds)."""
        return round(float(self.query("SWE:TIME?")) * 1_000)

    def set_sweep_points(self, points: int):
        self.write(f"SWE:POIN {points}")

    def get_sweep_points(self) -> int:
        return int(self.query("SWE:POIN?"))

    def set_continuous_sweep(self, state: bool):
        self.write(f"INIT:CONT {'ON' if state else 'OFF'}")

    def initiate_sweep(self):
        """Start a sweep immediately (use with INIT:CONT OFF)."""
        self.write("INIT:IMM")

    def abort_sweep(self):
        self.write("ABOR")

    def sweep_timeout_s(self) -> float:
        """How long one single sweep may take before it is called stuck.

        ``SWEEP_TIMEOUT_MARGIN`` times what the analyzer is set to do (sweep time x
        averages), but never less than ``MIN_SWEEP_TIMEOUT_S``. A fixed limit would fail every
        point of a long configuration, at the same place each time.
        """
        try:
            nominal = float(self.query("SWE:TIME?")) * max(1, int(float(self.query("AVER:COUN?"))))
        except ValueError:  # an unreadable reply is not a reason to refuse to measure
            return MIN_SWEEP_TIMEOUT_S
        return max(MIN_SWEEP_TIMEOUT_S, SWEEP_TIMEOUT_MARGIN * nominal)

    def single_sweep_wait(self, timeout_sec: float | None = None) -> bool:
        """
        Set single sweep, trigger once, and wait for completion.

        ``timeout_sec`` defaults to :meth:`sweep_timeout_s`. ``False`` means it did not
        finish in time; the analyzer has then been aborted and the session cleared.
        """
        if timeout_sec is None:
            timeout_sec = self.sweep_timeout_s()
        self.set_continuous_sweep(False)
        self.initiate_sweep()
        return self.wait_opc(timeout_sec)

    # ------------------------------------------------------------------
    # Trace Operations
    # ------------------------------------------------------------------
    def get_frequency_axis(self) -> list[float]:
        """Return frequency value for each trace point (linear sweep)."""
        start = float(self.query("FREQ:STAR?"))
        stop = float(self.query("FREQ:STOP?"))
        pts = self.get_sweep_points()
        if pts <= 1:
            return [start]
        step = (stop - start) / (pts - 1)
        return [start + i * step for i in range(pts)]

    def set_average_count(self, count: int):
        self.write(f"AVER:COUN {count}")

    def get_average_count(self) -> int:
        """Read the configured trace-average count."""
        return round(float(self.query("AVER:COUN?")))

    def set_average_type(self, avg_type: str):
        """
        avg_type: RMS (power), LOG (log-power), SCAL (voltage).
        Use RMS for true noise power averaging.
        """
        self.write(f"AVER:TYPE {avg_type}")

    def get_average_type(self) -> str:
        """Read the averaging type (RMS, LOG, or SCAL)."""
        return self.query("AVER:TYPE?").strip().upper()

    # -- Trace Control (suffix syntax) --------------------------------
    def set_trace_mode(self, trace_num: int | str, mode: str):
        self.write(f":TRACe{trace_num}:TYPE {mode}")

    def set_trace_update(self, trace_num, state: bool = True):
        self.write(f":TRACe{trace_num}:UPDATE {'ON' if state else 'OFF'}")

    def set_trace_display(self, trace_num, state: bool = True):
        self.write(f":TRACe{trace_num}:DISPLAY {'ON' if state else 'OFF'}")

    def clear_trace(self, trace_num: int):
        self.write(f":TRACe{trace_num}:CLEar")

    def get_trace_data(self, trace_num: int = 1, binary: bool = True) -> list[float]:
        """Read one trace; its length must be the analyzer's point count.

        A reply of the wrong length or not made of numbers means the session is out of step
        (a stale reply from an earlier command); it is cleared and
        :class:`MXAProtocolError` raised rather than returning data that belongs elsewhere.
        """
        expected = self.get_sweep_points()
        command = f":TRACe:DATA? TRACe{trace_num}"
        if binary:
            self.write("FORMat:DATA REAL,32")
            self.write("FORMat:BORDer NORM")
            values = self.query_binary(command, datatype="f", is_big_endian=True)
        else:
            self.write("FORMat:DATA ASCii")
            reply = self.query(command)
            try:
                values = [float(x) for x in reply.split(",")]
            except ValueError:
                self._resync(command)
                raise MXAProtocolError(f"{command} answered {reply[:60]!r}, not numbers") from None
        # A trace has several points. Requiring that also catches a session shifted by one
        # reply, where the length query and the trace query would otherwise agree with each other.
        if expected < 2 or len(values) != expected:
            self._resync(command)
            raise MXAProtocolError(
                f"{command} returned {len(values)} point(s) but the analyzer has {expected}"
            )
        return values

    # ------------------------------------------------------------------
    # Marker Control
    # ------------------------------------------------------------------
    def set_marker_state(self, marker: int, state: bool):
        """marker: 1-12."""
        self.write(f"CALC:MARK{marker}:STAT {'ON' if state else 'OFF'}")

    def set_marker_mode(self, marker: int, mode: str):
        """
        mode: POS, DELT, BAND, NOIS.
        **NOIS** returns noise density in dBm/Hz.
        """
        self.write(f"CALC:MARK{marker}:MODE {mode}")

    def set_marker_x(self, marker: int, freq_hz: float):
        self.write(f"CALC:MARK{marker}:X {freq_hz}")

    def get_marker_x(self, marker: int) -> float:
        return float(self.query(f"CALC:MARK{marker}:X?"))

    def get_marker_y(self, marker: int) -> float:
        return float(self.query(f"CALC:MARK{marker}:Y?"))

    def configure_noise_marker(self, marker: int = 1, freq_hz: float = 0.0):
        """Place a noise density marker (dBm/Hz)."""
        self.set_marker_state(marker, True)
        self.set_marker_mode(marker, "NOIS")
        if freq_hz > 0:
            self.set_marker_x(marker, freq_hz)

    def configure_band_power_marker(
        self, marker: int, center_hz: float, left_hz: float, right_hz: float
    ):
        """Integrated power marker with left/right offsets."""
        self.set_marker_state(marker, True)
        self.set_marker_mode(marker, "BAND")
        self.set_marker_x(marker, center_hz)
        self.write(f"CALC:MARK{marker}:FUNC:BAND:LEFT {left_hz}")
        self.write(f"CALC:MARK{marker}:FUNC:BAND:RIGH {right_hz}")
        self.write(f"CALC:MARK{marker}:FUNC:BAND:STAT ON")

    def get_band_power(self, marker: int) -> float:
        return float(self.query(f"CALC:MARK{marker}:Y?"))

    # ------------------------------------------------------------------
    # Triggering
    # ------------------------------------------------------------------
    def set_trigger_source(self, source: str):
        """
        source: IMM (FreeRun), VID (Video), EXT (External),
                RFB (RFBurst), FRAM (Frame).
        **Avoid RFBurst for non-repetitive / single-burst signals**;
        use VID or EXT instead.
        """
        self.write(f"TRIG:SOUR {source}")

    def get_trigger_source(self) -> str:
        """Read the configured trigger source."""
        return self.query("TRIG:SOUR?").strip().upper()

    def set_trigger_level_video(self, level_dbm: float):
        self.write(f"TRIG:LEV:VID {level_dbm}")

    def set_trigger_level_external(self, level_v: float):
        self.write(f"TRIG:LEV:EXT {level_v}")

    def set_trigger_slope(self, slope: str):
        """slope: POS or NEG."""
        self.write(f"TRIG:SLOP {slope}")

    def wait_for_trigger_ready(
        self, timeout_sec: float = 5.0, poll_interval_sec: float = 0.01
    ) -> bool:
        """
        Poll the Operation Status Register until the instrument is armed
        and **waiting for trigger** (bit 5, value 32).
        Essential for synchronizing single-burst or DUT-triggered captures.
        """
        self.write("STAT:OPER:ENAB 32")
        self.query("STAT:OPER:EVEN?")  # clear
        t0 = time.time()
        while (time.time() - t0) < timeout_sec:
            try:
                status = int(self.query("STAT:OPER:EVEN?"))
                if status & 32:
                    return True
            except ValueError:
                pass
            time.sleep(poll_interval_sec)
        return False
