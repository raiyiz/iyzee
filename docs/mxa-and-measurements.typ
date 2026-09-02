// iyzee MXA and measurement guide

#set document(
  title: "iyzee MXA control and measurement model",
  author: "iyzee",
)

#set page(
  margin: (x: 2.2cm, y: 2cm),
  header: context [
    #set text(size: 8pt)
    #smallcaps[iyzee]
    #h(1fr)
    MXA & measurements
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
  #text(size: 22pt, weight: "bold")[MXA control and measurement model]
  #v(0.4em)
  #text(size: 11pt)[iyzee technical guide]
]

#v(0.6em)

*Status:* documentation of the current `moonshine` implementation. This guide explains the boundary between the experiment code, the MXA driver, and the measurement quantities returned to Python. It is not a replacement for the Keysight programmer's or measurement references.

#align(center)[#outline(title: [Contents], indent: 1.2em)]

#pagebreak()

= Code and instrument boundary

The control path is

$ "experiment" -> "KeysightMXA" -> "PyVISA" -> "SCPI" -> "MXA" $

and the data path is

$ "MXA" -> "SCPI response" -> "PyVISA" -> "KeysightMXA" -> "Python" $

`KeysightMXA` inherits the shared `BaseDevice` lifecycle. The base class owns the VISA resource manager, address, timeout, terminations, connection, close operation, and context-manager behavior. The MXA driver owns analyzer-specific operations: frequency, amplitude, bandwidth, sweep control, traces, markers, and triggering.

The driver deliberately keeps the SCPI boundary thin. For example, `set_center_freq(freq_hz)` writes `FREQ:CENT`, `set_rbw(rbw_hz)` writes `BWID`, and `get_trace_data()` selects ASCII or binary transfer before reading the trace. Units are explicit at the Python boundary: frequencies are in Hz, sweep duration in ms, RF reference and marker powers in dBm where applicable, and counts are dimensionless.

The experiment layer builds measurements from `Step` objects. `BandwidthStep` scans RBW; `FrequencyStep` changes the laser setpoint, waits for a configured settling interval, and acquires squeezing and shot-noise traces with the shutter closed again before the reference acquisition. `main.py` currently runs the bandwidth sweep, plots it, and saves the resulting `StepResult` objects.

= Measurement configuration

An MXA trace is the result of a configured signal-processing chain, not a direct ADC sample:

$ "input" -> "attenuation / preamp" -> "mixer / IF" -> "RBW" -> "detector" -> "VBW / averaging" -> "trace" $

`AnalyzerConfig` collects the experiment-level settings used by `prepare_analyzer()`: center frequency, span, RBW, sweep duration, average count and type, and trigger source. The current bandwidth procedure uses 1.5 MHz center frequency, 100 kHz span, 24 kHz initial RBW, 10 ms sweep duration, 300 averages, and the configured trigger source.

RBW and point spacing are different quantities. For a linear sweep with start frequency `f_1`, stop frequency `f_2`, and `N` points,

$ f_i = f_1 + i (f_2 - f_1) / (N - 1) $

and

$ d_f = (f_2 - f_1) / (N - 1) $

where `i` runs from zero to `N - 1`. The spacing `d_f` describes sampling of the trace axis; RBW describes the analyzer's resolution filter.

Reference level and RF attenuation are also distinct. Reference level sets the displayed vertical operating point; attenuation changes the signal level entering later stages. A reproducible noise measurement should therefore record attenuation and relevant preamplifier state, not only center frequency and RBW.

= Bandwidth, averaging, and noise quantities

RBW controls frequency selectivity and, for approximately white noise, the noise power admitted by the resolution filter. A simple model is

$ P approx S B_e $

where `P` is integrated noise power, `S` is noise power density, and `B_e` is effective noise bandwidth. Effective bandwidth need not equal the nominal RBW.

VBW is a post-detection filter. It reduces variation in the displayed statistic; it is not a second RF resolution filter. Detector selection, trace averaging, VBW, and average type therefore describe different parts of the measurement chain. In particular, averaging a logarithmic quantity is not generally equivalent to averaging linear power:

$ log((P_1 + P_2) / 2) != (log(P_1) + log(P_2)) / 2 $

For noise measurements, the averaging domain must match the quantity being estimated. The code exposes `set_detector()`, `set_average_type()`, `set_average_count()`, and trace averaging explicitly so the experiment can make that choice visible.

Noise-marker results are interpreted as spectral density, typically in dBm/Hz; band-power markers return integrated power. The expected white-noise scaling is

$ P_2 / P_1 approx B_2 / B_1 $

so doubling effective bandwidth predicts about `3.01 dB`, subject to filter shape, DUT flatness, detector behavior, and analyzer noise.

The analyzer itself contributes noise. Any background subtraction must therefore be justified in linear power and under the same relevant instrument state. Subtracting two dBm values gives a logarithmic ratio, not a physical power difference. When a residual power is required, convert to linear power first, subtract there, and convert back only after the subtraction.

= Acquisition and synchronization

The driver separates configuration, arming, acquisition, and readout. The normal single-sweep path is:

$ "configure" -> "INIT:CONT OFF" -> "INIT:IMM" -> "*OPC?" -> "read trace" $

`single_sweep_wait()` performs that sequence, while `wait_opc()` temporarily applies the requested VISA timeout and restores the previous value afterward. `get_errors()` drains the SCPI error queue with `SYST:ERR?`.

`wait_for_trigger_ready()` uses the Operation Status Register rather than a fixed delay to detect the analyzer's armed state. Trigger readiness and acquisition completion are different states, and neither one proves that an external physical source emitted the intended event.

Fixed `sleep()` calls belong only where they represent characterized physical settling. This matters in `FrequencyStep`: the laser frequency setpoint is changed, the procedure waits `relax_time_s`, the shutter is opened only for the squeezing acquisition, the shutter is closed in a `finally` block, and the shot-noise reference is acquired afterward.

The software ordering is therefore explicit, but its scientific validity still depends on the physical system being sufficiently stationary during the sequence.

= Data transfer and persistence

Trace data can be transferred as ASCII values or IEEE 488.2 binary floating-point data. The binary path explicitly selects 32-bit floats and big-endian decoding. Transport format does not define the physical units; interpretation still depends on the analyzer mode and measurement configuration.

An exported `StepResult` contains an x value and unit, named traces, and metadata. `save_step_results()` writes those results to a compressed NumPy archive and stores per-point metadata together with optional run-level metadata. The saved data therefore retains the experimental context alongside the numerical arrays instead of relying on a filename or an undocumented convention.

The minimum useful record includes the frequency coordinate and unit, analyzer configuration, RBW/VBW, detector and averaging settings, sweep duration, trigger state, trace identity, attenuation/reference information when relevant, and physical scan state such as laser setpoint and shutter state.

The scientific rule is simple: a trace is reproducible only when the settings that can change its numerical meaning are recorded with it.

= Current experiment workflows

`run_bandwidth_sweep()` builds a sequence of `BandwidthStep` objects. Each step sets RBW, uses `VBW = 2 * RBW`, acquires squeezing and shot-noise traces, and records the RBW/VBW values as metadata. This scan is useful because the measured noise should approximately follow effective bandwidth for a white-noise region; deviations can reveal non-flat DUT noise, analyzer noise, effective-bandwidth differences, or estimator bias.

`run_frequency_sweep()` builds `FrequencyStep` objects around a laser-frequency center value. Its settling interval is currently estimated as

$ t = t_s N_a $

from sweep duration `t_s` and average count `N_a`. This is an acquisition-workload estimate, not a demonstrated physical settling constant.

The key comparison in that workflow is squeezing versus shot noise. A difference between two dBm traces is a power ratio. A residual power requires linear-domain subtraction. Documentation and downstream analysis should keep those quantities distinct.

= Python to SCPI map

#table(
  columns: (1.2fr, 1.65fr, 2fr),
  stroke: 0.5pt,
  inset: 5pt,
  align: (left, left),
  [*Concept*], [*Project API*], [*SCPI*],
  [Identity], [`idn()`], [`*IDN?`],
  [Status], [`reset()`, `get_errors()`], [`*RST`, `*CLS`, `SYST:ERR?`],
  [Synchronization], [`wait_opc()`], [`*OPC?`],
  [Frequency], [`set_center_freq`, `set_span`, `set_start_stop`], [`FREQ:CENT`, `FREQ:SPAN`, `FREQ:STAR`, `FREQ:STOP`],
  [Amplitude], [`set_ref_level`, `set_attenuation`], [`DISP:WIND:TRAC:Y:RLEV`, `POW:ATT`],
  [Bandwidth], [`set_rbw`, `set_vbw`], [`BWID`, `BWID:VID`],
  [Sweep], [`set_sweep_duration`, `set_sweep_points`, `single_sweep_wait`], [`SWE:TIME`, `SWE:POIN`, `INIT:*`],
  [Trace], [`set_trace_mode`, `set_trace_update`, `get_trace_data`], [`:TRACe...`, `FORMat:DATA`, `:TRACe:DATA?`],
  [Markers], [`set_marker_*`, `get_marker_*`], [`CALC:MARK...`],
  [Trigger], [`set_trigger_*`, `wait_for_trigger_ready`], [`TRIG:*`, `STAT:OPER:*`],
)

= References

- Keysight, *X-Series Signal Analyzer Programmer's Guide*: SCPI programming, communication, synchronization, and status. #link("https://www.keysight.com/us/en/assets/7018-06864/programming-guides/9018-06864.pdf")[Guide]
- Keysight, *X-Series Spectrum Analyzer Mode Measurement Guide*: RBW, sweep behavior, detectors, and measurement setup. #link("https://www.keysight.com/tn/en/assets/9018-04190/user-manuals/9018-04190.pdf")[Measurement guide]
- Keysight, *Noise Measurements*: detector choice, trace averaging, VBW/RBW behavior, and averaging domains. #link("https://helpfiles.keysight.com/csg/89600B/Webhelp/Subsystems/powerspectrum/content/ps_noisemeasurements.htm")[Noise measurements]
- NIST, *Spectrum Amplitude Definition, Generation, and Measurement*, Technical Note 699: bandwidth and equivalent-bandwidth concepts. #link("https://www.nist.gov/system/files/documents/calibrations/tn699.pdf")[Technical Note 699]
- PyVISA documentation, `query_binary_values()`: binary transfer, datatype, endianness, and containers. #link("https://pyvisa.readthedocs.io/en/1.10.0/api/resources.html")[PyVISA resources]
- Repository implementation: `src/iyzee/base.py`, `src/iyzee/mxa.py`, `src/iyzee/experiment/`, and the associated tests.

= Maintenance rule

Keep this guide tied to the implementation. When a code change alters analyzer behavior, SCPI commands, units, timing, calibration assumptions, experiment ordering, or persisted metadata, update the corresponding paragraph here. Avoid documenting historical procedures that no longer exist in the code.

*Always strive for improvement, always be humble.*
