"""Load the newest sweep recording, check it, and print its noise relative to shot noise."""

import hashlib

from iyzee.analysis import summarize_sweep
from iyzee.config import data_root
from iyzee.experiment import load_recording

# Sweeps are named for their kind ("bandwidth" or "frequency"); scope runs are "scope".
candidates = [p for p in data_root().rglob("*.npz") if "_scope_" not in p.name]
path = max(candidates, key=lambda p: p.stat().st_mtime)
recording = load_recording(path)
manifest = recording.metadata

# 1. Is the file what was written? (The manifest stores the NPZ's SHA-256.)
assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["data_sha256"], "file was modified"

# 2. How did the run end? "running" means it never finished.
run = manifest["run_metadata"]
print(f"{path.name}: {run['status']}, {len(run['failed_steps'])} failed step(s)")

# 3. One number per point: the mean relative noise (squeezing - shot noise) in dB. Negative = below shot noise.
sweep = summarize_sweep(recording, "mean")
for point in sweep.points:
    shown = "no data" if point.relative_noise_db is None else f"{point.relative_noise_db:+.2f} dB"
    print(f"{point.label:>24}  x={point.requested:g} {sweep.x_unit}  {shown}")
