# iyzee `moonshine` cleanup plan

This document is the durable execution plan for the incremental cleanup of the `moonshine` branch.

## Goal

Improve correctness, safety, testability, maintainability, and reproducibility without performing a large rewrite. Preserve experimental behavior unless a change is explicitly identified as a bug or safety issue.

## Working branches

- Starting point: `moonshine`
- Cleanup branch: `cleanup-moonshine`
- Target: `moonshine`
- One pull request should contain the incremental commits.

## Critical workflow: one commit, then CI, then fix

Work strictly in small, reviewable commits.

For every planned change:

1. Inspect the current HEAD and the relevant files.
2. Make **one logical change only**.
3. Commit it with a focused conventional commit message.
4. Push/update the cleanup branch.
5. **Do not immediately make the next planned change.**
6. Check CI for the commit just created.
7. If CI fails, make one or more small follow-up fix commits addressing only those failures.
8. Re-check CI for the resulting HEAD.
9. At every step, retain the commit history; do not squash intermediate commits.

Important: CI results are only available after a commit exists. Therefore the next iteration must explicitly inspect the CI result of the previous commit before proceeding. Treat CI as feedback, not as something to predict perfectly in advance.

If a CI failure is unrelated to the current change, document it and investigate before proceeding. Do not silently ignore failing checks.

## Local checks

Before each commit, run the checks that are available locally and relevant to the change:

- `pytest`
- `ruff check .`
- `ruff format --check .`
- `mypy` or the repository's configured type checker

Do not claim a check was run if it was not actually run.

CI remains authoritative for environment-dependent behavior.

## Safety rules

This is laboratory/instrument-control software.

- Never leave an optical shutter open because of an exception path.
- Never turn a hardware communication failure into a plausible measurement value.
- Do not make real hardware access happen merely because a Python module is imported.
- Do not change instrument setpoints or SCPI behavior unless the change is understood and tested.
- Prefer deterministic cleanup with `try/finally` or context managers.
- Keep hardware-facing changes small and independently reviewable.

## Known open issue — needs a human, not an incremental step

`power.py`'s `ShutterControl.__init__` sets the shutter's PSU channel to **1.7 V**, while the
adjacent comment reads `# Shutter needs at least ~2 V to trigger.` This is a direct contradiction
and the comment must be fixed!

The corrent value is 1.7 V


## Planned sequence

### 01 — Fix package imports — ✅ done

`src/iyzee/main.py` uses correct package-relative imports (`.mxa`, `.power`,
`.wavemeter_readout`). Package/module execution and tests verified.

Follow-up gap closed: the test suite (`tests/test_mxa.py`, `test_power.py`,
`test_wavemeter_readout.py`) still imported bare `mxa` / `power` / `wavemeter_readout` modules via
a `sys.path` hack in `conftest.py`, rather than `iyzee.mxa` etc. This created a second, duplicate
copy of each module at test time and was invisible to mypy. Fixed: `conftest.py` now puts `src/`
(not `src/iyzee/`) on `sys.path`, and all three test files import through the `iyzee` package.

### 02 — Remove import-time hardware execution — ✅ done

No module performs hardware I/O or monitoring merely from being imported; all such behavior lives
behind explicit functions/entry points.

### 03 — Centralize instrument addresses — ✅ done

All instrument IPs, including the wavemeter, live in `base.py`'s `IP` enum and are referenced from
there (`IP.WAVEMETER` in `wavemeter_readout.py`).

### 04 — Make hardware cleanup exception-safe — ✅ done

`record_bw_seq()` and `record_freq_seq()` in `main.py` wrap acquisition in `try/finally` and
guarantee `mx.disconnect()` / `shutter.close()` on failure. `acquire_trace()` disables trace update
in a `finally` block.

### 05 — Make device lifecycle explicit — ✅ done

`BaseDevice`, `KeysightMXA`, and `ShutterControl` all support `__enter__`/`__exit__`.

Remaining gap (see step 09): `KeysightMXA` duplicates this lifecycle logic instead of reusing
`BaseDevice`.

### 06 — Fix RBW/VBW scan behavior — ✅ done

`record_bw_seq()` keeps VBW = 2 × RBW explicit in code and comments, with a test
(`test_record_bw_seq_tracks_vbw_with_rbw`) pinning the relationship.

### 07 — Improve wavemeter error handling — ✅ mostly done, one bug fixed

`single_readout()` raises `WavemeterReadoutError` on communication/parse failure instead of
returning `0`, with tests covering both failure modes.

Bug found and fixed: `track_frequency()`'s inner `update_plot()` passed `timeout=2` as a keyword
argument to `float(...)` instead of to `urllib.request.urlopen(...)`. This is a guaranteed
`TypeError` the first time that code path executes (the live-plotting loop was, in effect, dead
code). Fixed by moving `timeout=2` into the `urlopen()` call.

Still open: `update_plot()`'s except-and-print-and-continue pattern is a reasonable choice for a
live plot (skip a bad sample rather than crash), but it's a different error-handling
philosophy than `single_readout()`'s explicit exception. Worth a deliberate decision, not urgent.

### 08 — Verify `*OPC?` and SCPI errors — ✅ done

`wait_opc()` checks the response equals `"1"` rather than assuming completion. `get_errors()`
drains the SCPI error queue.

### 09 — Finish the shared base-device extraction — ⚠️ partially done

`base.py` holds `CH`, `IP`, `BaseDevice`; `__init__.py` is minimal and re-exports them.

Remaining gap: `KeysightMXA` (`mxa.py`) does **not** inherit from `BaseDevice`. It re-implements
essentially the same `connect()` / `close()` / `__enter__` / `__exit__` logic independently, with a
different instrument attribute name (`self.instr` vs. `BaseDevice`'s `self.instrument`). This is
real duplication, but unifying it is a breaking change: every test in `test_mxa.py` currently
constructs instances via `KeysightMXA.__new__(KeysightMXA)` and pokes `.instr` directly. This needs
its own dedicated step that updates the tests in the same commit — do not fold it into an unrelated
change.

### 10 — Remove stale/dead MXA implementations — ⚠️ partially done

`scope.py` had a `readOld()` method that duplicated `readAll()`: unused anywhere in the codebase,
explicitly documented as "not tested lately," and containing a real bug (mixed `str`/`bytes`
concatenation that would raise `TypeError` if ever called). Removed, and the class docstring's
method list updated to match.

Still open: `mxa.py`'s `configure_noise_measurement()` and `apply_trace_math_noise_cancel()` still
contain commented-out lines (`# self.reset()`, `# self.set_trace_math(...)`,
`# self.set_trace_mode(...)`). These aren't duplicate *implementations* the way `readOld` was, but
they are dead code worth a decision: either delete them or turn them into real optional behavior.

### 11 — Improve hardware testability — ✅ done

`FakeResourceManager` / `FakeInstrument` doubles are injected via `resource_manager=` across
`BaseDevice`, `KeysightMXA`, and `PSU`, and are exercised in `test_base.py`, `test_mxa.py`,
`test_power.py`.

### 12 — Fix and expand tests — ⚠️ in progress

No incorrect monkeypatch targets found (the duplicate-module issue from step 01 was a correctness
risk but happened to still work because `monkeypatch.setattr(mxa_module.pyvisa, ...)` patches the
shared global `pyvisa` module regardless of which copy of `mxa` imported it — now moot after the
import unification).

Added this pass: `tests/test_scope.py`, the **first tests `scope.py` has ever had** — covers
`_recv_exact()` reassembling data delivered across multiple fragmented `recv()` calls, raising on a
closed connection mid-read, and `__getHeader()` assembling a header split across reads.

Still open: `scope.py`'s `getDataBytes`, `getDataWords`, `getDataFloats`, `getHorProperties`,
`send()`, and `readAll()` remain untested against a fake socket end-to-end. `wavemeter_readout.py`'s
`track_frequency()` and `monitoring_frequencies()` are also untested.

### 13 — Clean dependency configuration — ✅ done

`pyproject.toml` keeps `pytest` under `[project.optional-dependencies].test` only; no dev-only
package leaks into runtime `dependencies`.

### 14 — Add lint/type-check configuration — ✅ done

`[tool.ruff]` (target-version, line-length, `select = ["E4","E7","E9","F","I"]`) and `[tool.mypy]`
are both configured. CI runs `ruff check`, `ruff format --check`, and `mypy src tests` as separate
jobs.

Bug found and fixed in the surrounding CI, not the lint config itself: a recent commit
(`ci: use lightweight runners and avoid pip upgrades`) changed `runs-on: ubuntu-latest` to
`runs-on: ubuntu-slim` in all three jobs (`test`, `lint`, `typecheck`). **`ubuntu-slim` is not a
valid GitHub-hosted runner label** — this was silently preventing every CI job from starting at
all, which also breaks this document's own "commit → check CI → fix" workflow, since there was no
CI signal to check. Reverted to `ubuntu-latest`; the `pip install --upgrade pip` removal from that
same commit was fine and was kept.

**This should be treated as the highest-priority fix of this pass** — nothing else in the plan can
be verified via CI until it lands and a real green run is confirmed.

### 15 — Make units explicit — ⚠️ partially done

`main.py`'s `AnalyzerConfig` dataclass uses explicit `_hz` / `_ms` suffixes throughout.

Still open: `wavemeter_readout.py`'s frequency constants (`D1_center_85`, `two_photon_762`, etc.)
and `single_readout()`/`set_pid_setpoint()` parameters are still bare floats documented only in
comments/docstrings (THz vs. GHz vs. MHz is easy to mix up here, and the module already computes in
multiple scales via `scal = 1e-3`).

### 16 — Introduce typed analyzer configuration — ✅ done

`AnalyzerConfig` (a `@dataclass(slots=True)`) replaced the loose dict/`**overrides` pattern for MXA
setup.

### 17 — Separate acquisition, plotting, and persistence — ✅ done for now

`main.py` already separates `prepare_analyzer()` / `acquire_trace()` / `record_*()` /
`multiplot()` / `save_data()` into distinct functions, with plotting performed once after all
traces are acquired. Per the README's own design direction, splitting `main.py` into a
`measurements/` package should happen once more procedures are added — not needed yet at the
current size.

### 18 — Improve measurement persistence — ❌ not started

`save_data()` still stores only the raw `(x_value, squeezing, shot_noise)` tuples as an object
array in the `.npz`. No configuration, timestamp, or software-revision metadata is embedded in the
saved file itself (only in the directory name via `create_dirs()`). Implement as a backward-compatible
addition — e.g. an optional `metadata: dict | None = None` parameter on `save_data()` that adds a
second array to the archive — so the existing `test_save_data_round_trip` contract keeps passing
unchanged for callers that don't pass metadata.

### 19 — Improve oscilloscope socket robustness — ✅ mostly done

Audited every fixed-length socket read in `scope.py`. Found and fixed:

- `__getHeader()` did a single `self.s.recv(8)` and assumed it returned all 8 header bytes at once
  — not guaranteed over TCP.
- The `rethead = self.s.recv(38)` reads in `getDataBytes()` and `getDataWords()` had the same
  assumption.
- The trailing-newline reads (`en = self.s.recv(aln)`) had the same assumption.
- `send()` passed a raw `ctypes.Structure` (`head`) directly to `socket.send()`; changed to
  `socket.send(bytes(head))` for portability/correctness.

Added a single `_recv_exact()` helper (loops until the requested byte count is actually received,
raises `ConnectionError` on a closed socket) and used it consistently everywhere a fixed-length read
happens, replacing several slightly-different ad hoc accumulation loops with one. Added
`tests/test_scope.py` to cover it against a fragmenting fake socket (see step 12).

Still open: `scope.py`'s `LeCroy` class remains a standalone legacy driver, not integrated with
`BaseDevice`'s connection lifecycle (no context-manager support, no injectable
transport/resource-manager for testing beyond the low-level socket helpers now covered). If this
oscilloscope is still in active use, it's the best candidate for the next full cleanup pass —
bringing `connect()`/`disconnect()` in line with `BaseDevice`'s pattern, and testing
`getDataBytes`/`getDataWords`/`getDataFloats`/`getHorProperties` end-to-end against a fake socket.

### 20 — Final review — ❌ not started, blocked on the above

Blocked on: step 09 (BaseDevice/MXA unification), step 18 (persistence metadata), the remaining
step 15 typing gap, and confirming real (not just local) CI is green — see step 14's CI fix, which
must be verified against an actual GitHub Actions run, not just local `pytest`/`ruff`/`mypy`, before
this step can start.

## Commit discipline

Prefer commits such as:

- `fix: use package-relative imports`
- `fix: prevent wavemeter work at import time`
- `fix: centralize wavemeter address`
- `fix: close shutter on acquisition failure`
- `fix: correct RBW scan VBW handling`
- `test: add MXA command transport double`
- `refactor: extract shared device base`
- `ci: restore valid GitHub-hosted runner labels`
- `fix: pass wavemeter readout timeout to urlopen, not float`
- `fix: read fixed-length scope frames to completion`
- `refactor: remove untested duplicate scope read implementation`
- `test: cover fragmented scope socket reads`
- `test: import instrument modules through the iyzee package`

Avoid commits such as `cleanup`, `misc fixes`, or `refactor everything`.

## CI feedback log

Append a short note here after each CI cycle if useful. Local-only verification (this cleanup
pass, not yet pushed/checked against real CI) confirmed `pytest` (25 passed), `ruff check .`, and
`ruff format --check .` all pass with the CI-runner and code fixes applied together — but per this
document's own rules, that is not a substitute for an actual CI run and must still be checked once
pushed.

| Step | Commit | CI result | Follow-up |
|---|---|---|---|
| 01 | TBD | TBD | TBD |
| 02 | TBD | TBD | TBD |
| 03 | TBD | TBD | TBD |
| 04 | TBD | TBD | TBD |
| 05 | TBD | TBD | TBD |
| 06 | TBD | TBD | TBD |
| 07 | TBD | TBD | TBD |
| 08 | TBD | TBD | TBD |
| 09 | TBD | TBD | TBD |
| 10 | TBD | TBD | TBD |
| 11 | TBD | TBD | TBD |
| 12 | TBD | TBD | TBD |
| 13 | TBD | TBD | TBD |
| 14 | TBD | TBD — was previously unable to run at all due to `ubuntu-slim`; fix included in this pass, needs a real CI check | TBD |
| 15 | TBD | TBD | TBD |
| 16 | TBD | TBD | TBD |
| 17 | TBD | TBD | TBD |
| 18 | TBD | TBD | TBD |
| 19 | TBD | TBD | TBD |
| 20 | TBD | TBD | TBD |

## Continuation instructions

If this work is resumed in a later conversation, start by reading this file and then:

1. Inspect `cleanup-moonshine` HEAD.
2. Inspect the latest commit and its diff.
3. Check CI for that exact commit.
4. If CI is failing, fix those failures first in small commits.
5. If CI is green, continue with the first unfinished numbered step — currently **step 09**
   (BaseDevice/MXA unification) or **step 18** (persistence metadata) are the best next
   candidates, since steps 01–08, 11, 13, 14, 16, and 19 are done or effectively done.
6. Never assume a previous CI result; fetch it again.
7. Do not skip steps or combine unrelated planned changes merely to reduce commit count.
8. Resolve the shutter-voltage contradiction noted above with a human before touching
   `ShutterControl`'s setpoints.

The objective is a clean, understandable commit-by-commit history where every step leaves the branch in a known state.
