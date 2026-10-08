"""For a frequency sweep, report each point's detuning from the nearest rubidium line."""

import hashlib

from iyzee.config import data_root
from iyzee.devices.wavemeter import Rb_transitions  # (label, THz) for the D1/D2 hyperfine lines
from iyzee.experiment import difference_values_many, load_recording

path = max(data_root().rglob("*_frequency_*.npz"), key=lambda p: p.stat().st_mtime)
recording = load_recording(path)
manifest = recording.metadata
assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["data_sha256"], "file was modified"

db = difference_values_many(
    recording.arrays["trace_squeezing"], recording.arrays["trace_shot_noise"], "mean"
)
for point, value in zip(manifest["points"], db, strict=True):
    # What the wavemeter read after settling, not the requested setpoint.
    freq_thz = point["measured_frequency_thz"]
    label, line_thz = min(Rb_transitions, key=lambda t: abs(freq_thz - t[1]))
    detuning_mhz = (freq_thz - line_thz) * 1e6
    shown = "no data" if value is None else f"{value:+.2f} dB"
    print(f"{freq_thz:.7f} THz  {detuning_mhz:+9.1f} MHz from {label}  {shown}")
