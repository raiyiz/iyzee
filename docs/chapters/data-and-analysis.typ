// iyzee data and analysis guide: what is in the files, and how to trust and read them
#import "requirements.typ": *

#part(
  "For the scientist",
  "Data and analysis",
  "What every saved file contains, field by field; how to load it; and how to tell a clean measurement from a qualified one.",
  id: "part-data",
)

#callout(
  "Rendered from the code",
  [
    The tables in this part are not typed by hand. They are produced by running the real
    save functions on a small synthetic run and reading the files back
    (`scripts/docs_index.py`). Adding a field to a manifest without describing it, or
    describing a field that no longer exists, fails the documentation build.
  ],
  tone: "result",
)

#v(0.6em)

= Where your data lands <data-where>

Every measurement is a *pair* of files with one shared stem, written below the data
directory in a folder per month (`data/YYYY-MM/`, set by `IYZEE_DATA_DIR` or the
config file; see @sec-addresses):

#raw("DDTHHMMSS_<name>_<6 hex>.npz   numbers only (compressed NumPy archive)\nDDTHHMMSS_<name>_<6 hex>.json  the manifest: what the numbers mean and where they came from", block: true)

The stem is the day of month, the time, the run's name and six random hex digits,
so two runs started in the same second never collide and a directory lists
chronologically. `<name>` is `bandwidth` or `frequency` for sweeps and `scope` for
scope acquisitions. The pattern is #code("STEM_PATTERN"), and #code("load_recording") is the single reader
for the pair.

The `.npz` contains only numeric arrays, so loading it never needs `allow_pickle=True`
and cannot execute anything. The `.json` is plain text you can read with `cat`. The two
are written to a temporary name and then renamed, so a crash leaves the previous
checkpoint intact rather than a truncated file.

#anchors("save_numeric_recording", "save_step_results", "load_recording", "Recording")

= Sweep recordings <data-sweep>

A sweep is saved after *every* point (one file pair, replaced atomically), so an
interrupted run keeps what it had. Row `i` of every `trace_*` array, entry `i` of `x_values`
and entry `i` of the manifest's `points` list describe the same measurement point.

#let dictionary(family) = {
  let docs = index.recordings.at(family).field_docs
  let rows = ()
  for key in docs.keys().sorted() {
    let depth = key.split(".").len() - 1
    rows.push(pad(left: depth * 8pt, raw(key.split(".").last())))
    rows.push(eval(docs.at(key), mode: "markup"))
  }
  table(
    columns: (auto, 1fr),
    stroke: (x, y) => (bottom: 0.4pt + hairline),
    inset: (x: 5pt, y: 3.5pt),
    fill: none,
    table.header([*Field*], [*Meaning*]),
    ..rows,
  )
}

#let arrays(family) = {
  let docs = index.recordings.at(family).array_docs
  let info = index.recordings.at(family).arrays
  let rows = ()
  for key in docs.keys().sorted() {
    rows.push(raw(key))
    rows.push(text(size: 8pt, fill: muted)[#info.at(key).dtype, #info.at(key).ndim\-D])
    rows.push(eval(docs.at(key), mode: "markup"))
  }
  table(
    columns: (auto, auto, 1fr),
    stroke: (x, y) => (bottom: 0.4pt + hairline),
    inset: (x: 5pt, y: 3.5pt),
    fill: none,
    table.header([*Array*], [*Type*], [*Meaning*]),
    ..rows,
  )
}

== Arrays <data-sweep-arrays>

#arrays("sweep")

The scanned quantity of a point is `x_values`; the analyzer traces are taken in zero span, so a trace's own axis is
time (@part-mxa). The comparison the Results page plots, and the number a sweep is usually
reduced to, is *squeezing minus shot noise* per sample, in dB, because both traces are in dBm:
#code("difference_series") gives the per-sample line and #code("difference_statistic") reduces a pair of traces to their mean or minimum,
using only samples where both traces are finite.

== Manifest <data-sweep-manifest>

#dictionary("sweep")

#figure(
  block(width: 100%, fill: blue-pale, inset: 6pt, radius: 3pt, breakable: true)[
    #set text(size: 7pt)
    #raw(json.encode(index.recordings.sweep.manifest, pretty: true), lang: "json", block: true)
  ],
  caption: [A real manifest written by #code("save_step_results") for a two-point synthetic run (values are illustrative; the field set is the code's).],
)

= Scope recordings <data-scope>

A scope acquisition writes a new pair per click. Each recorded channel `<ch>` (`C1`..`C4`) contributes three arrays.

== Arrays <data-scope-arrays>

#arrays("scope")

== Manifest <data-scope-manifest>

#dictionary("scope")

The requested and applied configuration objects are #code("ChannelSettings") and #code("TriggerSettings") serialized as plain keys
(`channel`, `enabled`, `volts_per_div`, `offset`, `coupling`; `source`, `mode`, `slope`, `coupling`, `level_volts`, `time_per_div`).

= Reading the data <data-reading>

These are the exact scripts under `docs/examples/`; a test runs each one against recordings written by the real save
functions, so they cannot silently rot.

== A sweep <data-reading-sweep>

#raw(read("../examples/load_sweep.py"), lang: "python", block: true)

== A scope acquisition <data-reading-scope>

#raw(read("../examples/load_scope.py"), lang: "python", block: true)

Both end up as ordinary NumPy arrays. To ask what a recording *means* instead of reading arrays yourself, #code("summarize_sweep") returns one #code("SweepPoint") per point (requested and measured setpoint, and the point's relative noise in dB: squeezing minus shot noise, negative below shot noise) and #code("summarize_scope") returns the channel statistics and timebase; the Results page shows exactly these. For waveform arithmetic (subtracting traces, baseline correction, rescaling) the pure functions in
`waveform_math` take and return #code("Trace") objects with no Textual or hardware import (#code("subtract_traces"), #code("subtract_background"), #code("scale_trace"));
the Results page calls the same ones.

#tested-by("test_load_sweep_example_reads_what_save_step_results_wrote", "test_load_scope_example_reads_what_save_scope_acquisition_wrote", "test_a_bandwidth_sweep_reports_requested_setpoints_and_relative_noise")


= Notebook workbench <data-workbench>

The interactive workbench is a read-only front end for recordings already on disk. It never
connects to an instrument or changes hardware state. It starts with the JSON sidecars and
loads the numeric arrays only for recordings you mark, so browsing a large data directory
does not read every waveform into memory.

Install the optional notebook dependencies and launch it from the repository root:

#raw("uv sync --group notebooks\nuv run --group notebooks marimo edit notebooks/workbench.py", lang: "sh", block: true)

Use the directory control to select a recording folder (or set `IYZEE_DATA_DIR` before
launching), then press *Refresh index* after adding or changing files. The browser shows
recording kind, status, instrument, synthetic-data flag, warnings, file size, and whether a
checksum is recorded.

Every recording gets a stable short id such as `#07` (numbered oldest to newest across the
whole index), shown in every table, legend and plot. To analyse recordings, build a
*working set*: filter the list, tick rows, and press *Add ticked rows* (or one of the quick
buttons). Filters never change the working set, so a set can mix kinds and dates, and the set
survives re-indexing. The set is shown in the sidebar and at the top of each section, with one
plot colour per run, so it is always clear which files an analysis is using.

The recording table is built for scanning many files: the `in set` and `ref` columns stay
pinned while you scroll sideways, hovering a `ref` shows the recording's facts (instrument,
status, item count, checksum, path), and a date range, *Only the working set* and the type,
status and origin filters narrow the list. Click a column header to sort. Ticking a row shows a
preview below the table: its facts, a thumbnail of its data, the arrays stored in the file, and
its raw JSON sidecar, so a recording can be inspected before it is added to the set.

*Saved working sets* keep a selection (and its group labels, below) under a name. They are stored
in `worksets.json` next to the iyzee config file (set `IYZEE_WORKSETS_FILE` to move it), never in
the data directory, so the recordings folder stays read-only. A saved set is matched back to
files by path and then by run id, because the `ref` numbers shift if older recordings are added
later. Runs that no longer exist are reported rather than silently dropped. A file that cannot
be parsed is moved aside as `worksets.json.corrupt` instead of being overwritten.

#table(
  columns: (1.2fr, 2.5fr),
  stroke: (x, y) => (bottom: 0.4pt + hairline),
  inset: (x: 5pt, y: 4pt),
  fill: none,
  [*View*], [*What it provides*],
  [*Sweep comparison*], [One row per sweep point, a scoreboard with the best point per run, and relative-noise and trace-level metrics in tidy Polars tables.],
  [*Point inspector*], [The raw squeezing and shot-noise traces of one sweep point, for every sweep in the set at once.],
  [*Scope statistics*], [Per-channel sample count, extrema, peak-to-peak amplitude, mean, RMS, standard deviation, and recorded channel settings.],
  [*Scope traces*], [Selected waveforms for overlay and plotting. Plotting uses extrema-preserving decimation for responsiveness; the saved arrays are not modified.],
  [*Groups and statistics*], [Label runs with a group (an editable column, or filled from name, type, status or origin). Each group gets a mean curve with a standard-deviation, standard-error or min-to-max band on one shared x grid, a per-group summary of the best points, and an A minus B difference with a two-standard-error band.],
  [*Raw arrays*], [List every array in every file, then overlay one named array across the runs that have it. One-dimensional arrays and a selected row of a two-dimensional array are supported; higher-dimensional arrays are rejected rather than silently flattened.],
  [*Plot builder*], [Choose a tidy table, x/y fields, colour and line-style groups, one panel per value, log axes, and line/point/bar marks instead of writing a separate plotting function for each question. Download the figure as PNG, SVG or PDF.],
  [*Integrity and export*], [Verify the NPZ SHA-256 digest against its JSON manifest and download tidy tables as CSV. Missing digests are reported rather than treated as verified.],
)

Group statistics interpolate every grouped run onto one x grid (linearly; in log x for bandwidth
sweeps) over the range that all of them cover, so nothing is extrapolated. A group needs at least
two runs for a spread, and a group of one run shows only its mean. The A minus B difference uses
Welch's unequal-variance standard error; a point is flagged when the difference exceeds two
standard errors. That flag is a rough guide to whether a gap is larger than the run-to-run
scatter, not a p-value, and with fewer than three runs per group it is only indicative.

For frequency sweeps, plotted offsets use one median reference shared by all marked frequency
runs; the code does not re-center each run independently, so measured drift remains visible.
The reference-comparison view interpolates compatible runs onto the chosen run's x-grid and
does not extrapolate beyond another run's measured range. A positive difference means the
comparison run has more relative noise than the reference.

The workbench includes ready-made frequency, bandwidth, and scope comparisons as well as
the configurable plot builder. All of it is driven by one `Workset` object, so the same
analysis is available without the widgets:

#raw("ws = Workset.open(\"demo-data\", kind=\"frequency\", latest=5)  # or a list of #refs / names\nws.scoreboard()\nws.delta(ws.refs[0])\ng = ws.with_groups({ws.refs[0]: \"base\", ws.refs[1]: \"pumped\", ws.refs[2]: \"pumped\"})\ng.plot_groups()  # mean with a band per group\ng.group_diff(\"pumped\", \"base\")  # difference, standard error, flag\nWorksetStore().save(\"my set\", g)  # saved sets, groups included", lang: "python", block: true)

#file("notebooks/sweep_scope_analysis.py", label: "sweep_scope_analysis.py") is a short code-first
notebook that does exactly this.

For a different task—reproducing the live frequency sweep to investigate TUI versus
measurement behavior—use
#file("notebooks/reproduce_frequency_sweep.py", label: "reproduce_frequency_sweep.py").
This notebook operates real instruments: it changes the laser setpoint, opens and closes the
shutter, and reconfigures the MXA. It requires explicit confirmation and the TUI must be
closed first. It is intentionally separate from the read-only workbench.

The shared helpers in
#file("notebooks/toolbox.py", label: "toolbox.py")
and the working-set class in
#file("notebooks/workset.py", label: "workset.py")
remain ordinary Python; the data path uses the same
#file("src/iyzee/experiment/io.py", label: "recording loader")
and
#file("src/iyzee/analysis.py", label: "analysis summaries")
as the rest of iyzee.

= Deciding whether to trust a recording <data-trust>

A recording is only as good as its provenance, and iyzee records the provenance so you do not have to remember it. Before using one, check:

#table(
  columns: (1.2fr, 2.6fr),
  stroke: (x, y) => (bottom: 0.4pt + hairline),
  inset: (x: 5pt, y: 4pt),
  fill: none,
  [*Look at*], [*What it tells you*],
  [`data_sha256`], [Whether the `.npz` is still the file that was written (recompute and compare).],
  [`run_metadata.status`], [`completed` is a finished run; `completed_with_errors` means some points are missing and `failed_steps` says which and why; `aborted` was stopped by you or by shutdown; `failed` stopped on setup or an unexpected error; `running` means the process died before finishing, so the file is only as long as it got.],
  [`run_metadata.config`], [The analyzer settings in force, including the averaging type, so you know in which domain the traces were averaged.],
  [`points[*].measured_frequency_thz`], [For frequency sweeps, the wavemeter reading taken after settling. Prefer it to the requested `x_values` when the laser lock matters.],
  [`NaN` rows in `trace_*`], [A point that has no data for that trace at all (distinct from a length mismatch, which refuses to save).],
  [`acquisition.frozen` and `warnings`], [Whether every scope channel came from one capture, and any problem that did not stop it.],
  [requested vs applied configuration], [The scope rounds V/div and offset and can ignore a command; *applied* is what it reported after writing, and is what the capture used.],
  [`instrument.identity`], [Which physical scope (make, model, serial, firmware) produced the data.],
)

Two physical orderings are fixed by the code and worth knowing when you interpret a frequency sweep (#code("FrequencyStep")): the shutter is open only around the
squeezing trace and is closed again before the shot-noise reference, and the laser settles for a fixed time before the wavemeter is read. Both are
software orderings; whether the system was stationary enough over the sequence is a question about your experiment, not the file.

= Terms <data-terms>

#table(
  columns: (auto, 1fr),
  stroke: (x, y) => (bottom: 0.4pt + hairline),
  inset: (x: 5pt, y: 3.5pt),
  fill: none,
  [*RBW / VBW*], [Resolution bandwidth (the RF filter) and video bandwidth (post-detection smoothing). A bandwidth sweep steps RBW and sets VBW to twice it.],
  [*Zero span*], [The analyzer stays at one frequency; the trace is power versus time.],
  [*Step*], [One reproducible measurement point (#code("Step")); a sweep is a list of them run by #code("run_sequence").],
  [*Recording*], [A saved `.npz` + `.json` pair (#code("Recording")).],
  [*Handle*], [The adapter that gives every instrument the same connect / probe / lock contract (#code("InstrumentHandle")).],
  [*Probe*], [The cheap call right after connecting that proves the instrument really answers.],
  [*Baseline*], [The last scope state the instrument itself reported; Apply writes only what differs from it.],
  [*Read-back*], [Asking the instrument what it now holds after a write; only read-back values are trusted.],
  [*Freeze*], [Stopping a running scope acquisition for the download so all channels share one capture.],
  [*DEF9 block*], [LeCroy's definite-length binary reply: `#9`, a nine-digit byte count, then that many bytes.],
  [*VXI-11*], [The LAN instrument protocol the scope is controlled over (VISA `TCPIP0::<ip>::inst0::INSTR`).],
  [*PSR*], [Polarization self-rotation, the squeezing mechanism (@part-rubidium).],
)
