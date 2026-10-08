// iyzee Rubidium physics and polarization self-rotation guide
#import "requirements.typ": *
#import "rb-figures.typ": *

#part(
  "Scientific reference",
  "Rubidium",
  "Atomic structure, D-line spectroscopy, warm-vapor broadening, polarization self-rotation, quantum-noise transfer, and the connection to the measurements made by iyzee.",
  id: "part-rubidium",
)

#callout(
  "Purpose",
  [
    This is the physics layer behind the software's Rubidium reference page
    and its squeezing measurement workflow. It separates vacuum reference
    frequencies from observed warm-vapor spectra, classical polarization
    rotation from the quantum squeezing mechanism, and the physical experiment
    from what the current software actually models.
  ],
  tone: "result",
)

#v(0.5em)

*Where the numbers come from.* Every figure and table of transition data in
this part is generated from the same `Rb_transitions` table the application
uses (#file("src/iyzee/devices/wavemeter.py", label: "source")), through
`docs/data/rubidium.json`; a test fails if the two disagree. Atomic constants
are D. A. Steck's _Alkali D Line Data_ (refs. 1 and 2); the table was checked
against them line by line.

= Rubidium in one picture

Rubidium is an alkali atom with one optically active valence electron. That
gives a comparatively simple electronic structure while retaining rich
hyperfine and Zeeman manifolds. Natural rubidium is a mixture of two isotopes
with different nuclear spins, and therefore different hyperfine structure:

#figure(
  table(
    columns: (1.1fr, 1fr, 0.9fr, 1.2fr, 1.4fr, 1.5fr, 1.5fr),
    align: (left, right, center, center, right, right, right),
    table.header([*Isotope*], [*Abundance*], [*Spin I*], [*Ground F*], [*Ground splitting*], [*D1 centre*], [*D2 centre*]),
    [#text(fill: rb85, weight: "bold")[#iso-name("85")]], [72.17 %], [5/2], [2, 3], [#fmt(3.0357324390, 4) GHz],
    [#fmt(wavelength-nm(rb-data.centers_thz.at("85").D1), 4) nm], [#fmt(wavelength-nm(rb-data.centers_thz.at("85").D2), 4) nm],
    [#text(fill: rb87, weight: "bold")[#iso-name("87")]], [27.83 %], [3/2], [1, 2], [#fmt(6.8346826109, 4) GHz],
    [#fmt(wavelength-nm(rb-data.centers_thz.at("87").D1), 4) nm], [#fmt(wavelength-nm(rb-data.centers_thz.at("87").D2), 4) nm],
  ),
  caption: [The two naturally occurring isotopes (vacuum wavelengths from the table's centre frequencies; abundances and ground splittings from Steck).],
)

The relevant fine-structure transitions are

$ 5^2 S_"1/2" -> 5^2 P_"1/2" $

for D1 and

$ 5^2 S_"1/2" -> 5^2 P_"3/2" $

for D2, at about 795 nm and 780 nm. The experiment uses frequency as its
primary optical coordinate because detunings are naturally expressed in MHz or
GHz.

#diagram(
  ```mermaid
flowchart TD
  A["Rb-85 / Rb-87"] --> B["5²S₁/₂ ground manifold"]
  B --> C["5²P₁/₂"]
  B --> D["5²P₃/₂"]
  C --> E["D1 ≈ 795 nm"]
  D --> F["D2 ≈ 780 nm"]```.text,
  caption: [Fine structure: D1 and D2 connect the same ground manifold to the two 5²P manifolds.],
  height: 6cm,
)

= Angular momentum and hyperfine structure

The electronic angular momentum couples orbital and spin angular momenta by
vector addition, schematically written as

$ J = L + S $

and the nuclear spin couples to the electronic angular momentum as

$ F = I + J. $

For fixed I and J, the allowed values are

$ F = I + J, I + J - 1, ..., abs(I - J). $

Define

$ K = F(F+1) - I(I+1) - J(J+1). $

The standard first-order hyperfine energy shift, relative to the manifold's
centre of gravity, is

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

A is the magnetic-dipole constant and B the electric-quadrupole constant
(@tab-hfs-constants). For J = 1/2 the quadrupole interaction is absent, so the shift
reduces to $Delta E_"HFS" = A K slash 2$; the B-term is not evaluated for
J = 1/2. iyzee does not evaluate this Hamiltonian: the source table stores
transition frequencies already assembled from the reference data.

#figure(
  table(
    columns: (1.7fr, 1.5fr, 1.5fr, 2.4fr),
    align: (left, right, right, left),
    table.header([*Constant*], [#iso-name("85")], [#iso-name("87")], [*Role*]),
    [A, 5#super[2]S#sub[1/2]], [1.011 910 813 GHz], [3.417 341 305 GHz], [ground splitting = A (I + ½)],
    [A, 5#super[2]P#sub[1/2]], [120.527 MHz], [408.328 MHz], [D1 excited manifold],
    [A, 5#super[2]P#sub[3/2]], [25.0354 MHz], [84.7185 MHz], [D2 excited manifold],
    [B, 5#super[2]P#sub[3/2]], [25.898 MHz], [12.4965 MHz], [D2 quadrupole term],
  ),
  caption: [Hyperfine constants (Steck, refs. 1 and 2: #super[85]Rb from revision 2.3.4, #super[87]Rb from revision 1.6).],
) <tab-hfs-constants>

#callout(
  "Worked check: the formula reproduces the table",
  [
    For #super[87]Rb 5#super[2]P#sub[3/2], F′ = 3 (I = J = 3/2): K = 4.5, so
    A K / 2 = 190.617 MHz and the quadrupole term is
    B · 4.5 / 18 = 3.124 MHz. The sum, 193.741 MHz, is the +0.1937408 GHz
    offset hard-coded for `D2 - Rb87_F23` in the transition table. The ground
    state agrees too: A (I + ½) = 2 × 3.417 341 GHz = 6.834 683 GHz.
  ],
  tone: "result",
)

#callout(
  "Keep the angular-momentum layers separate",
  [
    Fine structure separates D1 from D2. Hyperfine structure splits a
    fine-structure manifold into F levels. Zeeman structure resolves M#sub[F]
    states in a magnetic field. The software reference contains the fine and
    hyperfine structure, and does not model Zeeman shifts.
  ],
)

#level-figure()

= Optical selection rules and strengths

For electric-dipole excitation,

$ Delta F = 0, plus.minus 1 $

with the F = 0 → F′ = 0 transition forbidden. Applied to the two isotopes this
leaves exactly twenty hyperfine lines — eight on D1 and twelve on D2:

#figure(
  table(
    columns: (1.35fr, 1.2fr, 2.65fr),
    align: (left, center, left),
    table.header([*Family*], [*Ground F*], [*Allowed excited F′*]),
    [D1, #iso-name("85")], [2], [2, 3],
    [D1, #iso-name("85")], [3], [2, 3],
    [D1, #iso-name("87")], [1], [1, 2],
    [D1, #iso-name("87")], [2], [1, 2],
    [D2, #iso-name("85")], [2], [1, 2, 3],
    [D2, #iso-name("85")], [3], [2, 3, 4],
    [D2, #iso-name("87")], [1], [0, 1, 2],
    [D2, #iso-name("87")], [2], [1, 2, 3],
  ),
  caption: [Transition families in the table. The excited levels are those allowed by J′ and the nuclear spin.],
)

Not every allowed line is equally strong. Steck's relative strength factor
S#sub[FF′] (refs. 1 and 2) is the share of the absorption from ground level F
that goes to F′; it sums to one over F′ for each F. The factor appears as the
connector thickness in the level diagram above and as the stick height in the
spectra below. The complete transition table, with frequencies, offsets and
strengths, is generated from the code:

#transition-table()

The parser behind the static TUI page is `rb_lines()` in
#code("rb_lines", label: "source").

= Reference frequencies and detuning

The implementation uses vacuum reference frequencies based on the rubidium
D-line data compiled by Daniel A. Steck. The source representation is THz.

#figure(
  table(
    columns: (1.6fr, 1.8fr, 1.8fr),
    align: (left, right, right),
    table.header([*Reference*], [*Frequency (THz)*], [*Wavelength (nm)*]),
    [#iso-name("85") D1 centre], [#fmt(rb-data.centers_thz.at("85").D1, 9)], [#fmt(wavelength-nm(rb-data.centers_thz.at("85").D1), 4)],
    [#iso-name("87") D1 centre], [#fmt(rb-data.centers_thz.at("87").D1, 9)], [#fmt(wavelength-nm(rb-data.centers_thz.at("87").D1), 4)],
    [#iso-name("85") D2 centre], [#fmt(rb-data.centers_thz.at("85").D2, 9)], [#fmt(wavelength-nm(rb-data.centers_thz.at("85").D2), 4)],
    [#iso-name("87") D2 centre], [#fmt(rb-data.centers_thz.at("87").D2, 9)], [#fmt(wavelength-nm(rb-data.centers_thz.at("87").D2), 4)],
  ),
  caption: [Fine-structure centres of gravity, each isotope with its own isotope shift included.],
)

Individual hyperfine transition frequencies are assembled from these centres
and the ground- and excited-state offsets embedded in the `Rb_transitions`
table (#file("src/iyzee/devices/wavemeter.py", label: "source")).

#diagram(
  ```mermaid
flowchart LR
  A["Fine-structure centre"] --> B["isotope shift"]
  B --> C["ground-state HFS"]
  C --> D["excited-state HFS"]
  D --> E["transition frequency"]
  E --> F["laser detuning"]```.text,
  caption: [A transition frequency is a difference of two level energies; a fine-structure centre is not itself one hyperfine line.],
  width: 100%,
  height: 3cm,
)

For laser frequency ν and reference frequency ν₀,

$ Delta = nu - nu_0. $

Positive detuning therefore means higher optical frequency; negative detuning
is red of the reference.

#callout(
  "Three sign conventions appear in the project",
  [
    #table(
      columns: (1.9fr, 2.2fr, 1.7fr),
      stroke: none,
      fill: none,
      inset: (x: 3pt, y: 3pt),
      [*Where*], [*Quantity*], [*Positive means*],
      [`monitoring_frequencies`, in #code("monitoring_frequencies", label: "source")], [ν#sub[laser] − ν#sub[transition], × 10³ for GHz], [laser above the line],
      [Rb page of the TUI, “From centre”], [ν#sub[transition] − ν#sub[centre of that isotope]], [line above its centre],
      [spectra in this part], [ν#sub[transition] − ν#sub[centre of #iso-name("85")]], [line above the #iso-name("85") centre; one axis for both isotopes],
    )
    Quote the convention whenever a detuning is written down.
  ],
  tone: "warning",
)

== Where the standard sweep sits

The experiment's default laser-frequency centre, #fact("sweeps.frequency_steps.center_thz") THz
(the default of #code("frequency_sweep_steps")), is #fmt((fact-raw("sweeps.frequency_steps.center_thz") - rb-data.centers_thz.at("85").D1) * 1e3, 3) GHz from the
#iso-name("85") D1 centre. Compared with the table it coincides — to the 40 kHz
rounding of the stored seven decimals — with the #iso-name("87") D1
F = 2 → F′ = 2 line. The default scan is #fact("sweeps.frequency_steps.count") points, offsets of #number-text(calc.round(fact-raw("sweeps.frequency_steps.offsets_thz.0") * 1e6, digits: 3)) MHz and #number-text(calc.round(fact-raw("sweeps.frequency_steps.offsets_thz.1") * 1e6, digits: 3))
from that centre, far inside one Doppler width: it samples a
feature, it does not map the spectrum. Scans that cover several features pass
their own offsets.

= Warm-vapor spectroscopy

The line positions above are an ideal reference. A warm cell produces
broadened and overlapping resonances.

For longitudinal atomic velocity $v_z$, taken positive along the laser
propagation direction,

$ Delta nu_"D" approx nu_0 v_z / c. $

A Maxwell-Boltzmann distribution gives a Gaussian Doppler envelope with FWHM

$ Delta nu_"D,FWHM" =
nu_0 sqrt(8 k_B T ln(2) / (m c^2))
. $

The width follows in three steps. At temperature $T$ the Maxwell–Boltzmann velocity component along the beam is Gaussian with variance
$sigma_v^2 = k_B T / m$. The first-order Doppler shift maps it linearly onto frequency, so the absorption envelope is Gaussian with
$sigma_nu = nu_0 sigma_v / c$. A Gaussian has full width at half maximum $2 sqrt(2 ln 2) sigma_nu$, which is the expression above.
The $sqrt(T slash m)$ scaling is why the table below grows by about 10 % between 295 K and 360 K and why #iso-name("87") is slightly wider than #iso-name("85").

#figure(
  table(
    columns: (1.3fr, 1fr, 1fr, 1fr, 1fr),
    align: (left, right, right, right, right),
    table.header([*Doppler FWHM (MHz)*], [#iso-name("85") D1], [#iso-name("85") D2], [#iso-name("87") D1], [#iso-name("87") D2]),
    ..for temp in (295, 330, 360) {
      (
        [T = #temp K (#calc.round(temp - 273.15) °C)],
        ..for (iso, line) in (("85", "D1"), ("85", "D2"), ("87", "D1"), ("87", "D2")) {
          ([#fmt(doppler-fwhm(iso, line, temp) * 1e3, 0)],)
        },
      )
    },
  ),
  caption: [The Doppler width is hundreds of MHz — about a hundred times the natural linewidth of 5.75 MHz (D1) and 6.07 MHz (D2).],
)

Pressure broadening, transit-time effects, power broadening, magnetic fields
and hyperfine overlap further modify the observed profile.

#scale-ladder()

In the simplest Beer–Lambert model,

$ alpha(nu) = n sigma(nu) $

and

$ I(L) = I_0 exp(-alpha L). $

Thus an observed line shape is not a lookup of one transition frequency: it
depends on atomic structure, velocity distribution, collisions, optical depth,
experimental geometry and instrumental response.

The following figures combine the pieces for a vapor in the optically thin
limit: each hyperfine line is weighted by the isotopic abundance, the thermal
population of its ground level, #sym.approx (2F + 1) / (2(2I + 1)), and S#sub[FF′],
then broadened by the Gaussian above. The marker on the D1 figure is the
application's default sweep centre.

#figure(
  rb-spectrum("D1", marker: (thz: fact-raw("sweeps.frequency_steps.center_thz"), label: [default sweep centre #fact("sweeps.frequency_steps.center_thz") THz])),
  caption: [D1 line: stick positions with relative strengths, and the Doppler-broadened absorption they produce at 330 K. The ground-state splitting resolves four groups; the excited-state structure inside each group is only partly resolved (#iso-name("87") F = 2 shows two humps).],
)

#figure(
  rb-spectrum("D2"),
  caption: [D2 line, same construction. The excited-state structure is smaller than the Doppler width, so each ground-level group is a single broadened dip.],
)

#callout(
  "Reference page versus spectroscopy model",
  [
    The Rb page answers “where are the reference transitions?”. It does not
    predict the spectrum of a heated cell; the figures above are an
    illustration, built from the strengths in Steck's tables, not output of the
    software. A quantitative model would need temperature, density, isotope
    composition (enriched cells change the weights), pressure broadening,
    optical depth and magnetic-field inputs.
  ],
)

= Saturation and optical pumping

A useful two-level intuition is the saturation parameter

$ s_0 = I / I_"sat" $

with detuning-dependent form

$ s(Delta) = s_0 / (1 + 4 Delta^2 / Gamma^2). $

The corresponding two-level scattering rate is

$ R_"sc" = Gamma/2 * s(Delta) / (1 + s(Delta)). $

For orientation, Steck's saturation intensities for linearly polarized,
far-detuned light are about 2.5 mW/cm² on D2 and 4.5 mW/cm² on D1; the
cycling transition driven by circular light saturates at 1.67 mW/cm² (ref. 1).
Intensities in a squeezing experiment commonly exceed these by orders of
magnitude, so the vapor is strongly saturated and the two-level picture is
only a starting point.

Real rubidium is multilevel. Hyperfine and Zeeman sublevels have
polarization-dependent transition strengths, so repeated scattering changes
the population distribution through optical pumping.

That matters directly for PSR: the probe is not a passive spectator. It
changes the atomic state, so the nonlinear susceptibility depends on intensity,
polarization, detuning and relaxation.

= Polarization self-rotation: classical picture

Take a strong, nearly linearly polarized field. In the circular basis,

$ E_x = (E_+ + E_-) / sqrt(2) $

up to the chosen phase convention.

If the atomic medium gives the two circular components different complex
susceptibilities, they acquire different phase and amplitude changes. In the
simplest birefringent picture,

$ phi = k L / 2 "Re"(n_+ - n_-). $

The polarization ellipse therefore rotates. The process is nonlinear because
the differential response itself depends on the optical field and the pumped
atomic state. Unlike the Faraday effect, it needs no external magnetic field.

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
  width: 100%,
  height: 4cm,
)

= PSR as a quantum interaction

For the squeezing picture, treat the strong polarization component as a
classical pump and the orthogonal polarization as a weak quantum field. An
ideal undepleted-pump description couples opposite-frequency sidebands:

$ a_"out"(Omega) =
mu(Omega) a_"in"(Omega) + nu(Omega) a_"in"^dagger(-Omega) $

with the lossless Bogoliubov constraint

$ |mu(Omega)|^2 - |nu(Omega)|^2 = 1. $

The quadrature

$ X_theta =
(a exp(-i theta) + a^dagger exp(i theta)) / sqrt(2) $

can then have a variance below the vacuum value while the conjugate
quadrature is anti-squeezed. Squeezing is quoted in decibels relative to the
vacuum (shot-noise) level,

$ S_"dB" = 10 log_10 (V / V_"vac"). $

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
  height: 6.2cm,
)

This is a physical model, not a claim that the code computes μ and ν. iyzee
measures the downstream noise consequence.

Squeezed vacuum from PSR in a vapor was proposed by Matsko _et al._ (ref. 3)
and first observed by Ries, Brezger and Lvovsky (ref. 4), who reported
0.85 dB of squeezing. What limits the result has been studied since: atomic
noise (ref. 5), spatial multimode structure (ref. 6) and cell geometry and
optical depth (ref. 7). Observed squeezing in these experiments is of order
1 dB, including sub-MHz analysis frequencies (ref. 8).

== From decibels to a squeezing parameter <rb-r>

For an ideal squeezer acting on one sideband pair, the Bogoliubov coefficients are $mu = cosh r$ and $nu = sinh r$ with $r >= 0$ the squeezing parameter
(the phase of $nu$ only decides which quadrature is the squeezed one, so it is taken real). They satisfy $|mu|^2 - |nu|^2 = 1$ for every $r$. With
$a_"out" = mu a + nu a^dagger$, the two quadratures transform as

$ X_"out" = (mu + nu) X_"in" = e^(r) X_"in", quad P_"out" = (mu - nu) P_"in" = e^(-r) P_"in". $

Vacuum has variance $1 slash 2$ in each, so the output variances are $V_(plus.minus) = 1/2 e^(plus.minus 2r)$ and, relative to vacuum,

$ S_"dB" = 10 log_10 e^(-2r) = -(20 / ln(10)) r approx -8.686 thin r. $

#let r-of(db) = db * calc.ln(10) / 20
#figure(
  table(
    columns: (1fr, 1fr, 1.4fr, 1.6fr),
    align: (right, right, right, right),
    table.header([*Squeezing*], [*r*], [*V / V#sub[vac]*], [*Anti-squeezed quadrature*]),
    ..for db in (1, 3, 6, 10) {
      ([#db dB], [#fmt(r-of(db), 3)], [#fmt(calc.pow(10.0, -db / 10), 3)], [+#db dB])
    },
  ),
  caption: [The pure-state relation: decibels, squeezing parameter, variance ratio. The last column is the anti-squeezing of a lossless state.],
)

Loss changes this picture asymmetrically. After efficiency $eta$ the variances become $1 - eta + eta e^(∓ 2r)$: the squeezed quadrature
loses most of its benefit while the anti-squeezed one barely changes, so a lossy state shows much more anti-squeezing than squeezing without any
excess noise at all. For $eta = 0.8$ and $r = 0.5$ the pure state is ±#fmt(20 * 0.5 / calc.ln(10), 2) dB but the detected values are
#fmt(10 * calc.log(1 - 0.8 + 0.8 * calc.exp(-1.0), base: 10), 2) dB and +#fmt(10 * calc.log(1 - 0.8 + 0.8 * calc.exp(1.0), base: 10), 2) dB.
A large anti-squeezing/squeezing asymmetry is therefore evidence about loss first; only the part that exceeds what the loss predicts points to
excess atomic or technical noise (ref. 5).

= The useful detuning window

Changing optical detuning moves several competing effects at once:

#figure(
  table(
    columns: (1.55fr, 2fr, 2.35fr),
    table.header([*Effect*], [*Closer to resonance*], [*Further detuned*]),
    [Absorption], [generally stronger], [generally weaker],
    [Nonlinear response], [can be stronger], [usually decreases],
    [Optical pumping], [stronger and more state dependent], [weaker],
    [Transmitted power], [can be reduced], [usually larger],
    [Technical contamination], [can couple strongly], [often reduced],
  ),
  caption: [Qualitative trends only; the real dependence is apparatus specific.],
)

There is no universal best detuning. The optimum depends on vapor temperature,
optical depth, pump intensity, cell length, magnetic field, polarization
purity, spatial mode, losses and detection efficiency.

A frequency scan is consequently valuable because it maps the actual response
of the complete apparatus rather than choosing a point from a vacuum line table.

= Detection chain: optical state to RF spectrum

The MXA observes an electrical signal, not the optical field directly.

#flow(
  "Rb vapor", "polarization analysis", "photodetector(s)", "RF / electrical chain", "MXA", "measured noise spectrum",
  caption: [The detector and RF chain are part of the measurement, not transparent observers of the atomic state. A reference state is also routed to the detector.],
)

For total detection efficiency η, optical loss mixes the field with vacuum.
A simple variance model is

$ V_"meas" = eta V_"field" + (1 - eta) V_"vac". $

With the quadrature convention above, the vacuum reference has
$V_"vac" = 1/2$. Optical loss, mode mismatch and detector inefficiency reduce
the observable squeezing through this vacuum mixing, and the penalty is steep:

#loss-figure()

Electronics noise is an additional additive contribution and must be
characterized separately.

*Undoing the loss.* Write $v = V_"field" slash V_"vac"$ for the squeezing before detection. Then the measured level is

$ S_"meas" = 10 log_10 (1 - eta + eta v), $

which can never go below the *loss floor* $10 log_10 (1 - eta)$, however large the generated squeezing. Inverting gives the squeezing the apparatus
produced, for a measured level and an efficiency you must supply:

$ v = (10^(S_"meas" slash 10) - (1 - eta)) / eta, quad S_"generated" = 10 log_10 v. $

#let generated(meas, eta) = {
  let v = (calc.pow(10.0, meas / 10) - (1 - eta)) / eta
  if v <= 0 { [below the loss floor] } else { [#fmt(10 * calc.log(v, base: 10), 2) dB] }
}
#figure(
  table(
    columns: (1.4fr, 1fr, 1fr, 1fr),
    align: (left, right, right, right),
    table.header([*Measured*], [*η = 60 %*], [*η = 80 %*], [*η = 90 %*]),
    ..for meas in (-1, -2, -3) {
      ([#fmt(meas, 0) dB], [#generated(meas, 0.6)], [#generated(meas, 0.8)], [#generated(meas, 0.9)])
    },
  ),
  caption: [Squeezing generated before detection, from a measured level and a total efficiency η. The correction grows quickly as η falls.],
)

Here $eta$ is the product of every loss between the vapor and the spectrum analyzer: transmission of the optics, mode matching or visibility with the
reference, and photodiode quantum efficiency. iyzee records no efficiency, so a generated-squeezing figure is always the experimenter's calculation
layered on the recorded level.

= Squeezing versus shot noise

The current frequency step follows a deliberate sequence: set the laser
frequency, wait, read the wavemeter, acquire the squeezing trace with the
shutter open, close the shutter, and acquire the shot-noise reference.

#flow(
  "set laser setpoint", "wait", "read wavemeter", "open shutter", "measure squeezing", "close shutter", "measure shot noise",
  caption: [Frequency-step ordering in the experiment code. The shutter closes in a `finally` block even if the squeezing acquisition fails.],
)

The implementation is `FrequencyStep` in
#code("FrequencyStep", label: "source").

#figure(
  table(
    columns: (1.2fr, 2fr, 2.6fr),
    table.header([*Trace*], [*Optical state*], [*Interpretation*]),
    [Squeezing], [PSR-generated field present], [target quadrature noise],
    [Shot noise], [reference state], [quantum-noise baseline],
    [Electronics background], [separate characterization], [instrument floor],
  ),
  caption: [The three traces behind a squeezing number.],
)

A lower noise trace than the shot-noise reference is meaningful only after
relevant detector gain, optical power, bandwidth, and reference conditions
have been matched. With powers in linear units and the electronics background
$P_"el"$ subtracted from both traces, the squeezing level is

$ S_"dB" = 10 log_10 ((P_"sqz" - P_"el") / (P_"shot" - P_"el")). $

#callout(
  "Logarithmic and linear operations are different",
  [
    A difference in dBm is a ratio:
    $ P_"dBm" - Q_"dBm" = 10 log_10(P / Q). $
    It is not a physical power subtraction. Convert dBm to linear power before
    subtracting backgrounds or references.
  ],
  tone: "warning",
)

#callout(
  "What the software actually computes",
  [
    iyzee records two traces per point, `squeezing` and `shot_noise`, both in dBm. It records *no electronics-background trace* and subtracts none. The number the Results page and the data examples report is
    $ overline(Delta)_"dB" = 1/N sum_(i=1)^N (P_(i,"sqz")^"dBm" - P_(i,"shot")^"dBm"), $
    the mean over trace samples of the per-sample dB difference (#code("difference_values_many"), #code("difference_statistic")). It equals the $S_"dB"$ above only when the electronics noise is negligible next to both traces and the traces are flat across the samples; otherwise it is an average of ratios rather than the ratio of averages. Treat it as the raw comparison, and subtract a separately measured electronics background in linear units yourself when that floor matters: the recorded traces make it possible, but the software does not do it for you.
  ],
  tone: "warning",
)

== How precisely can the software resolve a squeezing number? <rb-statistics>

The answer is a statistical floor, not a promise: it is what the averaging buys under idealized assumptions, and real scatter will be larger. Treat the
noise in the analyzer's filter as Gaussian. Its detected power over one correlation time is then exponentially distributed, and two facts about the
*logarithm* of such a variable set the scale (both checked by simulation in the test suite):

- the mean of $10 log_10 P$ lies #fmt(0.5772156649015329 * 10 / calc.ln(10), 2) dB below $10 log_10 overline(P)$ (Euler's constant: $10 gamma slash ln(10)$), so logarithmic averaging of noise reads low by that fixed amount;
- one sample has a standard deviation of #fmt(calc.pi / calc.sqrt(6) * 10 / calc.ln(10), 2) dB ($pi slash sqrt(6) dot 10 slash ln(10)$), and averaging $N$ independent samples divides it by $sqrt(N)$. Averaging *power* instead would give #fmt(10 / calc.ln(10), 2) dB$slash sqrt(N)$, about 28 % less.

How many independent samples does a measurement hold? A zero-span trace of duration $T$ through a resolution filter of bandwidth $B$ holds about $B T$ independent
values, and the analyzer's trace average multiplies that by the number of sweeps, so $N_"eff" approx n_"avg" B T$. The scatter of one trace's mean level is then
$sigma approx 5.57 slash sqrt(N_"eff")$ dB, and of the difference of two independent traces $sqrt(2) sigma$:

#let eff(c) = c.avg_count * c.res_bw_hz * c.sweep_duration_ms / 1000
#let sigma-db(n) = calc.pi / calc.sqrt(6) * 10 / calc.ln(10) / calc.sqrt(n)
#figure(
  table(
    columns: (1.7fr, 1fr, 1fr, 1fr, 1fr, 1fr),
    align: (left, right, right, right, right, right),
    table.header([*Preset*], [*n#sub[avg]*], [*B*], [*T*], [*N#sub[eff]*], [*σ of difference*]),
    ..for (name, key) in (("Frequency sweep", "frequency"), ("Bandwidth sweep (library default)", "bandwidth")) {
      let c = fact-raw("sweeps." + key)
      (
        [#name],
        [#c.avg_count],
        [#si(c.res_bw_hz, "Hz")],
        [#c.sweep_duration_ms ms],
        [#fmt(eff(c), 0)],
        [#fmt(calc.sqrt(2) * sigma-db(eff(c)), 3) dB],
      )
    },
  ),
  caption: [Idealized statistical scatter of the squeezing-minus-shot-noise difference at the standard presets, from the values the code uses. A longer sweep duration or more averages raises N#sub[eff] proportionally.],
)

Two things follow. First, the fixed #fmt(0.5772156649015329 * 10 / calc.ln(10), 2) dB bias is present in *both* traces and cancels in their difference, which is why the
difference, not an absolute level, is the quantity to report; the cancellation holds for noise-like signals and fails for a coherent tone or a strongly non-Gaussian
trace, so check the traces for peaks. Second, a statistical floor of a few hundredths of a dB is far below the roughly 1 dB squeezing this kind of experiment
reports, so repeat-to-repeat scatter you actually observe is dominated by something else (laser lock and drift, alignment, background, the time between the two
traces) and should be measured by repeating a point, not read off this table. Instrument-specific effects (the detector type, the filter shape, correlation between
neighbouring trace points) change the constants, which is another reason to treat the table as a floor.

#tested-by("test_log_averaging_constants_quoted_in_the_physics_part", "test_log_bias_cancels_in_the_difference_and_scatter_follows_the_formula", "test_the_reported_number_is_the_mean_of_per_sample_db_differences", "test_squeezing_parameter_and_loss_formulas_round_trip")

= Mapping the physics into iyzee

The current implementation exposes a compact set of experimental controls:

#figure(
  table(
    columns: (1.65fr, 1.95fr, 2.4fr),
    table.header([*Physical quantity*], [*Software representation*], [*Role*]),
    [Laser frequency], [`frequency_thz` and the wavemeter channel], [sets optical detuning],
    [Measured frequency], [`read_frequency()`], [checks the actual laser frequency],
    [Reference lines], [`Rb_transitions`, Rb page (`r`)], [locates D1/D2 hyperfine lines],
    [Optical state], [shutter and optical setup], [selects the squeezed/reference path],
    [RF analysis frequency], [`center_hz` (#fact-si("sweeps.frequency.center_hz", "Hz") in the frequency sweep, zero span)], [sets the analyzed noise frequency],
    [RBW / VBW], [`AnalyzerConfig`, `BandwidthStep`], [sets spectral resolution and estimator behavior],
    [Averaging], [`avg_count`, `avg_type`], [controls variance and acquisition cost],
    [Reference], [shot-noise trace], [defines the comparison baseline],
    [Squeezing number], [#code("difference_statistic"), per-sample dB difference], [the raw comparison; no background subtraction],
    [Detuning of a recorded point], [`measured_frequency_thz` minus a `Rb_transitions` entry], [where on the line structure the point sits],
  ),
  caption: [Physical quantities and where the code holds them.],
)

The sequence runner is `run_sequence` in
#code("run_sequence", label: "source").
The analyzer configuration is `AnalyzerConfig` in
#code("AnalyzerConfig", label: "source"),
and the standard frequency-sweep analyzer setup is `frequency_sweep_config` in
#code("frequency_sweep_config", label: "source").

== From a recording to a detuning <rb-detuning>

A frequency-sweep recording stores, for every point, the wavemeter reading taken after the laser settled
(`measured_frequency_thz`). The reference table #code("Rb_transitions") holds the D1 and D2 hyperfine lines of both isotopes
in THz, the same values the Rb page shows. The detuning of a point from a line is their difference, which is what #code("monitoring_frequencies") prints
live for the current laser frequency; this script does it for a saved run, using the nearest line:

#raw(read("../examples/detuning.py"), lang: "python", block: true)

The test suite runs this script against a recording written by the real save functions. Two cautions belong to the physics, not the
code: the detuning is only as good as the wavemeter's absolute accuracy and calibration, and "nearest line" is a convenience: in warm vapor several
Doppler-broadened hyperfine lines overlap, so the nearest tabulated line is not necessarily the one that matters for the effect you are studying.

#tested-by("test_detuning_example_reports_distance_to_the_nearest_rubidium_line")

= What the software does not infer

The present Rubidium path does not automatically infer:

- the electronics background, or a linear-power squeezing level (see the callout in the section on squeezing versus shot noise);
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

#figure(
  table(
    columns: (0.35fr, 4.9fr),
    align: (center, left),
    table.header([*Ref.*], [*Source*]),
    [1], [D. A. Steck, _Rubidium 85 D Line Data_, revision 2.3.4 (8 August 2025). #link("https://steck.us/alkalidata/rubidium85numbers.pdf")[PDF]],
    [2], [D. A. Steck, _Rubidium 87 D Line Data_, revision 1.6, the revision the #super[87]Rb values were checked against. #link("https://steck.us/alkalidata/rubidium87numbers.1.6.pdf")[PDF]; current revision at #link("https://steck.us/alkalidata")[steck.us/alkalidata]],
    [3], [A. B. Matsko, I. Novikova, G. R. Welch, D. Budker, D. F. Kimball and S. M. Rochester, _Vacuum squeezing in atomic media via self-rotation_, Phys. Rev. A 66, 043815 (2002). #link("https://doi.org/10.1103/PhysRevA.66.043815")[DOI]],
    [4], [J. Ries, B. Brezger and A. I. Lvovsky, _Experimental vacuum squeezing in rubidium vapor via self-rotation_, Phys. Rev. A 68, 025801 (2003). #link("https://doi.org/10.1103/PhysRevA.68.025801")[DOI]],
    [5], [M. T. L. Hsu et al., _Effect of atomic noise on optical squeezing via polarization self-rotation in a thermal vapor cell_, Phys. Rev. A 73, 023806 (2006). #link("https://doi.org/10.1103/PhysRevA.73.023806")[DOI]],
    [6], [M. Zhang et al., _Spatial multimode structure of atom-generated squeezed light_, Phys. Rev. A 93, 013853 (2016). #link("https://doi.org/10.1103/PhysRevA.93.013853")[DOI]],
    [7], [M. Zhang et al., _Multipass configuration for improved squeezed vacuum generation in hot Rb vapor_, Phys. Rev. A 96, 013835 (2017). #link("https://doi.org/10.1103/PhysRevA.96.013835")[DOI]],
    [8], [E. E. Mikhailov and I. Novikova, _Low-frequency vacuum squeezing via polarization self-rotation in Rb vapor_, Opt. Lett. 33 (2008). #link("https://arxiv.org/abs/0802.1558")[arXiv:0802.1558]],
    [9], [O. Katz et al., _Coherent polarization self-rotation_, Phys. Rev. A 113, 043715 (2026). #link("https://doi.org/10.1103/3m6y-sfx8")[DOI]],
  ),
  caption: [References for this part.],
)

The 2003 experiment established vacuum squeezing through polarization
self-rotation in rubidium vapor. Later work showed that atomic noise, spatial
multimode structure and geometry can strongly influence what is actually
observed. Recent coherent-PSR work (ref. 9) is useful when distinguishing
conventional polarization rotation from explicitly coherent spin-light
interactions.

= Scientific maintenance rule

Reference frequencies are reference data, not calibration truth. A vacuum
frequency locates an atomic feature; a warm-cell observation is a broadened,
shifted and power-dependent physical response.

Whenever the transition convention, reference data, detuning definition,
measurement ordering, squeezing interpretation or physical assumptions in the
code change, update this part with the implementation. When `Rb_transitions`
changes, regenerate the figures' data with
`uv run python scripts/export_rb_data.py`.
