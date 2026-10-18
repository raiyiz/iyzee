"""Write synthetic iyzee recordings to explore the analysis notebooks without hardware.

Example:
    uv run scripts/demo_data.py demo-data
    IYZEE_DATA_DIR=demo-data uv run --group notebooks marimo edit notebooks/workbench.py

The signals are illustrative only, and each saved recording is labelled synthetic.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from iyzee.devices.scope import Channel, Coupling, TriggerCoupling, TriggerMode, TriggerSlope
from iyzee.experiment import AnalyzerConfig, RunRecord, StepResult, save_step_results
from iyzee.scope_workflows import (
    ChannelSettings,
    ScopeAcquisition,
    ScopeWaveform,
    TriggerSettings,
    _stats,
    save_scope_acquisition,
)

CENTER_THZ = 377.1052067
TRACE_SAMPLES = 1001
SHOT_NOISE_DBM = -78.0


def _stem(when: datetime, name: str, rng: np.random.Generator) -> str:
    return f"{when:%dT%H%M%S}_{name}_{rng.bytes(3).hex()}"


def _utc(when: datetime) -> str:
    return when.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run_metadata(config: AnalyzerConfig, started: datetime, status: str, note: str) -> dict:
    record = RunRecord(config=asdict(config))
    record.started_at_utc = _utc(started)
    record.finished_at_utc = _utc(started + timedelta(minutes=4))
    record.status = status
    return {**record.as_metadata(), "synthetic": True, "note": note}


def _traces(rng: np.random.Generator, relative_db: float) -> dict[str, np.ndarray]:
    shot = SHOT_NOISE_DBM + rng.normal(0.0, 0.12, TRACE_SAMPLES)
    squeezing = SHOT_NOISE_DBM + relative_db + rng.normal(0.0, 0.12, TRACE_SAMPLES)
    return {"squeezing": squeezing, "shot_noise": shot}


def _save_sweep(
    root: Path,
    name: str,
    when: datetime,
    results: list[StepResult],
    config: AnalyzerConfig,
    status: str,
    rng: np.random.Generator,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return save_step_results(
        results,
        root,
        _run_metadata(config, when, status, f"synthetic {name}"),
        name=name,
        path=root / f"{_stem(when, name, rng)}.npz",
    )


def bandwidth_sweep(
    root: Path,
    when: datetime,
    rng: np.random.Generator,
    *,
    name: str,
    depth_db: float,
    fade_khz: float,
) -> Path:
    """An illustrative squeezing dip that fades with increasing resolution bandwidth."""
    config = AnalyzerConfig(res_bw_hz=20e3, avg_count=200, sweep_duration_ms=10)
    results = []
    for rbw in np.arange(1, 20) * 20e3:
        relative_db = -depth_db * np.exp(-rbw / (fade_khz * 1e3)) + 0.4
        results.append(
            StepResult(
                f"rbw={rbw:.0f}Hz",
                float(rbw),
                "Hz",
                _traces(rng, relative_db),
                {"rbw_hz": float(rbw), "vbw_hz": float(2 * rbw)},
            )
        )
    return _save_sweep(root, name, when, results, config, "completed", rng)


def frequency_sweep(
    root: Path,
    when: datetime,
    rng: np.random.Generator,
    *,
    name: str,
    depth_db: float,
    detuning_mhz: float,
    points: int = 21,
    status: str = "completed",
) -> Path:
    """An illustrative resonance dip sampled every 10 MHz."""
    config = AnalyzerConfig(res_bw_hz=24e3, avg_count=150, sweep_duration_ms=10)
    offsets_mhz = (np.arange(points) - points // 2) * 10.0
    results = []
    for offset in offsets_mhz:
        requested = CENTER_THZ + offset * 1e-6
        measured = requested + rng.normal(0.0, 4e-7)
        relative_db = -depth_db * np.exp(-((offset - detuning_mhz) ** 2) / (2 * 35.0**2))
        relative_db += 0.6 + 0.5 * np.tanh(offset / 80.0)
        results.append(
            StepResult(
                f"freq={requested:.6f}THz",
                float(requested),
                "THz",
                _traces(rng, relative_db),
                {
                    "wavemeter_channel": 4,
                    "relax_time_s": 0.5,
                    "measured_frequency_thz": float(measured),
                },
            )
        )
    return _save_sweep(root, name, when, results, config, status, rng)


def scope_acquisition(
    root: Path,
    when: datetime,
    rng: np.random.Generator,
    *,
    name: str,
    pulse_hz: float,
    amplitude: float,
    warnings: tuple[str, ...] = (),
) -> Path:
    """Create a three-channel pulse, detector-response, and trigger capture."""
    samples, interval, offset = 10_000, 2e-6, -5e-3
    time = offset + np.arange(samples) * interval
    pulses = (np.sin(2 * np.pi * pulse_hz * time) > 0).astype(float)
    detector = np.convolve(pulses, np.exp(-np.arange(400) / 90.0) / 90.0, mode="same")
    signals = {
        Channel.C1: amplitude * pulses + rng.normal(0.0, 0.01, samples),
        Channel.C2: amplitude * 0.6 * detector + rng.normal(0.0, 0.006, samples),
        Channel.C3: 2.5 * pulses + rng.normal(0.0, 0.02, samples),
    }
    waveforms = tuple(
        ScopeWaveform(
            channel=channel,
            time=time,
            values=values,
            raw_codes=None,
            value_unit="V",
            time_unit="S",
            time_offset=offset,
            time_interval=interval,
            vertical_gain=None,
            vertical_offset=None,
            stats=_stats(time, values),
        )
        for channel, values in signals.items()
    )
    settings = tuple(
        ChannelSettings(channel, True, 0.5, 0.0, Coupling.DC_1M) for channel in signals
    )
    trigger = TriggerSettings(
        Channel.C3, TriggerMode.NORMAL, TriggerSlope.POSITIVE, TriggerCoupling.DC, 1.0, 1e-3
    )
    acquisition = ScopeAcquisition(
        measurement_id=rng.bytes(4).hex(),
        started_at_utc=_utc(when),
        completed_at_utc=_utc(when + timedelta(seconds=2)),
        instrument_address="192.0.2.50",
        socket_timeout_s=10.0,
        requested_channel_settings=settings,
        requested_trigger_settings=trigger,
        applied_channel_settings=settings,
        applied_trigger_settings=trigger,
        waveforms=waveforms,
        instrument_id="SYNTHETIC,DEMO-SCOPE,0000,0.0",
        frozen=True,
        prior_trigger_mode=TriggerMode.AUTO,
        warnings=warnings,
    )
    root.mkdir(parents=True, exist_ok=True)
    path = save_scope_acquisition(
        acquisition, root, name=name, path=root / f"{_stem(when, name, rng)}.npz"
    )
    sidecar = path.with_suffix(".json")
    manifest = json.loads(sidecar.read_text())
    manifest["synthetic"] = True
    sidecar.write_text(json.dumps(manifest, indent=2))
    return path


def write_demo(root: Path, *, now: datetime | None = None, seed: int = 7) -> list[Path]:
    """Write a varied set of saved runs, grouped by month like iyzee data."""
    rng = np.random.default_rng(seed)
    now = (now or datetime.now().astimezone()).replace(microsecond=0)
    before = set(root.rglob("*.npz")) if root.exists() else set()

    def at(days_ago: float, hour: int) -> datetime:
        base = now - timedelta(days=days_ago)
        return base.replace(
            hour=hour,
            minute=int(rng.integers(0, 60)),
            second=int(rng.integers(0, 60)),
        )

    def month(when: datetime) -> Path:
        return root / f"{when:%Y-%m}"

    for days, name, depth, fade in (
        (9, "rbw-baseline", 3.2, 140.0),
        (6, "rbw-lowpower", 2.1, 110.0),
        (2, "rbw-aligned", 3.8, 190.0),
    ):
        when = at(days, 10)
        bandwidth_sweep(month(when), when, rng, name=name, depth_db=depth, fade_khz=fade)

    for days, name, depth, detuning, count, status in (
        (8, "scan-coarse", 2.2, -20.0, 21, "completed"),
        (5, "scan-aligned", 3.4, 5.0, 21, "completed"),
        (3, "scan-repeat", 3.1, 10.0, 21, "completed"),
        (1, "scan-pumped", 4.0, 0.0, 21, "completed"),
        (0.2, "scan-interrupted", 2.8, 15.0, 9, "aborted"),
    ):
        when = at(days, 14)
        frequency_sweep(
            month(when), when, rng, name=name, depth_db=depth,
            detuning_mhz=detuning, points=count, status=status,
        )

    for days, name, hz, amplitude, warnings in (
        (7, "scope-pulses-500hz", 500.0, 1.6, ()),
        (4, "scope-pulses-500hz-b", 500.0, 1.2, ()),
        (1, "scope-pulses-800hz", 800.0, 1.4, ("could not restore trigger mode AUTO: timeout",)),
    ):
        when = at(days, 16)
        scope_acquisition(
            month(when), when, rng, name=name, pulse_hz=hz,
            amplitude=amplitude, warnings=warnings,
        )
    return sorted(set(root.rglob("*.npz")) - before)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path, help="directory to write synthetic recordings under")
    parser.add_argument("--seed", type=int, default=7, help="seed for repeatable signal shapes")
    args = parser.parse_args()
    written = write_demo(args.out, seed=args.seed)
    print(f"wrote {len(written)} synthetic recordings under {args.out}")
    print(
        f"Try them with: IYZEE_DATA_DIR={args.out} "
        "uv run --group notebooks marimo edit notebooks/workbench.py"
    )


if __name__ == "__main__":
    main()
