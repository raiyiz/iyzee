#iyzee cleanup summary

#import "@preview/physica:0.9.8": grad, pdv

#set page(margin: 2.2cm)
#set text(size: 10pt)

= iyzee `moonshine` cleanup

*Status:* incremental cleanup of the `moonshine` branch, documented after a green GitHub Actions run for commit `3fa429a`.

*Principle:* _Always strive for improvement, always be humble._

== Executive summary

The cleanup is deliberately conservative: improve correctness, safety, testability, maintainability, and reproducibility without rewriting the experiment control layer. Hardware-facing behavior and SCPI commands are preserved unless a defect is identified and tested.

The current pass has established a cleaner separation between experiment orchestration and instrument drivers, centralized shared VISA lifecycle handling, strengthened failure cleanup, and added deterministic tests around the most failure-prone network boundary.

== What changed

#table(
  columns: (1.3fr, 1.7fr),
  stroke: .5pt,
  inset: 7pt,
  [Area], [Validated outcome],
  [Imports], [Package-relative imports are used; tests exercise the `iyzee` package rather than duplicate top-level modules.],
  [Hardware access], [Importing modules no longer starts hardware work. Acquisition remains behind explicit procedures.],
  [Addresses], [Instrument IP definitions are centralized in `base.py`.],
  [Cleanup], [`main.py` acquisition paths close the analyzer and shutter in failure-safe `finally` blocks.],
  [VISA lifecycle], [`BaseDevice` owns connection, timeout, termination, close, and context-manager behavior. `KeysightMXA` reuses it.],
  [MXA], [Tests use the shared `.instrument` double; stale commented-out MXA code was removed without enabling new SCPI behavior.],
  [Wavemeter], [Communication/parse failures raise an explicit error rather than silently producing a plausible zero.],
  [Synchronization], [`*OPC?` is checked explicitly and the SCPI error queue is drained.],
  [Scope transport], [`_recv_exact()` handles fragmented TCP reads and fails loudly on premature connection closure.],
  [CI], [GitHub Actions runs the test, Ruff, and mypy checks without duplicate push/PR pipelines; GitLab mirrors the same Python checks.],
  [Documentation], [This document is compiled with Typst 0.15.1 and pinned to `physica` 0.9.8 for scientific notation.],
)

== Oscilloscope transport hardening

TCP is a byte stream, not a message transport. A request for $n$ bytes does not guarantee that one `recv(n)` returns $n$ bytes. The LeCroy driver now treats fixed-size protocol fields as exact-length reads.

The test suite uses a deliberately fragmenting fake socket. It verifies the behavior for complete VICP frames, headers split over multiple reads, byte waveforms, 16-bit word waveforms, malformed waveform headers, and truncated payloads.

The essential invariant is:

$ len(received) = n $

before a fixed-length field is interpreted. If the peer closes the connection first, the driver raises `ConnectionError` rather than converting an incomplete transfer into measurement data.

For a measured quantity represented by samples $x_i$, this distinction matters because a transport failure must not silently become a shorter or otherwise plausible vector:

$ x = (x_0, x_1, dots, x_(N-1)) $

must be complete before downstream analysis treats it as an acquisition.

== Scientific notation

The documentation uses `physica` for compact mathematical notation. For example, the gradient of a scalar field $f$ can be written as

$ grad f = (pdv(f,x), pdv(f,y), pdv(f,z)) $

This is documentation infrastructure only; it does not introduce a new numerical dependency into the Python package.

== Hardware correctness note

The shutter control documentation now records the actual tested setpoint as *1.7 V*. Comments should describe measured or established behavior accurately; a contradictory `~2 V` statement was removed rather than preserved as folklore.

== Verification model

Every cleanup change follows the same loop:

+ inspect the current implementation;
+ make one logical change;
+ commit with a focused message;
+ run CI;
+ treat CI failures as feedback;
+ make only the necessary follow-up fix;
+ proceed only after the resulting head is green.

The latest code commit in this sequence is:

`3fa429a` — `test: cover LeCroy waveform socket paths`

Its GitHub Actions run completed successfully. The next documentation commit also adds a dedicated Typst compilation job so that the document format itself remains continuously validated.

== Remaining work

The cleanup is not complete. The highest-value remaining items are:

- make wavemeter frequency units explicit and consistently named;
- add backward-compatible metadata to persisted measurement files;
- decide whether `LeCroy` should adopt the shared device lifecycle or remain a standalone legacy driver;
- extend wavemeter and LeCroy tests where useful;
- perform a final review after the open cleanup-plan statuses are reconciled.

The intent is not to eliminate every imperfection. The intent is to leave the next change easier to understand, safer to test, and easier to verify than the previous one.

== Motto

#align(center)[
  *Always strive for improvement, always be humble.*
]
