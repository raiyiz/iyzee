import time
from typing import ClassVar

import pyvisa

from iyzee import BaseDevice, IP


class KeysightMXA(BaseDevice):
    """
    PyVISA controller for Keysight X-Series MXA Signal Analyzers.
    Optimized for noise analysis workflows: trace control, markers,
    triggering synchronization, and binary data transfer.
    """

    TRACE_IDS: ClassVar[list[str]] = [
        "TRACE1",
        "TRACE2",
        "TRACE3",
        "TRACE4",
        "TRACE5",
        "TRACE6",
    ]
    TRACE_MODES: ClassVar[dict[str, str]] = {
        "WRIT": "Write",
        "MAXH": "MaxHold",
        "MINH": "MinHold",
        "AVER": "Average",
        "VIEW": "View",
        "BLAN": "Blank",
    }
    MARKER_MODES: ClassVar[dict[str, str]] = {
        "POS": "Normal",
        "DELT": "Delta",
        "BAND": "BandPower",
        "NOIS": "Noise",
    }
    TRIG_SOURCES: ClassVar[dict[str, str]] = {
        "IMM": "FreeRun",
        "VID": "Video",
        "EXT": "External",
        "RFB": "RFBurst",
        "FRAM": "Frame",
    }

    def __init__(self, ip: IP = IP.NOISE_ANALYZER, timeout_ms: int = 5_000, resource_manager=None):
        super().__init__(
            ip=ip,
            resource_manager=resource_manager,
            timeout_ms=timeout_ms,
            read_termination="\n",
            write_termination="\n",
        )

    def disconnect(self) -> None:
        """Backward-compatible alias for :meth:`close`."""
        self.close()

    def write(self, cmd: str):
        if self.instrument is None:
            raise RuntimeError("Instrument not connected")
        self.instrument.write(cmd)

    def query(self, cmd: str) -> str:
        if self.instrument is None:
            raise RuntimeError("Instrument not connected")
        return self.instrument.query(cmd)

    def query_binary(
        self, cmd: str, datatype: str = "f", is_big_endian: bool = True
    ) -> list[float]:
        """
        Query binary IEEE 488.2 block data.
        datatype='f' (float32) or 'd' (float64).
        """
        if self.instrument is None:
            raise RuntimeError("Instrument not connected")
        return self.instrument.query_binary_values(
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

    def wait_opc(self, timeout_sec: float = 30.0) -> bool:
        """Block until *OPC? explicitly returns 1."""
        if self.instrument is None:
            raise RuntimeError("Instrument not connected")
        old_timeout = self.instrument.timeout
        self.instrument.timeout = int(timeout_sec * 1000)
        try:
            response = self.query("*OPC?").strip()
            return response == "1"
        except pyvisa.errors.VisaIOError:
            return False
        finally:
            self.instrument.timeout = old_timeout

    def get_errors(self) -> list[str]:
        """Drain the SCPI error queue."""
        errs = []
        while True:
            msg = self.query("SYST:ERR?")
            if msg.startswith(("+0,", "0,")):
                break
            errs.append(msg)
        return errs

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

    def _set_trace_math(self, result: str, operation: str, operand1: str, operand2: str):
        """
        Perform trace math (e.g., phase noise cancellation).
        Example: result='TRACE3', operation='POW',
                 operand1='TRACE1', operand2='TRACE2'
        """
        self.write(f"TRAC:MATH {result},{operation},{operand1},{operand2}")

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

    def set_trace_type_average(self, trace_num):
        self.write(f":TRACe{trace_num}:TYPE AVERAGE")

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

    def marker_to_peak(self, marker: int):
        self.write(f"CALC:MARK{marker}:MAX")

    def marker_to_next_peak(self, marker: int):
        self.write(f"CALC:MARK{marker}:MAX:NEXT")

    def marker_to_center(self, marker: int):
        self.write(f"CALC:MARK{marker}:CENT")

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

    # ------------------------------------------------------------------
    # Convenience: Noise Measurement Presets
    # ------------------------------------------------------------------
    def configure_noise_measurement(
        self,
        center_hz: float,
        span_hz: float,
        rbw_hz: float | None = None,
        use_rms: bool = True,
        avg_count: int = 10,
    ):
        """
        Typical preset for noise floor / broadband noise characterization.
        - RMS detector
        - Power averaging
        - Single sweep (caller must re-trigger for each acquisition)
        """
        self.set_center_freq(center_hz)
        self.set_span(span_hz)
        self.set_rbw(rbw_hz, auto=(rbw_hz is None))
        self.set_vbw(None, auto=True)
        if use_rms:
            self.set_detector("AVER")
            self.set_average_type("RMS")
        self.set_average_count(avg_count)
        self.set_continuous_sweep(False)

    def apply_trace_math_noise_cancel(
        self,
        trace_cal: str = "TRACE1",
        trace_dut: str = "TRACE2",
        trace_result: str = "TRACE3",
        avg_count: int = 100,
    ):
        """
        Example workflow to remove instrument noise / LO phase noise:
        1. Average cal trace (source off or known cal)
        2. Average DUT trace
        3. Compute power difference into result trace.
        """
        self.set_trace_mode(trace_cal, "AVER")
        self.set_average_count(avg_count)
        self.single_sweep_wait()

        self.set_trace_mode(trace_dut, "AVER")
        self.set_average_count(avg_count)
        self.single_sweep_wait()
