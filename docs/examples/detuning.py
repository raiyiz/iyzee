"""For a frequency sweep, report each point's detuning from the nearest rubidium line."""

import hashlib

from iyzee.analysis import summarize_sweep
from iyzee.config import data_root
from iyzee.devices.wavemeter import Rb_transitions  # (label, THz) for the D1/D2 hyperfine lines
from iyzee.experiment import load_recording

path = max(data_root().rglob("*_frequency_*.npz"), key=lambda p: p.stat().st_mtime)
recording = load_recording(path)
manifest = recording.metadata
assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["data_sha256"], "file was modified"

for point in summarize_sweep(recording, "mean").points:
    # What the wavemeter read after settling, not the requested setpoint.
    measured = point.measured
    if measured is None:
        print(f"{point.label}: no wavemeter reading")
        continue
    label, line_thz = min(Rb_transitions, key=lambda t: abs(measured - t[1]))
    detuning_mhz = (measured - line_thz) * 1e6
    noise = point.relative_noise_db
    shown = "no data" if noise is None else f"{noise:+.2f} dB"
    print(f"{measured:.7f} THz  {detuning_mhz:+9.1f} MHz from {label}  {shown}")
