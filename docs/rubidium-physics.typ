// iyzee Rubidium physics and polarization self-rotation guide

#import "requirements.typ": *

#set document(
  title: "iyzee Rubidium physics and polarization self-rotation guide",
  author: "iyzee",
)

#set page(
  margin: (x: 2.2cm, y: 2cm),
  header: context [
    #set text(size: 8pt, fill: muted)
    #smallcaps[iyzee]
    #h(1fr)
    RUBIDIUM / PSR
  ],
  footer: context [
    #set text(size: 8pt, fill: muted)
    #h(1fr)
    #counter(page).display("1 / 1", both: true)
  ],
)

#set par(justify: true, leading: 0.58em)
#set heading(numbering: "1.")
#set text(size: 10pt)

#hero(
  "Scientific reference",
  "Rubidium",
  "Atomic structure, D-line spectroscopy, warm-vapor broadening, polarization self-rotation, quantum-noise transfer, and the connection to the measurements made by iyzee."
)

#v(0.65em)

#callout(
  "Purpose",
  [
    This is the physics layer behind the software's Rubidium reference page
    and its squeezing measurement workflow. It distinguishes vacuum reference
    frequencies from observed warm-vapor spectra, classical polarization
    rotation from the quantum squeezing mechanism, and the physical experiment
    from what the current software actually models.
  ],
  tone: "result",
)

#align(center)[#outline(title: [Contents], indent: 1.2em)]
#pagebreak()

= Rubidium in one picture

Rubidium is an alkali atom with one optically active valence electron. That
gives a comparatively simple electronic structure while retaining rich
hyperfine and Zeeman manifolds.

#table(
  columns: (1.2fr, 1.25fr, 1.7fr, 2fr),
  stroke: 0.6pt + hairline,
  inset: 6pt,
  [*Isotope*], [*Nuclear spin*], [*Ground-state F*], [*Optical families*],
  [Rb-85], [I = 5/2], [F = 2, 3], [D1 and D2],
  [Rb-87], [I = 3/2], [F = 1, 2], [D1 and D2],
)

The relevant fine-structure transitions are

$ 5^2 S_"1/2" -> 5^2 P_"1/2" $

for D1 and

$ 5^2 S_"1/2" -> 5^2 P_"3/2" $

for D2.

Their wavelengths are approximately 795 nm and 780 nm. The experiment uses
frequency as its primary optical coordinate because detunings are naturally
expressed in MHz or GHz.

#diagram(
  ```mermaid
flowchart TD
  A["Rb-85 / Rb-87"] --> B["5²S₁/₂ ground manifold"]
  B --> C["5²P₁/₂"]
  B --> D["5²P₃/₂"]
  C --> E["D1 ≈ 795 nm"]
  D --> F["D2 ≈ 780 nm"]```.text,
  caption: [Fine structure: D1 and D2 connect the same ground manifold to the two 5²P manifolds.],
  width: 74%,
)

= Angular momentum and hyperfine structure

The electronic angular momentum is

$ J = L + S $

and the nuclear spin couples to it as

$ F = I + J. $

For fixed I and J, the allowed values are

$ F = I + J, I + J - 1, ..., abs(I - J). $

Define

$ K = F(F+1) - I(I+1) - J(J+1). $

A standard first-order hyperfine energy shift is

$ Delta E_"HFS" =
A K / 2
+
B
(
3 K (K+1) / 4 - I(I+1) J(J+1)
)
/
(
2 I (2I-1) J (2J-1)
). $

For J = 1/2, the electric-quadrupole term vanishes. iyzee does not evaluate
this Hamiltonian dynamically: the source table stores transition frequencies
already assembled from the chosen reference data.

#callout(
  "Keep the angular-momentum layers separate",
  [
    Fine structure separates D1 from D2. Hyperfine structure splits a
    fine-structure manifold into F levels. Zeeman structure resolves M_F
    states in a magnetic field. The current software reference contains the
    fine and hyperfine structure, but does not model Zeeman shifts.
  ],
)

= Optical selection rules

For electric-dipole excitation,

$ Delta F = 0, pm 1 $

with the F = 0 -> F' = 0 transition forbidden.

The transition families represented by the current table are:

#table(
  columns: (1.35fr, 1.2fr, 2.65fr),
  stroke: 0.6pt + hairline,
  inset: 6pt,
  [*Family*], [*Ground F*], [*Allowed excited F'*],
  [D1, Rb-85], [2], [2, 3],
  [D1, Rb-85], [3], [2, 3],
  [D1, Rb-87], [1], [1, 2],
  [D1, Rb-87], [2], [1, 2],
  [D2, Rb-85], [2], [1, 2, 3],
  [D2, Rb-85], [3], [2, 3, 4],
  [D2, Rb-87], [1], [0, 1, 2],
  [D2, Rb-87], [2], [1, 2, 3],
)

The project contains twenty hyperfine transition entries plus four
fine-structure centre references. The parser behind the static TUI page is
#src-link("src/iyzee/tui/screens/rb.py", line: 56).

= Reference frequencies and detuning

The implementation uses vacuum reference frequencies based on the Rubidium
D-line data compiled by Daniel A. Steck. The source representation is THz.

#align(center)[
  #table(
    columns: (1.6fr, 1.8fr, 1.8fr),
    stroke: 0.6pt + hairline,
    inset: 6pt,
    [*Reference*], [*Frequency (THz)*], [*Approx. wavelength (nm)*],
    [Rb-85 D1 centre], [377.107385690], [794.9790],
    [Rb-87 D1 centre], [377.107463500], [794.9789],
    [Rb-85 D2 centre], [384.230406370], [780.2414],
    [Rb-87 D2 centre], [384.230484469], [780.2412],
  )
]

Individual hyperfine transition frequencies are assembled from these centres
and the isotope/hyperfine offsets embedded in
#src-link("src/iyzee/devices/wavemeter.py", line: 99).

#diagram(
  ```mermaid
flowchart LR
  A["Fine-structure centre"] --> B["isotope shift"]
  B --> C["ground-state HFS"]
  C --> D["excited-state HFS"]
  D --> E["transition frequency"]
  E --> F["laser detuning"]```.text,
  caption: [A transition frequency is a difference of two level energies; a fine-structure centre is not itself one hyperfine line.],
  width: 92%,
)

For laser frequency nu and reference frequency nu_0,

$ Delta = nu - nu_0. $

Positive detuning therefore means higher optical frequency; negative detuning
is red of the reference.

In the monitoring utility,

$ Delta_"GHz" = (nu_"laser" - nu_"transition") 10^3 $

because both quantities are represented in THz.

The standard frequency-sweep centre is 377.1052067 THz, approximately
-2.179 GHz from the Rb-85 D1 fine-structure centre. Its detuning from an
individual hyperfine line is different again.

= Warm-vapor spectroscopy

The TUI stick plot is an ideal reference. A warm cell produces broadened and
overlapping resonances.

For longitudinal atomic velocity v_z,

$ Delta nu_"D" approx nu_0 v_z / c. $

A Maxwell-Boltzmann distribution gives a Gaussian Doppler envelope with FWHM

$ Delta nu_"D,FWHM" =
nu_0 sqrt(8 k_B T ln(2) / (m c^2))
. $

At typical warm-vapor temperatures this is hundreds of MHz, substantially
broader than the few-MHz natural linewidth of the optical transition. Pressure
broadening, transit-time effects, power broadening, magnetic fields and
hyperfine overlap further modify the observed profile.

In the simplest Beer-Lambert model,

$ alpha(nu) = n sigma(nu) $

and

$ I(L) = I_0 exp(-alpha L). $

Thus an observed line shape is not a lookup of one transition frequency: it
depends on atomic structure, velocity distribution, collisions, optical depth,
experimental geometry and instrumental response.

#callout(
  "Reference page versus spectroscopy model",
  [
    The Rb page answers "where are the reference transitions?". It does not
    predict the spectrum of a heated cell. A quantitative model would need
    temperature, density, isotope abundance, transition strengths, Doppler
    averaging, pressure broadening, optical depth and magnetic-field inputs.
  ],
)

= Saturation and optical pumping

A useful two-level intuition is the saturation parameter

$ s_0 = I / I_"sat" $

with detuning-dependent form

$ s(Delta) = s_0 / (1 + 4 Delta^2 / Gamma^2). $

The corresponding two-level scattering rate is

$ R_"sc" = Gamma/2 * s(Delta) / (1 + s(Delta)). $

Real rubidium is multilevel. Hyperfine and Zeeman sublevels have
polarization-dependent transition strengths, so repeated scattering changes
the population distribution through optical pumping.

That matters directly for PSR: the probe is not a passive spectator. It changes
the atomic state, so the nonlinear susceptibility depends on intensity,
polarization, detuning and relaxation.

= Polarization self-rotation: classical picture

Take a strong, nearly linearly polarized field. In the circular basis,

$ E_x = (E_+ + E_-) / sqrt(2) $

up to the chosen phase convention.

If the atomic medium gives the two circular components different complex
susceptibilities, they acquire different phase and amplitude changes. In the
simplest birefringent picture,

$ phi = k L (n_+ - n_-) / 2. $

The polarization ellipse therefore rotates. The process is nonlinear because
the differential response itself depends on the optical field and the pumped
atomic state.

#diagram(
  ```mermaid
flowchart LR
  A["Strong linear field"] --> B["σ+ / σ− components"]
  B --> C["Multilevel Rb response"]
  C --> D["different complex susceptibilities"]
  D --> E["relative phase and amplitude"]
  E --> F["polarization self-rotation"]
  F --> G["orthogonal field fluctuations"]```.text,
  caption: [Classical PSR: differential complex response of the circular components generates an intensity-dependent polarization rotation.],
  width: 96%,
)

= PSR as a quantum interaction

For the squeezing picture, treat the strong polarization component as a
classical pump and the orthogonal polarization as a weak quantum field. An
ideal undepleted-pump description couples opposite-frequency sidebands:

$ a_"out"(Omega) =
mu a_"in"(Omega) + nu a_"in"^dagger(-Omega) $

with the lossless Bogoliubov constraint

$ |mu|^2 - |nu|^2 = 1. $

The quadrature

$ X_theta =
(a exp(-i theta) + a^dagger exp(i theta)) / sqrt(2) $

can then have a variance below the vacuum value while the conjugate
quadrature is anti-squeezed.

#diagram(
  ```mermaid
flowchart TD
  A["Strong pump polarization"] --> B["Rb nonlinear interaction"]
  B --> C["+Ω sideband"]
  B --> D["−Ω sideband"]
  C --> E["correlated quadratures"]
  D --> E
  E --> F["squeezed / anti-squeezed output"]```.text,
  caption: [Quantum picture: the nonlinear medium couples opposite-frequency sidebands and can transform vacuum fluctuations into a squeezed state.],
  width: 84%,
)

This is a physical model, not a claim that the current code computes mu and nu.
iyzee currently measures the downstream noise consequence.

= The useful detuning window

Changing optical detuning moves several competing effects at once:

#table(
  columns: (1.55fr, 2fr, 2.35fr),
  stroke: 0.6pt + hairline,
  inset: 6pt,
  [*Effect*], [*Closer to resonance*], [*Further detuned*],
  [Absorption], [generally stronger], [generally weaker],
  [Nonlinear response], [can be stronger], [usually decreases],
  [Optical pumping], [stronger and more state dependent], [weaker],
  [Transmitted power], [can be reduced], [usually larger],
  [Technical contamination], [can couple strongly], [often reduced],
)

There is no universal best detuning. The optimum depends on vapor temperature,
optical depth, pump intensity, cell length, magnetic field, polarization
purity, spatial mode, losses and detection efficiency.

A frequency scan is consequently valuable because it maps the actual response
of the complete apparatus rather than choosing a point from a vacuum line table.

= Detection chain: optical state to RF spectrum

The MXA observes an electrical signal, not the optical field directly.

#diagram(
  ```mermaid
flowchart LR
  A["Rb vapor"] --> B["polarization analysis"]
  B --> C["photodetector(s)"]
  C --> D["RF / electrical chain"]
  D --> E["MXA"]
  E --> F["measured noise spectrum"]
  G["reference state"] --> C```.text,
  caption: [The detector and RF chain are part of the measurement, not transparent observers of the atomic state.],
)

For total detection efficiency eta, optical loss mixes the field with vacuum.
A simple variance model is

$ V_"meas" = eta V_"field" + (1-eta) V_"vac". $

Mode mismatch, detector inefficiency and electronics noise therefore reduce
the observable squeezing even when the state leaving the cell is more strongly
squeezed.

= Squeezing versus shot noise

The current frequency step follows a deliberate sequence: set the laser
frequency, wait, read the wavemeter, acquire the squeezing trace with the
shutter open, close the shutter, and acquire the shot-noise reference.

#diagram(
  ```mermaid
flowchart LR
  A["Set laser setpoint"] --> B["wait"]
  B --> C["read wavemeter"]
  C --> D["open shutter"]
  D --> E["measure squeezing"]
  E --> F["close shutter"]
  F --> G["measure shot noise"]```.text,
  caption: [Current frequency-step ordering in the experiment code.],
  width: 96%,
)

The implementation is #src-link("src/iyzee/experiment/procedures.py", line: 98).

#table(
  columns: (1.2fr, 2fr, 2.6fr),
  stroke: 0.6pt + hairline,
  inset: 6pt,
  [*Trace*], [*Optical state*], [*Interpretation*],
  [Squeezing], [PSR-generated field present], [target quadrature noise],
  [Shot noise], [reference state], [quantum-noise baseline],
  [Electronics background], [separate characterization], [instrument floor],
)

A lower noise trace than the shot-noise reference is meaningful only after
relevant detector gain, optical power, bandwidth, and reference conditions
have been matched.

#callout(
  "Logarithmic and linear operations are different",
  [
    A difference in dBm is a ratio:
    $ P_"dB" - Q_"dB" = 10 log_10(P / Q). $
    It is not a physical power subtraction. Convert dBm to linear power before
    subtracting backgrounds or references.
  ],
  tone: "warning",
)

= Mapping the physics into iyzee

The current implementation exposes a compact set of experimental controls:

#table(
  columns: (1.65fr, 1.75fr, 2.6fr),
  stroke: 0.6pt + hairline,
  inset: 6pt,
  [*Physical quantity*], [*Software representation*], [*Role*],
  [Laser frequency], [frequency_thz + wavemeter channel], [sets optical detuning],
  [Measured frequency], [read_frequency()], [checks the actual laser frequency],
  [Optical state], [shutter + optical setup], [selects the squeezed/reference path],
  [RF analysis frequency], [center_hz], [sets the analyzed noise frequency],
  [RBW / VBW], [AnalyzerConfig + BandwidthStep], [sets spectral resolution and estimator behavior],
  [Averaging], [avg_count + avg_type], [controls variance and acquisition cost],
  [Reference], [shot-noise trace], [defines the comparison baseline],
)

The sequence runner is #src-link("src/iyzee/experiment/core.py", line: 152).
The analyzer configuration is #src-link("src/iyzee/experiment/procedures.py", line: 26).

= What the software does not infer

The present Rubidium path does not automatically infer:

- vapor temperature or density from a spectrum;
- absolute detuning from a fitted absorption profile;
- transition strengths from the stick height;
- Zeeman shifts from the ambient magnetic field;
- a complete multilevel susceptibility;
- a theoretical squeezing spectrum from the reference frequencies alone.

Those are scientifically meaningful extensions, but each requires additional
measurements or model parameters. The current implementation intentionally
keeps the reference table and measurement workflow small.

= References and further reading

#table(
  columns: (0.35fr, 4.9fr),
  stroke: 0.6pt + hairline,
  inset: 5pt,
  [*Ref.*], [*Source*],
  [1], [D. A. Steck, _Alkali D Line Data - Rubidium 85_. #link("https://steck.us/alkalidata/rubidium85numbers.pdf")[PDF]],
  [2], [D. A. Steck, _Alkali D Line Data - Rubidium 87_. #link("https://steck.us/alkalidata/rubidium87numbers.1.6.pdf")[PDF]],
  [3], [J. Ries, B. Brezger and A. I. Lvovsky, _Experimental vacuum squeezing in rubidium vapor via self-rotation_, Phys. Rev. A 68, 025801 (2003). #link("https://doi.org/10.1103/PhysRevA.68.025801")[DOI]],
  [4], [M. T. L. Hsu et al., _Effect of atomic noise on optical squeezing via polarization self-rotation in a thermal vapor cell_, Phys. Rev. A 73, 023806 (2006). #link("https://doi.org/10.1103/PhysRevA.73.023806")[DOI]],
  [5], [M. Zhang et al., _Spatial multimode structure of atom-generated squeezed light_, Phys. Rev. A 93, 013853 (2016). #link("https://doi.org/10.1103/PhysRevA.93.013853")[DOI]],
  [6], [M. Zhang et al., _Multipass configuration for improved squeezed vacuum generation in hot Rb vapor_, Phys. Rev. A 96, 013835 (2017). #link("https://doi.org/10.1103/PhysRevA.96.013835")[DOI]],
  [7], [O. Katz et al., _Coherent polarization self-rotation_, Phys. Rev. A 113, 043715 (2026). #link("https://doi.org/10.1103/3m6y-sfx8")[DOI]],
)

The 2003 experiment established vacuum squeezing through polarization
self-rotation in rubidium vapor. Later work showed that atomic noise, spatial
multimode structure and geometry can strongly influence what is actually
observed. Recent coherent-PSR work is useful when distinguishing conventional
polarization rotation from explicitly coherent spin-light interactions.

= Scientific maintenance rule

Reference frequencies are reference data, not calibration truth. A vacuum
frequency locates an atomic feature; a warm-cell observation is a broadened,
shifted and power-dependent physical response.

Whenever the transition convention, reference data, detuning definition,
measurement ordering, squeezing interpretation or physical assumptions in the
code change, update this guide with the implementation.

*Always strive for improvement, always be humble.*
