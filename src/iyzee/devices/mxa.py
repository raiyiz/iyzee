import logging
import time

import pyvisa
from pyvisa.constants import StatusCode

from ..config import IP, address
from .base import BaseDevice, InstrumentError

log = logging.getLogger("iyzee.devices")

#: Most entries ``SYST:ERR?`` is asked for before the queue is declared not to drain.
ERROR_QUEUE_LIMIT = 100
#: VISA timeout for the check that proves the link is back in step after a timeout.
RESYNC_TIMEOUT_MS = 2_000


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

    def write(self, cmd: str):
        self._exchange(lambda inst: inst.write(cmd))

    def query(self, cmd: str) -> str:
        return self._exchange(lambda inst: inst.query(cmd))

    def query_binary(
        self, cmd: str, datatype: str = "f", is_big_endian: bool = True
    ) -> list[float]:
        """
        Query binary IEEE 488.2 block data.
        datatype='f' (float32) or 'd' (float64).
        """
        return self._exchange(
            lambda inst: inst.query_binary_values(
                cmd, datatype=datatype, is_big_endian=is_big_endian, container=list
            )
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

    def wait_opc(self, timeout_sec: float = 30.0) -> bool:
        """Block until *OPC? returns 1; False if it did not within ``timeout_sec``.

        A timeout leaves the ``*OPC?`` answer pending, which would be read as the answer to
        the next query. So after one the link is resynchronised (see :meth:`_resync`) or
        dropped before this returns False. Any other I/O failure drops the link and raises.
        """
        inst = self.instrument
        if inst is None:
            raise RuntimeError("Instrument not connected")
        old_timeout = inst.timeout
        inst.timeout = int(timeout_sec * 1000)
        try:
            try:
                return str(inst.query("*OPC?")).strip() == "1"
            except pyvisa.errors.VisaIOError as exc:
                if exc.error_code != StatusCode.error_timeout:
                    self._drop()
                    raise
                self._resync()
                return False
        finally:
            if self.instrument is inst:  # not if it was dropped meanwhile
                inst.timeout = old_timeout

    def _resync(self) -> bool:
        """Prove the link is back in step after a timed-out ``*OPC?``, else drop it.

        Device clear empties the analyzer's input buffer and output queue (this discards the
        stale answer); ``ABOR`` stops a sweep that is still running, which would otherwise
        hold up the check; and an ``*OPC?`` that answers ``1`` shows commands and replies
        pair up again.
        """
        inst = self.instrument
        try:
            if inst is None:
                return False
            inst.clear()
            inst.write("ABOR")
            inst.timeout = RESYNC_TIMEOUT_MS
            if str(inst.query("*OPC?")).strip() == "1":
                return True
        except Exception:
            log.warning(
                "MXA did not resynchronise after a timeout; dropping the link", exc_info=True
            )
        self._drop()
        return False

    def get_errors(self, limit: int = ERROR_QUEUE_LIMIT) -> list[str]:
        """Drain the SCPI error queue (oldest first).

        Raises :class:`InstrumentError` if the queue does not report empty within ``limit``
        entries or a reply is not ``<code>,<message>``: an analyzer answering like that is
        not one to keep polling.
        """
        errors: list[str] = []
        for _ in range(limit + 1):
            msg = str(self.query("SYST:ERR?")).strip()
            try:
                code = int(msg.split(",", 1)[0])
            except ValueError:
                raise InstrumentError(f"unreadable reply to SYST:ERR?: {msg!r}") from None
            if code == 0:
                return errors
            errors.append(msg)
        raise InstrumentError(f"the error queue did not drain within {limit} entries")

    def check_errors(self, context: str = "") -> None:
        """Raise :class:`InstrumentError` if the analyzer has queued any error.

        SCPI setters do not fail: a rejected value just queues an error and the instrument
        carries on with the old setting. This is how a caller finds out.
        """
        if errors := self.get_errors():
            where = f" while {context}" if context else ""
            raise InstrumentError(f"the analyzer reported {'; '.join(errors)}{where}")

    def read_back(self) -> dict[str, float | str]:
        """The settings the analyzer is actually using (it may round what was requested)."""
        return {
            "rbw_hz": float(self.query("BWID?")),
            "vbw_hz": float(self.query("BWID:VID?")),
            "sweep_time_s": float(self.query("SWE:TIME?")),
            "avg_count": int(float(self.query("AVER:COUN?"))),
            "avg_type": str(self.query("AVER:TYPE?")).strip(),
        }

    def set_display_update(self, state: bool):
        """Disable display updates to improve remote measurement speed."""
        self.write(f"DISP:ENAB {'ON' if state else 'OFF'}")

    # ------------------------------------------------------------------
    # Frequency / Amplitude / Bandwidth
    # ------------------------------------------------------------------
    def set_center_freq(self, freq_hz: float):
        self.write(f"FREQ:CENT {freq_hz}")

    def set_span(self, span_hz: float):
        self.write(f"FREQ:SPAN {span_hz}")

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

    def single_sweep_wait(self, timeout_sec: float = 30.0) -> bool:
        """
        Set single sweep, trigger once, and wait for completion.
        """
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

    def set_average_type(self, avg_type: str):
        """
        avg_type: RMS (power), LOG (log-power), SCAL (voltage).
        Use RMS for true noise power averaging.
        """
        self.write(f"AVER:TYPE {avg_type}")

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
        if binary:
            self.write("FORMat:DATA REAL,32")
            self.write("FORMat:BORDer NORM")
            return self.query_binary(
                f":TRACe:DATA? TRACe{trace_num}",
                datatype="f",
                is_big_endian=True,
            )
        self.write("FORMat:DATA ASCii")
        resp = self.query(f":TRACe:DATA? TRACe{trace_num}")
        return [float(x) for x in resp.split(",")]

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
