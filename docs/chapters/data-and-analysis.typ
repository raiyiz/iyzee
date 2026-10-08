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

Both end up as ordinary NumPy arrays. For waveform arithmetic (subtracting traces, baseline correction, rescaling) the pure functions in
`waveform_math` take and return #code("Trace") objects with no Textual or hardware import (#code("subtract_traces"), #code("subtract_background"), #code("scale_trace"));
the Results page calls the same ones.

#tested-by("test_load_sweep_example_reads_what_save_step_results_wrote", "test_load_scope_example_reads_what_save_scope_acquisition_wrote")

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
