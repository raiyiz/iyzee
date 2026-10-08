"""Load the newest scope recording, check it, and summarize each channel."""

import hashlib

from iyzee.config import data_root
from iyzee.experiment import load_recording
from iyzee.waveform_math import traces_from_scope_recording

path = max(data_root().rglob("*_scope_*.npz"), key=lambda p: p.stat().st_mtime)
recording = load_recording(path)
manifest = recording.metadata

assert manifest["kind"] == "scope-acquisition"
assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["data_sha256"], "file was modified"

print(
    f"{path.name} from {manifest['instrument']['identity']} ({manifest['instrument']['protocol']})"
)
if manifest["acquisition"]["warnings"]:
    print("warnings:", *manifest["acquisition"]["warnings"], sep="\n  ")

for trace, wave in zip(traces_from_scope_recording(recording), manifest["waveforms"], strict=True):
    stats = wave["stats"]
    print(
        f"{trace.label}: {stats['sample_count']} samples over "
        f"{trace.time[-1] - trace.time[0]:.3g} {trace.time_unit}, "
        f"rms {stats['rms']:.4g} {trace.value_unit}, peak-to-peak {stats['peak_to_peak']:.4g}"
    )
