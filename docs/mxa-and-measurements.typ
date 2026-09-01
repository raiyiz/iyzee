// iyzee MXA and measurement guide

#import "@preview/physica:0.9.8": grad, pdv

#set page(margin: 2.2cm)
#set text(size: 10pt)

= MXA control and measurement model

*Status:* engineering documentation for the current `moonshine` implementation. SCPI relationships below are derived from the Keysight X-Series programming documentation and from the commands actually emitted by `src/iyzee/mxa.py`.

== Reading this document

The Keysight X-Series Programmer's Guide is the general programming layer: it explains SCPI syntax, communication, synchronization, and programming techniques. Application-specific command definitions belong to the relevant User's and Programmer's Reference rather than the general guide. This distinction is important when reading this project: the Python driver is not a second specification of the analyzer; it is a small, opinionated interface over selected SCPI operations.

Keysight describes the X-Series programming model as SCPI-based and distinguishes the general Programmer's Guide from application-specific references. The MXA spectrum-analyzer reference is therefore the authoritative place to resolve an individual measurement command or option. The project should link a driver method to that instrument vocabulary rather than inventing a parallel mental model. 

== The driver at a glance

`KeysightMXA` inherits from `BaseDevice`. The shared base owns the VISA resource manager, address, timeout, terminations, connection, close operation, and context-manager behavior. The MXA layer consequently concentrates on analyzer semantics: frequency, bandwidth, sweep control, traces, markers, triggering, and noise workflows.

The basic control path is:

$ application -> KeysightMXA -> PyVISA -> SCPI -> MXA $

The reverse path is:

$ MXA -> SCPI response -> PyVISA -> KeysightMXA -> typed Python value $

This is intentionally thin. For example, `set_center_freq(2.4e9)` emits `FREQ:CENT 2400000000`; `get_sweep_points()` queries `SWE:POIN?` and converts the response to an integer.

== Frequency, span, and amplitude

The driver exposes three closely related frequency operations:

- `set_center_freq(freq_hz)` -> `FREQ:CENT`
- `set_span(span_hz)` -> `FREQ:SPAN`
- `set_start_stop(start_hz, stop_hz)` -> `FREQ:STAR` and `FREQ:STOP`

These form the frequency-domain acquisition window. The helper `get_frequency_axis()` reconstructs a linear axis from start, stop, and point count:

$ f_i = f_start + i (f_stop - f_start)/(N - 1), quad i in 0, ..., N-1 $

The driver also exposes reference level and RF attenuation. These are deliberately separate concepts: the reference level controls the displayed vertical reference while attenuation changes the RF input path. The actual analyzer's limits, coupling, preamplifier state, and protection behavior remain instrument-level concerns.

== RBW and VBW

`set_rbw()` emits `BWID` or enables `BWID:AUTO`. `set_vbw()` emits `BWID:VID` or enables `BWID:VID:AUTO`.

For a spectrum measurement, RBW is part of the resolution of the frequency-domain measurement, while VBW applies video filtering after detection. They should therefore be documented as measurement parameters rather than generic software smoothing knobs.

A future cleanup should make all bandwidth units explicit in persisted configuration and user-facing documentation. The current driver names them in Hz, which is good at the Python boundary; the experiment configuration should preserve that convention end-to-end.

== Acquisition and synchronization

The driver separates *arming/configuration* from *starting a sweep*:

- `set_continuous_sweep(False)` -> `INIT:CONT OFF`
- `initiate_sweep()` -> `INIT:IMM`
- `abort_sweep()` -> `ABOR`
- `single_sweep_wait()` combines single-sweep mode, immediate initiation, and `*OPC?` synchronization.

This matters because issuing a command is not equivalent to having a completed measurement. The `wait_opc()` helper explicitly queries `*OPC?`, temporarily applies a bounded VISA timeout, and restores the previous timeout afterward.

Conceptually, an acquisition is only complete when

$ configured -> initiated -> operation-complete $

has been established. This is preferable to relying on a fixed sleep, because the instrument's actual execution time depends on sweep settings and measurement state.

== Traces

The driver models traces as analyzer-side storage and display objects rather than ordinary Python arrays.

`set_trace_mode()` maps to `:TRACe<n>:TYPE`, while `set_trace_update()` and `set_trace_display()` control whether a trace updates and is shown. `clear_trace()` clears the instrument-side trace. `get_trace_data()` configures the data format and then queries `:TRACe:DATA?`.

The driver currently supports two transfer representations:

- binary IEEE 488.2-style floating-point data, requested with `FORMat:DATA REAL,32`;
- ASCII data, requested with `FORMat:DATA ASCii`.

Binary transfer is the normal path for larger traces because it avoids textual conversion overhead and preserves a predictable element representation. The driver deliberately keeps this detail near the instrument boundary rather than leaking PyVISA calls into experiment code.

== Averaging and noise

Noise measurements are especially sensitive to the distinction between detector behavior and averaging behavior. The driver exposes:

- `set_detector()`;
- `set_average_type()`;
- `set_average_count()`;
- trace averaging via `set_trace_mode()`;
- a convenience preset `configure_noise_measurement()`.

The preset configures center frequency, span, RBW, automatic VBW, optional RMS detection, averaging count, and single-sweep mode. It does not itself perform an acquisition. This is an important boundary: configuration describes *what should be measured*; the caller decides *when and how often it is acquired*.

For an averaged sequence of measured powers $P_i$, the estimator depends on the instrument's averaging mode. The documentation should therefore avoid casually calling every average a generic arithmetic mean. `AVER:TYPE RMS` is a specific analyzer operation and should be interpreted using the instrument reference for the selected application.

== Noise markers and band power

`configure_noise_marker()` enables a marker, selects `NOIS`, and optionally places it at a requested frequency. The driver documents the result as noise density in dBm/Hz.

`configure_band_power_marker()` selects `BAND`, sets the marker frequency, and programs left/right offsets. `get_band_power()` then reads the marker result.

These two operations answer different scientific questions:

- noise marker: local spectral noise density;
- band-power marker: integrated power over a defined frequency region.

A useful dimensional check is that integrating a power spectral density over bandwidth produces power:

$ P = integral_(f_1)^(f_2) S_P(f) dif f $

The units must be kept straight. A quantity reported in dBm/Hz is not interchangeable with a quantity reported in dBm.

== Trace math and noise cancellation

The driver contains `_set_trace_math()` as a low-level wrapper for analyzer trace math. `apply_trace_math_noise_cancel()` implements the beginning of a calibration/DUT workflow: average a calibration trace, then average a DUT trace.

The intended conceptual operation is a comparison of measured powers. In linear power units, a subtraction can represent removal of a calibrated noise contribution:

$ P_result = P_DUT - P_cal $

but subtraction in logarithmic dB units is *not* the same operation. If the instrument trace is expressed in dBm, the corresponding linear-power calculation is

$ P_W = 10^((P_(dBm) - 30)/10) $

and any physical subtraction should be justified in that domain before converting back to dBm. This is precisely the kind of physics/software boundary that should remain explicit in project documentation.

The current method is intentionally not presented as a universal noise-cancellation algorithm. It is a concrete analyzer workflow whose exact validity depends on calibration conditions, detector/averaging settings, and the physical noise model.

== Triggering

The driver exposes immediate, video, external, RF-burst, and frame trigger sources. It also provides video and external trigger levels and positive/negative slope selection.

`wait_for_trigger_ready()` uses the Operation Status Register rather than sleeping for an assumed preparation time. It enables the relevant operation-status bit and polls until the analyzer reports the armed/waiting state.

This distinction is central for single-burst measurements:

$ arm -> wait-for-ready -> external-event -> acquire -> OPC $

The exact trigger semantics remain instrument/application specific. The method is a synchronization primitive, not a guarantee that a particular physical event occurred.

== Shutter and optical timing

The MXA does not control the optical shutter directly. `ShutterControl` owns a PSU channel and sets the established trigger voltage to *1.7 V* with a 10 mA current limit. `open()` enables the selected channel; `close()` disables it. Context-manager exit closes the shutter and releases the PSU connection.

The experiment therefore has two distinct layers:

$ optical\ gate -> shutter\ state $

and

$ RF\ analyzer -> acquisition\ state $

When an experiment needs these to be synchronized, the orchestration layer should make the ordering explicit rather than hiding timing inside either instrument driver.

== What the X-Series guide tells us, and what the code actually uses

The useful relationship is not “the guide says the project works this way.” It is:

#table(
  columns: (1.2fr, 1.5fr, 2.3fr),
  stroke: .5pt,
  inset: 6pt,
  [Instrument concept], [Project method], [Actual SCPI boundary],
  [Identification], [`idn()`], [`*IDN?`],
  [Reset/status], [`reset()`], [`*RST`, `*CLS`],
  [Synchronization], [`wait_opc()`], [`*OPC?`],
  [Error handling], [`get_errors()`], [`SYST:ERR?`],
  [Frequency], [`set_center_freq`, `set_span`, `set_start_stop`], [`FREQ:CENT`, `FREQ:SPAN`, `FREQ:STAR`, `FREQ:STOP`],
  [Bandwidth], [`set_rbw`, `set_vbw`], [`BWID`, `BWID:VID`],
  [Sweep], [`set_continuous_sweep`, `initiate_sweep`, `abort_sweep`], [`INIT:CONT`, `INIT:IMM`, `ABOR`],
  [Trace data], [`get_trace_data()`], [`FORMat:DATA`, `:TRACe:DATA?`],
  [Markers], [`set_marker_*`, `get_marker_*`], [`CALC:MARK...`],
  [Trigger], [`set_trigger_*`, `wait_for_trigger_ready()`], [`TRIG:*`, `STAT:OPER:*`],
)

This table is intentionally limited to functionality present in the repository. It should grow as the driver grows, and it should be updated when a method changes its SCPI behavior.

== Engineering interpretation

The driver is strongest when its abstractions correspond to stable instrument concepts and weakest when a convenience method hides assumptions about a physical experiment. The cleanup therefore favors three rules:

1. keep SCPI visible enough that an engineer can correlate Python with the instrument reference;
2. keep physical units explicit at the Python boundary;
3. keep acquisition sequencing explicit and testable.

For example, a quantity $q$ should carry a declared unit $[q]$ rather than relying on a reader to infer whether `1e9` means Hz, rad/s, or something else. Likewise, a completed acquisition should be an observed state, not an assumed delay.

== References

- Keysight, *X-Series Signal Analyzer Programmer's Guide*, publication N9020-90112: general SCPI programming, communication, synchronization, and programming techniques.
- Keysight, *X-Series Signal Analyzer Spectrum Analyzer Mode User's and Programmer's Reference*, publication 9018-06099: application-specific spectrum-analyzer commands and programming information.
- Repository implementation: `src/iyzee/mxa.py`, `src/iyzee/power.py`, and the associated tests.

== Maintenance rule

This document is deliberately a living technical record. When code changes, update the corresponding conceptual mapping if the instrument behavior, SCPI command, units, or acquisition semantics changed.

*Always strive for improvement, always be humble.*
