// iyzee cleanup summary

#set document(title: "iyzee moonshine cleanup", author: "iyzee")
#set page(
  margin: (x: 2.2cm, y: 2cm),
  header: context [
    #set text(size: 8pt)
    #smallcaps[iyzee]
    #h(1fr)
    Cleanup summary
  ],
  footer: context [
    #set text(size: 8pt)
    #h(1fr)
    #counter(page).display("1 / 1", both: true)
  ],
)
#set par(justify: true, leading: 0.55em)
#set heading(numbering: "1.")
#set text(size: 10pt)

#align(center)[
  #text(size: 22pt, weight: "bold")[iyzee `moonshine` cleanup]
  #v(0.5em)
  #text(size: 11pt)[Engineering cleanup summary]
]

#v(0.8em)

*Status:* incremental cleanup of the `moonshine` branch, with the code changes validated by CI.

*Principle:* _Always strive for improvement, always be humble._

#align(center)[#outline(title: [Contents], indent: 1.2em)]

#pagebreak()

= Executive summary

The cleanup is deliberately conservative: improve correctness, safety, testability, maintainability, and reproducibility without rewriting the experiment control layer. Hardware-facing behavior and SCPI commands are preserved unless a defect is identified and tested.

The current pass establishes a cleaner separation between experiment orchestration and instrument drivers, centralizes shared VISA lifecycle handling, strengthens failure cleanup, and adds deterministic tests around the most failure-prone network boundary.

= What changed

#table(
  columns: (1.25fr, 1.75fr),
  stroke: 0.5pt,
  inset: 7pt,
  align: (left, left),
  [*Area*], [*Validated outcome*],
  [Imports], [Package-relative imports are used; tests exercise the `iyzee` package rather than duplicate top-level modules.],
  [Hardware access], [Importing modules no longer starts hardware work. Acquisition remains behind explicit procedures.],
  [Addresses], [Instrument IP definitions are centralized in `base.py`.],
  [Cleanup], [`main.py` acquisition paths close the analyzer and shutter in failure-safe `finally` blocks.],
  [VISA lifecycle], [`BaseDevice` owns connection, timeout, termination, close, and context-manager behavior. `KeysightMXA` reuses it.],
  [MXA], [Tests use the shared `.instrument` double; stale commented-out MXA code was removed without enabling new SCPI behavior.],
  [Wavemeter], [Communication and parse failures raise an explicit error rather than silently producing a plausible zero.],
  [Synchronization], [`*OPC?` is checked explicitly and the SCPI error queue is drained.],
  [Scope transport], [`_recv_exact()` handles fragmented TCP reads and fails loudly on premature connection closure.],
  [CI], [GitHub Actions runs the test, Ruff, and mypy checks without duplicate push/PR pipelines; GitLab mirrors the same Python checks.],
  [Documentation], [The technical guides are authored in Typst and compiled in CI, with `physica` pinned for scientific notation.],
)

= Oscilloscope transport hardening

TCP is a byte stream, not a message transport. A request for $n$ bytes does not guarantee that one `recv(n)` returns $n$ bytes. The LeCroy driver now treats fixed-size protocol fields as exact-length reads.

The test suite uses a deliberately fragmenting fake socket. It verifies complete VICP frames, headers split over multiple reads, byte waveforms, 16-bit word waveforms, malformed waveform headers, and truncated payloads.

The essential invariant is:

$ N = n $

where $N$ is the number of bytes received for a field that declares length $n$. If the peer closes the connection first, the driver raises `ConnectionError` rather than converting an incomplete transfer into measurement data.

For a measured quantity represented by samples $x_i$, a transport failure must not silently become a shorter or otherwise plausible vector:

$ x = (x_0, x_1, dots, x_(N - 1)) $

must be complete before downstream analysis treats it as an acquisition.

= Scientific notation

The documentation uses `physica` for compact mathematical notation. For example, the gradient of a scalar field $f$ can be written as

$ grad f = (pdv(f, x), pdv(f, y), pdv(f, z)) $

This is documentation infrastructure only; it does not introduce a new numerical dependency into the Python package.

= Hardware correctness note

The shutter control documentation records the established trigger setpoint as *1.7 V*. Comments should describe measured or established behavior accurately; a contradictory `~2 V` statement was removed rather than preserved as folklore.

= Verification model

Every cleanup change follows the same loop:

+ inspect the current implementation;
+ make one logical change;
+ commit with a focused message;
+ run CI;
+ treat CI failures as feedback;
+ make only the necessary follow-up fix;
+ proceed only after the resulting head is green.

The latest code change in the sequence is `test: cover LeCroy waveform socket paths`. The documentation pipeline now compiles the Typst sources as a first-class CI check and publishes the resulting PDFs as artifacts.

= Remaining work

The cleanup is not complete. The highest-value remaining items are:

- make wavemeter frequency units explicit and consistently named;
- add backward-compatible metadata to persisted measurement files;
- decide whether `LeCroy` should adopt the shared device lifecycle or remain a standalone legacy driver;
- extend wavemeter and LeCroy tests where useful;
- perform a final review after the open cleanup-plan statuses are reconciled.

The intent is not to eliminate every imperfection. It is to leave the next change easier to understand, safer to test, and easier to verify than the previous one.

= Maintenance rule

Keep this summary short and factual. When the implementation changes, update the corresponding technical guide or cleanup note rather than allowing the documentation to drift from the code.

#align(center)[
  #v(1em)
  *Always strive for improvement, always be humble.*
]
