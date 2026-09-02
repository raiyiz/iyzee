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

*Status:* engineering documentation for the current `moonshine` implementation.

#align(center)[#outline(title: [Contents], indent: 1.2em)]

#pagebreak()

= Purpose and scope

The purpose of this document is not to reproduce the instrument manual. It connects four layers that must agree in a real experiment:

$ "physics" -> "measurement method" -> "analyzer state" -> "Python data" $

A value is scientifically useful only when its units, bandwidth, detector, averaging, timing, and calibration context are known.

== Reading this document

The Keysight X-Series Programmer's Guide is the general SCPI programming layer: syntax, communication, synchronization, status, and programming techniques. Application-specific command definitions belong to the relevant User's and Programmer's Reference. The Python driver is therefore a small, opinionated interface over selected SCPI operations, not a replacement for the analyzer reference.

A practical rule is: when a method changes analyzer behavior, document the *instrument concept*, the *SCPI command*, and the *physical meaning*. When a method only moves bytes, document the transport separately.

= The measurement chain

For the experiments represented in this repository, the useful mental model is

$ "DUT / optical system" -> "RF signal" -> "MXA input" -> "attenuation / preamp" -> "mixer / IF" -> "RBW filter" -> "detector" -> "VBW / averaging" -> "trace" -> "export" $

Each stage can alter the measured quantity. In particular, RBW changes the effective measurement bandwidth; the detector changes how the detected samples are formed; VBW and trace averaging reduce variation after detection; analyzer noise adds to the measured power; and reference level/attenuation affect the instrument operating point.

The MXA is therefore not a transparent voltmeter with a frequency axis. It is a signal-processing instrument whose displayed trace is the output of a configured measurement chain.

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

$ f_i = f_start + i (f_stop - f_start) / (N - 1), quad i = 0, ..., N - 1 $

This is valid only when the trace is represented on a linear frequency grid. The axis is metadata, not part of the returned trace values, so persisted measurements should save the frequency configuration with the trace.

A useful consistency check is

$ N >= 2 -> Delta f = (f_stop - f_start) / (N - 1) $

The frequency-bin spacing `Delta f` is not the same thing as RBW. RBW describes the analyzer's resolution filter; point spacing describes how densely the resulting trace is sampled/displayed.

= Amplitude, reference level, and attenuation

`set_ref_level()` writes the display reference level in dBm. `set_attenuation()` and `set_attenuation_auto()` control the RF input attenuation.

These are not interchangeable:

- *reference level* sets the vertical operating/display reference;
- *RF attenuation* changes the input attenuation and therefore the signal level presented to later stages.

Lower attenuation can improve sensitivity but reduces headroom before overload. Higher attenuation improves protection/headroom but raises the effective input-referred noise contribution. Preamp state, mixer level, and analyzer-specific protection limits remain instrument-level concerns.

For reproducible noise measurements, record attenuation and preamplifier state rather than treating the trace as fully specified by center frequency and RBW alone.

= RBW: resolution is also bandwidth

`set_rbw()` emits `BWID` or enables `BWID:AUTO`.

RBW is the analyzer's resolution-bandwidth filter. In a traditional swept measurement it is the IF filter that determines the minimum separation at which two spectral components can be resolved. It also sets how much random noise power is admitted by the measurement filter.

For approximately white noise, measured noise power scales approximately with the effective noise bandwidth:

$ P_"noise" approx S_P dot B_"eff" $

so increasing RBW generally increases displayed noise power. Conversely, narrowing RBW lowers both the DUT contribution and the analyzer's own displayed noise.

RBW is therefore not merely a visual smoothing parameter. It changes the measurement bandwidth and the time required to acquire a swept measurement. Keysight notes that decreasing RBW in swept measurements can increase sweep time by roughly an order of magnitude for the next lower 1--3--10 setting; the exact relationship depends on analyzer architecture, detector, span, and coupling.

This gives `record_bw_seq()` a physical interpretation: the experiment is deliberately changing the measurement bandwidth and observing how the reported noise scales with it.

= VBW: post-detection filtering

`set_vbw()` emits `BWID:VID` or enables `BWID:VID:AUTO`.

VBW is a video/post-detection filter. It acts after the spectrum has been detected and mainly reduces the visible variance of a noisy trace; it does not represent a second RF resolution filter in the same sense as RBW.

For noise-like signals, a smaller VBW/RBW ratio can reduce point-to-point fluctuations. This is useful when the scientific goal is a stable estimator, but it must not be described as if the underlying physical noise power had disappeared.

A useful distinction is

$ "RBW" -> "frequency selectivity and noise bandwidth" $

versus

$ "VBW" -> "post-detection smoothing of the measurement statistic" $

The current noise preset uses automatic VBW because exact coupling is analyzer/application dependent. Persisted experiment metadata should therefore record the resolved instrument setting whenever exact reproducibility matters.

= Detector, averaging, and statistical meaning

The driver exposes `set_detector()`, `set_average_type()`, `set_average_count()`, and trace averaging through `set_trace_mode()`.

These operations are related but not equivalent.

*Detector* determines how samples within the sweep are combined around each displayed point. For noise-like signals, Keysight recommends sample/average-style detection rather than peak-oriented detection when estimating noise statistics.

*Trace averaging* averages corresponding trace points from successive sweeps.

*VBW filtering* averages/smooths in the post-detection path.

*Average type* determines the domain in which the analyzer averages a measurement. This is critical for logarithmic displays.

For a power-like quantity, the physically relevant arithmetic mean is

$ P_"bar" = 1 / N sum_i P_i $

but, in general,

$ log(P_"bar") != 1 / N sum_i log(P_i) $

so averaging after conversion to dB is not equivalent to averaging linear power and then converting to dB.

Keysight specifically cautions that logarithmic averaging of noise can introduce a systematic error of up to about `-2.51 dB`; power/RMS averaging is preferred for accurate noise measurements. The code comment associated with `set_average_type()` therefore reflects a measurement principle, not just a UI preference.

For statistically meaningful noise measurements, document at least RBW, detector, average type, average count, sweep time, and VBW.

= Sweep time and statistical independence

`set_sweep_duration()` emits `SWE:TIME`; `set_sweep_points()` sets the number of trace points.

Sweep duration controls how long the analyzer spends acquiring a swept trace. It is not synonymous with the duration of the physical experiment or with the number of independent noise samples.

The number of statistically useful samples depends on the analyzer's detection/filtering path and on the noise correlation time. Increasing sweep time, reducing VBW, or increasing trace-average count can reduce variance, but the improvement does not necessarily follow an exact square-root law without a defined statistical model and independence assumption.

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

$ P_2 / P_1 approx B_2 / B_1 $

where `B_1` and `B_2` denote effective bandwidths of measurements 1 and 2.

In dB, the corresponding change is

$ Delta P approx 10 log_10(B_2 / B_1) $

A factor-of-two effective-bandwidth change predicts approximately `3.01 dB`, subject to filter shape, detector behavior, and DUT noise flatness.

= Band-power measurements

`configure_band_power_marker()` selects the band-power function and programs the center and offsets. `get_band_power()` returns the analyzer's integrated result for that band.

The dimensional chain is

$ "W/Hz" times "Hz" -> "W" $

= Analyzer noise floor and noise cancellation

The analyzer contributes its own noise. The measured power is therefore not generally equal to DUT power alone. A simple model is

$ P_"meas" = P_"DUT" + P_"MXA" $

in linear power units, assuming the contributions are uncorrelated and the measurement chain is otherwise unchanged.

Keysight notes that analyzer noise can both bias the measured power upward and increase result variance, especially near the analyzer noise floor. Reducing RBW reduces both DUT noise and analyzer noise; averaging, VBW reduction, and suitable detectors mainly reduce variance.

The repository's `apply_trace_math_noise_cancel()` follows the useful calibration pattern “measure background, measure DUT, then combine.” But subtraction is only physically justified when the calibration trace represents the same additive noise contribution under the same relevant instrument state.

If two traces are in dBm, subtraction of the displayed values is not power subtraction. Convert first:

$ P_W = 10^((P_"dBm" - 30) / 10) $

then, where the model justifies it,

$ P_"result" = P_"DUT" - P_"cal" $

and finally convert the positive result back to dBm.

A negative linear result is not “negative dBm power”; it means the assumed signal/background model is inconsistent with the measured values or the desired quantity is below the subtraction uncertainty.

This method should therefore remain documented as a specific calibration workflow, not a universal noise-cancellation algorithm.

= Trace storage and data transfer

The driver models traces as analyzer-side memory/display objects. `set_trace_mode()` maps to `:TRACe<n>:TYPE`; `set_trace_update()` controls trace updating; `set_trace_display()` controls display; `clear_trace()` clears instrument-side trace storage.

`get_trace_data()` has two transfer modes:

- ASCII via `FORMat:DATA ASCii`;
- binary floating point via `FORMat:DATA REAL,32` and `FORMat:BORDer NORM`.

The binary path uses PyVISA `query_binary_values(..., datatype="f", is_big_endian=True, container=list)`. PyVISA interprets the returned IEEE-style block and converts its elements to Python values. The project explicitly specifies endianness rather than relying on the library default.

The important data path is

$ "trace memory" -> "IEEE 488.2 block" -> "VISA" -> "float32 values" -> "Python array" $

Binary transfer reduces textual conversion and transport overhead for larger traces. ASCII remains useful for debugging because the representation is human-readable.

The numerical values in `get_trace_data()` must still be interpreted using the active analyzer mode and trace semantics. Transport format does not define the physical unit.

= What one trace point means

A trace point is a processed measurement result associated with one frequency-axis coordinate. It is not necessarily a single ADC sample.

A useful abstraction is

$ y_i = cal(M)[x(t); f_i, RBW, detector, VBW, averaging, "sweep state"] $

where `cal(M)` denotes the analyzer's configured signal-processing chain.

This is why changing RBW, detector, averaging mode, or sweep conditions can change the trace even when the DUT does not change.

Persisted data should therefore travel with sufficient metadata to reconstruct `cal(M)`: at minimum center/start/stop frequency, points, RBW, VBW, detector, averaging settings, sweep time, attenuation/reference level, trigger configuration, and the experiment state.

= Acquisition and synchronization

The driver separates configuration from measurement start:

- `set_continuous_sweep(False)` -> `INIT:CONT OFF`
- `initiate_sweep()` -> `INIT:IMM`
- `abort_sweep()` -> `ABOR`
- `single_sweep_wait()` performs single-sweep initiation followed by `*OPC?` synchronization.

`wait_opc()` temporarily adjusts the VISA timeout, queries `*OPC?`, and restores the previous timeout.

The critical distinction is between *command completion* and *physical-event completion*. `*OPC?` establishes that pending instrument operations have completed from the instrument's perspective. It does not mean that an unrelated external shutter, laser, or trigger event occurred.

The software state machine is therefore

$ "configure" -> "arm / initiate" -> "instrument complete" -> "readout" $

while an externally synchronized experiment may require

$ "prepare" -> "arm" -> "wait-for-event" -> "acquire" -> "OPC" -> "readout" $

Fixed `sleep()` calls are appropriate only when they represent a deliberately characterized physical settling time. They are not substitutes for instrument synchronization.

= Triggering and operation status

The driver exposes immediate, video, external, RF-burst, and frame trigger sources, plus video/external levels and slope selection.

`wait_for_trigger_ready()` uses the Operation Status Register rather than assuming that a fixed delay is enough to arm the analyzer. It enables the relevant operation-status bit and polls `STAT:OPER:EVEN?` until the instrument reports the expected state.

This matters because trigger readiness, trigger occurrence, acquisition completion, and data availability are different states:

$ "configuration" -> "armed" -> "event" -> "acquisition" -> "complete" -> "read" $

A polling method can establish analyzer state; it cannot by itself prove that the physical source emitted the intended event.

= Optical shutter timing

The MXA does not directly control the optical shutter. `ShutterControl` owns a PSU channel and sets the established trigger voltage to `1.7 V` with a `10 mA` current limit. `open()` enables the selected channel; `close()` disables it; context-manager exit closes the shutter and releases the PSU connection.

The experiment therefore spans two state machines:

$ "optical source" -> "shutter state" $

and

$ "RF analyzer" -> "trigger / acquisition state" $

The orchestration layer must define their ordering explicitly. In `record_freq_seq()`, for example, the laser setpoint is changed, a settling interval is applied, the shutter is opened, the squeezing trace is acquired, the shutter is closed, and the shot-noise trace is then acquired. The meaning of the difference depends on whether those states are physically stationary over the complete sequence.

A robust experiment should distinguish *software ordering* from *physical settling*. The latter is an experimentally characterized property of the laser, shutter, DUT, and environment.

= The squeezing / shot-noise comparison

The experiment code uses `TRACE_SQZ = 1` and `TRACE_SHOT = 2`, then computes a pointwise difference labelled “Squeezing - shot noise”. This label is useful as an experiment-level description but is not, by itself, a complete physical definition.

If the trace values are logarithmic powers in dBm, then

$ P_"sqz,dBm" - P_"shot,dBm" = 10 log_10(P_"sqz" / P_"shot") $

which is a *power ratio in logarithmic units*, not a linear power difference.

By contrast, a true residual power requires

$ P_"res" = P_"sqz" - P_"shot" $

in linear units before conversion to dB. Whether the experiment should use a difference or a ratio depends on the scientific observable being defined.

For quantum-noise or squeezing analysis, the documentation should state explicitly whether the reported quantity is:

- absolute noise power;
- noise power spectral density;
- a ratio relative to shot noise;
- or a normalized variance.

That distinction prevents a mathematically valid Python operation from being mistaken for a physically defined observable.

= Why `record_bw_seq()` is scientifically useful

`record_bw_seq()` scans RBW while keeping the broad measurement context fixed. For white noise with approximately constant spectral density, integrated noise power should grow with effective bandwidth. A factor-of-two increase therefore predicts approximately `+3.01 dB` in integrated power.

The scan is useful because it can expose several non-idealities:

- ENBW differs from nominal RBW;
- the DUT noise is not spectrally flat;
- analyzer noise becomes significant;
- detector/averaging settings bias or broaden the estimator;
- sweep coupling changes timing and effective statistics.

The expected scaling is therefore a diagnostic model, not a command-level invariant.

= Why `record_freq_seq()` is a synchronization experiment

`record_freq_seq()` changes a laser frequency setpoint and waits a configured settling interval before opening the shutter and acquiring data. The relaxation time is currently derived from sweep duration and average count:

$ t_"relax" = t_"sweep" times N_"avg" $

This is a software estimate of acquisition workload, not a demonstrated physical settling time. It should not be interpreted as a laser time constant unless independently characterized.

The sequence is best understood as a stateful experiment:

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

A useful measurement driver should preserve a few invariants:

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

The aim is not bureaucratic metadata. Each field corresponds to a variable that can change the numerical meaning of the trace.

= References

- Keysight, *X-Series Signal Analyzer Programmer's Guide*: general SCPI programming, communication, synchronization, status, and programming techniques.
- Keysight, *X-Series Spectrum Analyzer Mode Measurement Guide*: RBW, sweep behavior, detectors, and practical measurement setup.
- Keysight, *Noise Measurements*: detector choice, trace averaging, VBW/RBW behavior, and logarithmic-versus-power averaging.
- NIST, *Spectrum Amplitude Definition, Generation, and Measurement*, Technical Note 699: bandwidth definitions and equivalent-bandwidth concepts.
- PyVISA documentation, `query_binary_values()`: binary block decoding, datatype, endianness, and container behavior.
- Repository implementation: `src/iyzee/mxa.py`, `src/iyzee/power.py`, `src/iyzee/main.py`, and the associated tests.

= Maintenance rule

This document is a living technical record. When code changes, update the corresponding conceptual mapping if the instrument behavior, SCPI command, units, timing, calibration assumptions, or acquisition semantics change.

*Always strive for improvement, always be humble.*
