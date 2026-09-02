// iyzee MXA and measurement guide

#set document(title: "iyzee MXA control and measurement model", author: "iyzee")
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

*Status:* engineering documentation for the current `moonshine` implementation. SCPI relationships below are derived from the Keysight X-Series programming documentation and from the commands emitted by `src/iyzee/mxa.py`.

#align(center)[#outline(title: [Contents], indent: 1.2em)]

#pagebreak()

= Purpose and scope

This document connects the four layers that must agree in a real experiment:

$ "physics" -> "measurement method" -> "analyzer state" -> "Python data" $

A value is scientifically useful only when its units, bandwidth, detector, averaging, timing, and calibration context are known.

== Reading this document

The Keysight X-Series Programmer's Guide is the general SCPI programming layer. Application-specific command definitions belong to the relevant analyzer reference. The Python driver is a small interface over selected SCPI operations, not a replacement for the instrument manual.

When a method changes analyzer behavior, document the instrument concept, the SCPI command, and the physical meaning. When a method only moves bytes, document the transport separately.

= The measurement chain

The useful mental model is

$ "DUT / optical system" -> "RF signal" -> "MXA input" -> "attenuation / preamp" -> "mixer / IF" -> "RBW filter" -> "detector" -> "VBW / averaging" -> "trace" -> "export" $

Each stage can alter the measured quantity. RBW changes the effective measurement bandwidth; the detector changes how detected samples are formed; VBW and trace averaging reduce variation after detection; analyzer noise adds to measured power; and reference level and attenuation affect the instrument operating point.

= Driver architecture

`KeysightMXA` inherits from `BaseDevice`. The shared base owns the VISA resource manager, address, timeout, terminations, connection, close operation, and context-manager behavior. The MXA layer concentrates on analyzer semantics: frequency, bandwidth, sweep control, traces, markers, triggering, and noise workflows.

The forward control path is

$ "application" -> "KeysightMXA" -> "PyVISA" -> "SCPI" -> "MXA" $

and the measurement path is

$ "MXA" -> "SCPI response" -> "PyVISA" -> "KeysightMXA" -> "Python value" $

Keeping this boundary thin is deliberate: experiment code describes the measurement, while the driver owns protocol details.

= Frequency axis and sweep points

The driver exposes `set_center_freq`, `set_span`, `set_start_stop`, and `set_sweep_points`. `get_frequency_axis()` reconstructs a linear axis from the instrument's start frequency, stop frequency, and number of points:

$ f_i = f_"start" + i (f_"stop" - f_"start") / (N - 1), quad i = 0, ..., N - 1 $

This is valid only when the trace uses a linear frequency grid. A useful consistency check is

$ N >= 2 -> Delta f = (f_"stop" - f_"start") / (N - 1) $

The frequency-bin spacing `Delta f` is not the same thing as RBW. RBW describes the analyzer's resolution filter; point spacing describes how densely the resulting trace is sampled.

= Amplitude, reference level, and attenuation

`set_ref_level()` writes the display reference level in dBm. `set_attenuation()` and `set_attenuation_auto()` control RF input attenuation.

Reference level and RF attenuation are not interchangeable. Lower attenuation can improve sensitivity but reduces overload headroom. Higher attenuation improves headroom but raises the input-referred noise contribution. For reproducible noise measurements, record attenuation and preamplifier state.

= RBW: resolution is also bandwidth

`set_rbw()` emits `BWID` or enables `BWID:AUTO`.

RBW is the analyzer's resolution-bandwidth filter. It determines frequency selectivity and how much random noise power is admitted by the measurement filter.

For approximately white noise, measured noise power scales approximately with effective noise bandwidth:

$ P_"noise" approx S_P dot B_"eff" $

RBW is therefore not merely a visual smoothing parameter. It changes measurement bandwidth and can substantially change swept-acquisition time.

= VBW: post-detection filtering

`set_vbw()` emits `BWID:VID` or enables `BWID:VID:AUTO`.

VBW is a post-detection filter. It mainly reduces visible variation in a noisy trace; it is not a second RF resolution filter in the same sense as RBW.

A useful distinction is

$ "RBW" -> "frequency selectivity and noise bandwidth" $

and

$ "VBW" -> "post-detection smoothing of the measurement statistic" $

The current noise preset uses automatic VBW because exact coupling is analyzer/application dependent. Persisted metadata should record the resolved setting whenever reproducibility matters.

= Detector, averaging, and statistical meaning

The driver exposes detector, average type, average count, and trace averaging controls. These operations are related but not equivalent.

For a power-like quantity, the arithmetic mean is

$ P_"bar" = 1 / N sum_i P_i $

while, in general,

$ log(P_"bar") != 1 / N sum_i log(P_i) $

so averaging after conversion to dB is not equivalent to averaging linear power and then converting to dB.

For statistically meaningful noise measurements, document at least RBW, detector, average type, average count, sweep time, and VBW.

= Sweep time and statistical independence

`set_sweep_duration()` emits `SWE:TIME`; `set_sweep_points()` sets the number of trace points.

Sweep duration controls how long the analyzer spends acquiring a swept trace. It is not synonymous with physical experiment duration or with the number of independent noise samples.

Increasing sweep time, reducing VBW, or increasing trace-average count can reduce estimator variance, but the improvement does not necessarily follow an exact square-root law without a defined statistical model and independence assumption.

The conservative statement is

$ "more averaging" -> "generally lower estimator variance" $

= Noise density, integrated power, and ENBW

`configure_noise_marker()` selects the analyzer's noise-marker mode. The result is interpreted as a spectral noise density, typically reported in dBm/Hz.

The density and an integrated power are different quantities:

$ S_P(f) : "W/Hz" $

and

$ P_"band" = integral_(f_1)^(f_2) S_P(f) dif f : "W" $

A convenient conversion to dBm is

$ P_"dBm" = 10 log_10(P / (1 "mW")) $

The effective noise bandwidth may differ from the displayed RBW, so calibrated conversions should use the effective bandwidth defined for the analyzer/filter rather than blindly equating the two.

For white noise, a useful check is

$ P_2 / P_1 approx B_"eff,2" / B_"eff,1" $

and therefore

$ Delta P approx 10 log_10(B_"eff,2" / B_"eff,1) $

A factor-of-two effective-bandwidth change predicts approximately `3.01 dB`, subject to filter shape, detector behavior, and DUT noise flatness.

= Band-power measurements

`configure_band_power_marker()` selects the band-power function and programs the center and offsets. `get_band_power()` returns the analyzer's integrated result for that band.

The dimensional chain is

$ "W/Hz" times "Hz" -> "W" $

Only after obtaining a power in watts should it be converted to dBm.

= Analyzer noise floor and noise cancellation

A simple linear-power model for uncorrelated DUT and analyzer noise is

$ P_"meas" = P_"DUT" + P_"MXA" $

If two traces are in dBm, subtraction of the displayed values is not power subtraction. Convert to linear power first:

$ P = 10^((P_"dBm" - 30) / 10) $

Then, where the additive-noise model is justified,

$ P_"result" = P_"DUT" - P_"cal" $

Convert the positive result back to dBm as needed. A negative linear result indicates that the assumed signal/background model does not support a positive residual at the measurement uncertainty.

= Trace storage and data transfer

`get_trace_data()` has two transfer modes: ASCII and binary floating point. The binary path uses PyVISA `query_binary_values(..., datatype="f", is_big_endian=True, container=list)` and explicitly specifies endianness.

The important data path is

$ "trace memory" -> "IEEE 488.2 block" -> "VISA" -> "float32 values" -> "Python array" $

Transport format does not define the physical unit of the returned values; the active analyzer mode and trace semantics do.

= What one trace point means

A trace point is a processed measurement result associated with one frequency-axis coordinate. It is not necessarily a single ADC sample.

A useful abstraction is

$ y_i = M[x(t); f_i, RBW, detector, VBW, averaging, "sweep state"] $

where the single-letter `M` denotes the analyzer's configured signal-processing chain.

Changing RBW, detector, averaging mode, or sweep conditions can therefore change the trace even when the DUT itself does not change.

= Acquisition and synchronization

The driver separates configuration from measurement start:

- `set_continuous_sweep(False)` -> `INIT:CONT OFF`
- `initiate_sweep()` -> `INIT:IMM`
- `abort_sweep()` -> `ABOR`
- `single_sweep_wait()` performs a single-sweep initiation followed by `*OPC?` synchronization.

`wait_opc()` establishes instrument-side completion of pending operations. It does not prove that an unrelated external shutter, laser, or trigger event occurred.

The software state machine is

$ "configure" -> "arm / initiate" -> "instrument complete" -> "readout" $

while an externally synchronized experiment may require

$ "prepare" -> "arm" -> "wait-for-event" -> "acquire" -> "OPC" -> "readout" $

Fixed `sleep()` calls are appropriate only when they represent a deliberately characterized physical settling time.

= Triggering and operation status

`wait_for_trigger_ready()` uses the Operation Status Register rather than assuming a fixed delay is sufficient to arm the analyzer.

The relevant states are distinct:

$ "configuration" -> "armed" -> "event" -> "acquisition" -> "complete" -> "read" $

A polling method can establish analyzer state; it cannot by itself prove that the physical source emitted the intended event.

= Optical shutter timing

The MXA does not directly control the optical shutter. `ShutterControl` owns a PSU channel and sets the established trigger voltage to `1.7 V` with a `10 mA` current limit. The orchestration layer must define the ordering of laser setpoint, settling, shutter state, analyzer acquisition, and readout.

A robust experiment distinguishes software ordering from physical settling. The latter is an experimentally characterized property of the laser, shutter, detector, analyzer, and DUT.

= Squeezing and shot-noise comparison

The experiment uses `TRACE_SQZ = 1` and `TRACE_SHOT = 2` and computes a pointwise difference labelled “Squeezing - shot noise”. That label alone is not a complete physical definition.

If the trace values are logarithmic powers in dBm, then

$ P_"sqz,dBm" - P_"shot,dBm" = 10 log_10(P_"sqz" / P_"shot") $

which is a logarithmic power ratio, not a linear power difference.

A true residual power is instead

$ P_"res" = P_"sqz" - P_"shot" $

in linear units before any dB conversion.

For quantum-noise or squeezing analysis, the documentation should state explicitly whether the observable is absolute noise power, noise power spectral density, a ratio relative to shot noise, or a normalized variance.

= Why `record_bw_seq()` is useful

`record_bw_seq()` scans RBW while holding the broader measurement context fixed. For approximately white noise, integrated noise power should increase with effective bandwidth. A factor-of-two increase therefore predicts approximately `+3.01 dB`.

The scan can expose ENBW mismatch, non-flat DUT noise, analyzer-noise dominance, detector or averaging bias, and timing changes caused by sweep coupling.

= Why `record_freq_seq()` is a synchronization experiment

`record_freq_seq()` changes a laser frequency setpoint and waits a configured settling interval before opening the shutter and acquiring data. The current software estimate is

$ t_"relax" = t_"sweep" times N_"avg" $

This is an acquisition-workload estimate, not a demonstrated physical settling time and must not be interpreted as a laser time constant without independent characterization.

The sequence is

$ "setpoint" -> "settle" -> "shutter open" -> "squeezing acquisition" -> "shutter close" -> "shot-noise acquisition" $

Its validity depends on repeatability across those transitions.

= SCPI map: concept to Python to instrument

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

1. *Units are explicit.* Python boundaries use Hz, ms, dBm, and counts rather than implicit unit conventions.
2. *Logarithms are not powers.* Never subtract dBm values when a physical power subtraction is intended.
3. *RBW is not point spacing.* Frequency resolution and trace sampling are separate concepts.
4. *Averaging is a statistical operation.* Detector, VBW, trace averaging, and average type have distinct effects.
5. *Completion is observed.* `*OPC?` is preferable to guessed acquisition delays for instrument completion.
6. *Calibration is contextual.* A background trace is subtractable only when the additive-noise model and instrument state justify it.
7. *Data needs metadata.* A trace without analyzer state is difficult to reproduce and easy to interpret incorrectly.

= Recommended measurement record

For every exported trace, store at least frequency range and point count; RBW and VBW including automatic state; detector and averaging settings; sweep time and single/continuous state; RF attenuation and reference level; trigger settings; trace identity and analyzer mode; calibration/background state; physical experimental state; acquisition timestamp; and instrument identity.

= References

- Keysight, *X-Series Signal Analyzer Programmer's Guide*: general SCPI programming, communication, synchronization, and status. #link("https://www.keysight.com/us/en/assets/7018-06864/programming-guides/9018-06864.pdf")[Guide]
- Keysight, *X-Series Spectrum Analyzer Mode Measurement Guide*: RBW, sweep behavior, detectors, and practical measurement setup. #link("https://www.keysight.com/tn/en/assets/9018-04190/user-manuals/9018-04190.pdf")[Measurement guide]
- Keysight, *Noise Measurements*: detector choice, trace averaging, VBW/RBW behavior, and averaging domains. #link("https://helpfiles.keysight.com/csg/89600B/Webhelp/Subsystems/powerspectrum/content/ps_noisemeasurements.htm")[Noise measurements]
- NIST, *Spectrum Amplitude Definition, Generation, and Measurement*, Technical Note 699: bandwidth and equivalent-bandwidth concepts. #link("https://www.nist.gov/system/files/documents/calibrations/tn699.pdf")[Technical Note 699]
- PyVISA documentation: binary trace transfer, datatype, endianness, and container behavior. #link("https://pyvisa.readthedocs.io/en/1.10.0/api/resources.html")[PyVISA resources]

= Maintenance rule

This document is a living technical record. When code changes, update the conceptual mapping if the instrument behavior, SCPI command, units, timing, calibration assumptions, or acquisition semantics change.

*Always strive for improvement, always be humble.*