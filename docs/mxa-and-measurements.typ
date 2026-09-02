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
  #v(0.5em)
  #text(size: 11pt)[iyzee technical guide]
]

#v(0.7em)

*Status:* engineering documentation for the current `moonshine` implementation.

#align(center)[#outline(title: [Contents], indent: 1.2em)]

#pagebreak()

= Purpose and scope

The purpose of this document is not to reproduce the analyzer manual. It connects the layers that must agree in a real experiment:

$ "physics" -> "measurement method" -> "analyzer state" -> "Python data" $

A value is scientifically useful only when its units, bandwidth, detector, averaging, timing, and calibration context are known.

== Reading this document

The Keysight X-Series Programmer's Guide provides the general SCPI programming layer: syntax, communication, synchronization, status, and programming techniques. Application-specific command definitions belong to the relevant User's and Programmer's Reference. The Python driver is therefore a small, opinionated interface over selected SCPI operations, not a replacement for the analyzer reference.

When a method changes analyzer behavior, document the instrument concept, the SCPI boundary, and the physical meaning. When a method only moves bytes, document the transport separately.

= The measurement chain

The useful mental model is:

$ "DUT / optical system" -> "RF signal" -> "MXA input" -> "attenuation / preamp" -> "mixer / IF" -> "RBW filter" -> "detector" -> "VBW / averaging" -> "trace" -> "export" $

Each stage can alter the measured quantity. RBW changes effective measurement bandwidth; detector choice changes how samples are combined; VBW and trace averaging reduce variation after detection; analyzer noise contributes to measured power; and reference level and attenuation affect the instrument operating point.

The MXA is therefore not a transparent voltmeter with a frequency axis. Its trace is the output of a configured signal-processing chain.

= Driver architecture

`KeysightMXA` inherits from `BaseDevice`. The shared base owns the VISA resource manager, address, timeout, terminations, connection, close operation, and context-manager behavior. The MXA layer concentrates on analyzer semantics: frequency, bandwidth, sweep control, traces, markers, triggering, and noise workflows.

The forward control path is

$ "application" -> "KeysightMXA" -> "PyVISA" -> "SCPI" -> "MXA" $

and the measurement path is

$ "MXA" -> "SCPI response" -> "PyVISA" -> "KeysightMXA" -> "Python value" $

For example, `set_center_freq(2.4e9)` emits `FREQ:CENT 2400000000`; `get_sweep_points()` queries `SWE:POIN?` and converts the response to an integer.

Keeping this boundary thin is deliberate: experiment code describes the measurement, while the driver owns protocol details.

= Frequency axis and sweep points

The driver exposes:

- `set_center_freq(freq_hz)` -> `FREQ:CENT`
- `set_span(span_hz)` -> `FREQ:SPAN`
- `set_start_stop(start_hz, stop_hz)` -> `FREQ:STAR` and `FREQ:STOP`
- `set_sweep_points(points)` -> `SWE:POIN`

`get_frequency_axis()` reconstructs a linear axis from the instrument's start frequency, stop frequency, and number of points:

$ f_i = f_1 + i (f_2 - f_1) / (N - 1) $

Here `f_1` and `f_2` are the start and stop frequencies, `N` is the number of points, and `i` runs from zero through `N - 1`.

A necessary consistency condition is

$ N >= 2 $

and the frequency-bin spacing is

$ d_f = (f_2 - f_1) / (N - 1) $

The point spacing `d_f` is not the same thing as RBW. RBW describes the analyzer's resolution filter; point spacing describes how densely the resulting trace is sampled.

= Amplitude, reference level, and attenuation

`set_ref_level()` writes the display reference level in dBm. `set_attenuation()` and `set_attenuation_auto()` control RF input attenuation.

These are not interchangeable:

- *reference level* sets the vertical operating/display reference;
- *RF attenuation* changes the attenuation ahead of later stages.

Lower attenuation can improve sensitivity but reduces headroom before overload. Higher attenuation improves headroom but generally raises the input-referred noise contribution. Preamplifier state, mixer level, and analyzer-specific protection limits remain instrument-level concerns.

For reproducible noise measurements, record attenuation and preamplifier state rather than treating the trace as fully specified by center frequency and RBW alone.

= RBW: resolution is also bandwidth

`set_rbw()` emits `BWID` or enables `BWID:AUTO`.

RBW is the analyzer's resolution-bandwidth filter. In a traditional swept measurement it controls frequency selectivity and the amount of random noise power admitted by the filter.

For approximately white noise, measured noise power scales approximately with effective bandwidth:

$ P_n approx S B_e $

Here `P_n` is measured noise power, `S` is noise power density, and `B_e` is the effective noise bandwidth.

Increasing RBW generally increases displayed noise power. Narrowing RBW lowers both the DUT contribution and the analyzer's own displayed noise.

RBW is not merely a visual smoothing parameter. It affects both measurement bandwidth and swept-acquisition time.

= VBW: post-detection filtering

`set_vbw()` emits `BWID:VID` or enables `BWID:VID:AUTO`.

VBW is a video/post-detection filter. It acts after detection and mainly reduces visible variation of a noisy trace; it is not a second RF resolution filter in the same sense as RBW.

A smaller VBW/RBW ratio can reduce point-to-point fluctuations. This can be useful for a stable estimator, but it should not be described as though the physical noise power had disappeared.

The distinction is:

$ "RBW" -> "frequency selectivity and noise bandwidth" $

versus

$ "VBW" -> "post-detection smoothing of the measurement statistic" $

The current noise preset uses automatic VBW because exact coupling is analyzer/application dependent. Persisted experiment metadata should record the resolved setting whenever exact reproducibility matters.

= Detector, averaging, and statistical meaning

The driver exposes `set_detector()`, `set_average_type()`, `set_average_count()`, and trace averaging through `set_trace_mode()`.

These operations are related but not equivalent.

*Detector* determines how samples within the sweep are combined around each displayed point.

*Trace averaging* averages corresponding trace points from successive sweeps.

*VBW filtering* acts in the post-detection path.

*Average type* determines the domain in which the analyzer averages a measurement. This is important for logarithmic displays.

For a power-like quantity, let `P_1` and `P_2` be two linear-power samples. Their arithmetic mean is represented by

$ P_m = (P_1 + P_2) / 2 $

In general,

$ log(P_m) != (log(P_1) + log(P_2)) / 2 $

so averaging after conversion to dB is not equivalent to averaging linear power and then converting to dB.

Keysight cautions that logarithmic averaging of noise can introduce a systematic error of about `-2.51 dB`; power/RMS averaging is preferred for accurate noise measurements.

For statistically meaningful noise measurements, document at least RBW, detector, average type, average count, sweep time, and VBW.

= Sweep time and statistical independence

`set_sweep_duration()` emits `SWE:TIME`; `set_sweep_points()` sets the number of trace points.

Sweep duration controls how long the analyzer spends acquiring a swept trace. It is not synonymous with the duration of the physical experiment or with the number of independent noise samples.

Increasing sweep time, reducing VBW, or increasing trace-average count can reduce estimator variance, but the improvement does not necessarily follow an exact square-root law without a defined statistical model and independence assumption.

The conservative statement is:

$ "more averaging" -> "generally lower estimator variance" $

= Noise density, integrated power, and ENBW

`configure_noise_marker()` selects the analyzer's noise-marker mode. The result is interpreted as spectral noise density, typically reported in dBm/Hz.

The density and an integrated power are different quantities:

$ S = P / B_e $

For a white-noise region, an integrated band-power model is

$ P = S B_e $

The effective noise bandwidth can differ from the displayed RBW. Calibrated conversions should therefore use the effective bandwidth defined for the analyzer/filter rather than blindly equating the two.

A useful white-noise check is

$ P_2 / P_1 approx B_2 / B_1 $

and, in dB,

$ d_P approx 10 log(B_2 / B_1) $

A factor-of-two effective-bandwidth change therefore predicts approximately `3.01 dB`, subject to filter shape, detector behavior, and DUT noise flatness.

= Band-power measurements

`configure_band_power_marker()` selects the band-power function and programs the center and offsets. `get_band_power()` returns the analyzer's integrated result for that band.

The dimensional chain is

$ S B_e -> P $

The dimensional meaning matters: density times bandwidth gives integrated power.

= Analyzer noise floor and noise cancellation

The analyzer contributes its own noise. A simple linear-power model is

$ P_m = P_d + P_a $

where `P_m` is measured power, `P_d` is DUT power, and `P_a` is analyzer contribution.

This model assumes the contributions are additive in linear power and that the relevant instrument state is unchanged.

If two traces are represented by logarithmic powers `p_d` and `p_c`, subtracting the displayed dB values is not the same as subtracting linear powers. Convert first:

$ P = 10^((p - 30) / 10) $

Then, where the calibration model justifies it,

$ P_r = P_d - P_c $

Convert the positive result back to dBm only after the subtraction.

A negative linear result is not a negative dBm power. It indicates that the assumed signal/background model is inconsistent with the measured values or that the desired quantity is below the subtraction uncertainty.

The repository's `apply_trace_math_noise_cancel()` should therefore be understood as a contextual calibration workflow, not a universal noise-cancellation algorithm.

= Trace storage and data transfer

The driver models traces as analyzer-side memory/display objects. `set_trace_mode()` maps to `:TRACe<n>:TYPE`; `set_trace_update()` controls trace updating; `set_trace_display()` controls display; `clear_trace()` clears instrument-side trace storage.

`get_trace_data()` has two transfer modes:

- ASCII via `FORMat:DATA ASCii`;
- binary floating point via `FORMat:DATA REAL,32` and `FORMat:BORDer NORM`.

The binary path uses PyVISA `query_binary_values(..., datatype="f", is_big_endian=True, container=list)`. The project explicitly specifies endianness rather than relying on a library default.

The important data path is

$ "trace memory" -> "IEEE 488.2 block" -> "VISA" -> "float32 values" -> "Python array" $

Transport format does not define physical units. The values must still be interpreted using the active analyzer mode and trace semantics.

= What one trace point means

A trace point is a processed measurement result associated with one frequency-axis coordinate. It is not necessarily a single ADC sample.

A useful abstraction is

$ y_i = M x_i $

where `M` denotes the configured analyzer measurement chain and `x_i` is the relevant signal contribution at the point.

The practical point is that changing RBW, detector, averaging mode, or sweep conditions can change the reported trace even when the DUT does not change.

Persisted data should retain enough analyzer metadata to reconstruct the measurement context: frequency range, point count, RBW, VBW, detector, averaging settings, sweep time, attenuation/reference level, trigger configuration, analyzer mode, and experiment state.

= Acquisition and synchronization

The driver separates configuration from measurement start:

- `set_continuous_sweep(False)` -> `INIT:CONT OFF`
- `initiate_sweep()` -> `INIT:IMM`
- `abort_sweep()` -> `ABOR`
- `single_sweep_wait()` performs single-sweep initiation followed by `*OPC?` synchronization

`wait_opc()` temporarily adjusts the VISA timeout, queries `*OPC?`, and restores the previous timeout.

The critical distinction is between *command completion* and *physical-event completion*. `*OPC?` establishes that pending instrument operations have completed from the instrument's perspective. It does not mean that an unrelated external shutter, laser, or trigger event occurred.

The software state machine is therefore

$ "configure" -> "arm / initiate" -> "instrument complete" -> "readout" $

An externally synchronized experiment may instead be

$ "prepare" -> "arm" -> "wait-for-event" -> "acquire" -> "OPC" -> "readout" $

Fixed `sleep()` calls are appropriate only when they represent deliberately characterized physical settling time. They are not substitutes for instrument synchronization.

= Triggering and operation status

The driver exposes immediate, video, external, RF-burst, and frame trigger sources, plus video/external levels and slope selection.

`wait_for_trigger_ready()` uses the Operation Status Register rather than assuming that a fixed delay is enough to arm the analyzer. It enables the relevant operation-status bit and polls `STAT:OPER:EVEN?` until the instrument reports the expected state.

These are different states:

$ "configuration" -> "armed" -> "event" -> "acquisition" -> "complete" -> "read" $

A polling method can establish analyzer state; it cannot by itself prove that the physical source emitted the intended event.

= Optical shutter timing

The MXA does not directly control the optical shutter. `ShutterControl` owns a PSU channel and sets the established trigger voltage to `1.7 V` with a `10 mA` current limit. `open()` enables the selected channel; `close()` disables it; context-manager exit closes the shutter and releases the PSU connection.

The experiment therefore spans two state machines:

$ "optical source" -> "shutter state" $

and

$ "RF analyzer" -> "trigger / acquisition state" $

The orchestration layer must define their ordering explicitly. In `record_freq_seq()`, the laser setpoint is changed, a settling interval is applied, the shutter is opened, the squeezing trace is acquired, the shutter is closed, and the shot-noise trace is then acquired.

The meaning of the comparison depends on whether the relevant states are physically stationary over the complete sequence. Software ordering and physical settling are different concepts.

= Squeezing and shot-noise comparison

The experiment uses `TRACE_SQZ = 1` and `TRACE_SHOT = 2`, then computes a pointwise difference labelled “Squeezing - shot noise”.

If the trace values are logarithmic powers in dBm, then

$ p_s - p_b = 10 log(P_s / P_b) $

where `p_s` and `p_b` are the displayed dBm values and `P_s` and `P_b` are the corresponding linear powers.

That is a logarithmic power ratio, not a linear power difference.

A true residual power requires

$ P_r = P_s - P_b $

in linear units before any conversion to dB.

For quantum-noise or squeezing analysis, the documentation should state whether the reported quantity is absolute noise power, noise power spectral density, a ratio relative to shot noise, or a normalized variance.

= Why `record_bw_seq()` is scientifically useful

`record_bw_seq()` scans RBW while keeping the broad measurement context fixed.

For white noise with approximately constant spectral density, integrated noise power should grow with effective bandwidth. A factor-of-two increase therefore predicts approximately `3.01 dB`.

The scan can expose several non-idealities:

- effective bandwidth differs from nominal RBW;
- the DUT noise is not spectrally flat;
- analyzer noise becomes significant;
- detector or averaging settings bias the estimator;
- sweep coupling changes timing or effective statistics.

The expected scaling is therefore a diagnostic model, not a command-level invariant.

= Why `record_freq_seq()` is a synchronization experiment

`record_freq_seq()` changes a laser frequency setpoint and waits a configured settling interval before opening the shutter and acquiring data.

The relaxation interval is derived from sweep duration and average count:

$ t = t_s N_a $

Here `t_s` is sweep duration and `N_a` is average count. This is a software estimate of acquisition workload, not a demonstrated physical settling time.

The sequence is:

$ "setpoint" -> "settle" -> "shutter open" -> "squeezing acquisition" -> "shutter close" -> "shot-noise acquisition" $

Its validity depends on repeatability of the laser, shutter, detector, analyzer, and DUT state across those transitions.

= SCPI map: concept -> Python -> instrument

#table(
  columns: (1.25fr, 1.55fr, 2.2fr),
  stroke: 0.5pt,
  inset: 6pt,
  align: (left, left),
  [*Instrument concept*], [*Project method*], [*SCPI boundary*],
  [Identification], [`idn()`], [`*IDN?`],
  [Reset/status], [`reset()`], [`*RST`, `*CLS`],
  [Synchronization], [`wait_opc()`], [`*OPC?`],
  [Error handling], [`get_errors()`], [`SYST:ERR?`],
  [Frequency], [`set_center_freq`, `set_span`, `set_start_stop`], [`FREQ:CENT`, `FREQ:SPAN`, `FREQ:STAR`, `FREQ:STOP`],
  [Amplitude], [`set_ref_level`, `set_attenuation`], [`DISP:WIND:TRAC:Y:RLEV`, `POW:ATT`],
  [Bandwidth], [`set_rbw`, `set_vbw`], [`BWID`, `BWID:VID`],
  [Sweep], [`set_continuous_sweep`, `initiate_sweep`, `abort_sweep`], [`INIT:CONT`, `INIT:IMM`, `ABOR`],
  [Trace data], [`get_trace_data()`], [`FORMat:DATA`, `:TRACe:DATA?`],
  [Markers], [`set_marker_*`, `get_marker_*`], [`CALC:MARK...`],
  [Trigger], [`set_trigger_*`, `wait_for_trigger_ready()`], [`TRIG:*`, `STAT:OPER:*`],
  [Trace math], [`_set_trace_math()`], [`TRAC:MATH`],
)

= Engineering invariants

A useful measurement driver should preserve these invariants:

1. *Units are explicit.* Python boundaries use Hz, ms, dBm, and counts rather than implicit unit conventions.
2. *Logarithms are not powers.* Never subtract dBm values when a physical power subtraction is intended.
3. *RBW is not point spacing.* Frequency resolution and trace sampling are separate concepts.
4. *Averaging is a statistical operation.* Detector, VBW, trace averaging, and average type have distinct effects.
5. *Completion is observed.* `*OPC?` is preferable to guessed acquisition delays for instrument completion.
6. *Calibration is contextual.* A background trace is subtractable only when the additive-noise model and instrument state justify it.
7. *Data needs metadata.* A trace without analyzer state is difficult to reproduce and easy to misinterpret.

= Recommended measurement record

For every exported trace, the scientific minimum is more than `x` and `y`. Store, directly or through a structured configuration record:

- frequency range and point count;
- RBW and VBW, including whether either was automatic;
- detector and averaging type/count;
- sweep time and continuous/single state;
- RF attenuation and reference level, plus preamplifier state when relevant;
- trigger source and relevant trigger parameters;
- trace identity and analyzer mode;
- calibration/background state;
- physical experimental state, such as laser setpoint and shutter state;
- acquisition timestamp and instrument identity.

Each field corresponds to a variable that can change the numerical meaning of the trace.

= References

- Keysight, *X-Series Signal Analyzer Programmer's Guide*: general SCPI programming, communication, synchronization, status, and programming techniques. #link("https://www.keysight.com/us/en/assets/7018-06864/programming-guides/9018-06864.pdf")[Guide]
- Keysight, *X-Series Spectrum Analyzer Mode Measurement Guide*: RBW, sweep behavior, detectors, and practical measurement setup. #link("https://www.keysight.com/tn/en/assets/9018-04190/user-manuals/9018-04190.pdf")[Measurement guide]
- Keysight, *Noise Measurements*: detector choice, trace averaging, VBW/RBW behavior, and logarithmic-versus-power averaging. #link("https://helpfiles.keysight.com/csg/89600B/Webhelp/Subsystems/powerspectrum/content/ps_noisemeasurements.htm")[Noise measurements]
- Keysight, *Using Noise Floor Extension in an X-Series Signal Analyzer*: analyzer noise contribution and variance near the noise floor. #link("https://www.keysight.com/zz/en/assets/7018-02450/application-notes/5990-5340.pdf")[Application note]
- NIST, *Spectrum Amplitude Definition, Generation, and Measurement*, Technical Note 699: bandwidth definitions and the distinction between nominal and power/equivalent bandwidth concepts. #link("https://www.nist.gov/system/files/documents/calibrations/tn699.pdf")[Technical Note 699]
- PyVISA documentation, `query_binary_values()`: binary block decoding, datatype, endianness, and container behavior. #link("https://pyvisa.readthedocs.io/en/1.10.0/api/resources.html")[PyVISA resources]
- Repository implementation: `src/iyzee/mxa.py`, `src/iyzee/power.py`, `src/iyzee/main.py`, and the associated tests.

= Maintenance rule

This document is a living technical record. When code changes, update the corresponding conceptual mapping if the instrument behavior, SCPI command, units, timing, calibration assumptions, or acquisition semantics change.

*Always strive for improvement, always be humble.*
