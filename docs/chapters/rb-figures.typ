// Native figures for the Rubidium part.
//
// Every number that comes from the application is read from
// docs/data/rubidium.json, which scripts/export_rb_data.py generates from
// `iyzee.devices.wavemeter.Rb_transitions`; tests/test_docs_data.py fails when
// that file is stale. The figures therefore cannot drift from the code.
// Coordinates inside a canvas are centimetres, y pointing down.
#import "requirements.typ": *

#let rb-data = json("../data/rubidium.json")

// -- constants (CODATA 2018; masses from Steck's D-line data) ----------------
#let c0 = 299792458.0
#let kB = 1.380649e-23
#let amu = 1.66053906660e-27
#let mass-u = ("85": 84.911789732, "87": 86.909180520)
#let abundance = ("85": 0.7217, "87": 0.2783)
#let two-i = ("85": 5, "87": 3) // 2I: nuclear spin 5/2 and 3/2

#let iso-color(iso) = if str(iso) == "85" { rb85 } else { rb87 }
#let iso-name(iso) = [#super[#iso]Rb]
#let minus(s) = s.replace("-", "−")

// Fixed-decimal formatting (`str(calc.round(..))` drops trailing zeros).
#let fmt(x, d, plus: false) = {
  let s = str(calc.round(x, digits: d)).replace("−", "-")
  let negative = s.starts-with("-")
  if negative { s = s.slice(1) }
  let parts = s.split(".")
  let frac = if parts.len() > 1 { parts.at(1) } else { "" }
  while frac.len() < d { frac += "0" }
  let body = if d > 0 { parts.at(0) + "." + frac } else { parts.at(0) }
  if negative { "−" + body } else if plus { "+" + body } else { body }
}

// Doppler FWHM in GHz: nu0 * sqrt(8 kB T ln2 / (m c^2)).
#let doppler-fwhm(iso, line, temp) = {
  let nu0 = rb-data.centers_thz.at(iso).at(line) * 1e12
  let m = mass-u.at(iso) * amu
  nu0 * calc.sqrt(8 * kB * temp * calc.ln(2) / (m * c0 * c0)) / 1e9
}

// Vacuum wavelength in nm from a frequency in THz.
#let wavelength-nm(thz) = c0 / (thz * 1e12) * 1e9

// -- drawing primitives ------------------------------------------------------
#let canvas(w, h, body) = box(width: w * 1cm, height: h * 1cm, body)

#let seg(x0, y0, x1, y1, stroke) = place(
  top + left,
  line(start: (x0 * 1cm, y0 * 1cm), end: (x1 * 1cm, y1 * 1cm), stroke: stroke),
)

// A text label whose anchor point is (x, y); `anchor` is "c", "l" or "r".
#let lbl(x, y, body, w: 2.4, anchor: "c", size: 7.5pt, fill: blue-ink, weight: "regular") = {
  let dx = if anchor == "c" { x - w / 2 } else if anchor == "r" { x - w } else { x }
  let a = if anchor == "c" { center } else if anchor == "r" { right } else { left }
  place(
    top + left,
    dx: dx * 1cm,
    dy: (y - 0.15) * 1cm,
    box(width: w * 1cm, height: 0.3cm, align(horizon + a, text(size: size, fill: fill, weight: weight, body))),
  )
}

// -- Figure: hyperfine level structure ---------------------------------------
// Ground manifold at the bottom, the D1 (left) and D2 (right) excited manifolds
// above. Excited splittings are to scale within an isotope, ground splittings on
// their own scale; the axis is broken between them. Connector thickness is the
// relative hyperfine strength S_FF'.
#let level-panel(iso) = {
  let lv = rb-data.levels_ghz.at(iso)
  let ts = rb-data.transitions.filter(t => str(t.isotope) == iso)
  let col = iso-color(iso)
  let se = if iso == "85" { 8.5 } else { 3.8 } // cm per GHz, excited manifolds
  let g = lv.ground
  let g-vals = g.values()
  let sg = 1.6 / (calc.max(..g-vals) - calc.min(..g-vals)) // cm per GHz, ground
  let y-up(y) = 8.0 - y // internal y is "up"; the canvas is "down"
  let gy(f) = y-up(1.2 + g.at(str(f)) * sg)
  let ey(line, f) = y-up((if line == "D1" { 4.95 } else { 5.95 }) + lv.at(line).at(str(f)) * se)
  let (gx0, gx1) = (3.6, 5.0)
  let (d1x0, d1x1) = (1.05, 2.4)
  let (d2x0, d2x1) = (6.2, 7.55)
  let ink = navy

  canvas(8.6, 8.0, {
    // connectors (under the level bars)
    for t in ts {
      let width = 0.45pt + 2.6pt * t.strength
      let (xa, xb) = if t.line == "D1" { (gx0, d1x1) } else { (gx1, d2x0) }
      seg(xa, gy(t.f_ground), xb, ey(t.line, t.f_excited), width + col.transparentize(25%))
    }
    // ground manifold
    for (f, _) in g {
      seg(gx0, gy(int(f)), gx1, gy(int(f)), 2pt + ink)
      lbl((gx0 + gx1) / 2, gy(int(f)) - 0.27, [F = #f], w: 1.6, size: 7.5pt)
    }
    // excited manifolds
    for (line, x0, x1) in (("D1", d1x0, d1x1), ("D2", d2x0, d2x1)) {
      for (f, _) in lv.at(line) {
        seg(x0, ey(line, int(f)), x1, ey(line, int(f)), 2pt + ink)
        if line == "D1" {
          lbl(x0 - 0.1, ey(line, int(f)), [F′ = #f], w: 0.95, anchor: "r")
        } else {
          lbl(x1 + 0.1, ey(line, int(f)), [F′ = #f], w: 0.95, anchor: "l")
        }
      }
    }
    // headers
    lbl((d1x0 + d1x1) / 2, 0.15, [5#super[2]P#sub[1/2]  ·  D1], w: 3.0, weight: "bold", fill: navy)
    lbl((d2x0 + d2x1) / 2, 0.15, [5#super[2]P#sub[3/2]  ·  D2], w: 3.0, weight: "bold", fill: navy)
    lbl(
      (gx0 + gx1) / 2,
      7.78,
      [5#super[2]S#sub[1/2]  ·  #fmt(calc.max(..g-vals) - calc.min(..g-vals), 4) GHz],
      w: 4.4,
      weight: "bold",
      fill: navy,
    )
  })
}

#let level-figure() = figure(
  grid(
    columns: (1fr, 1fr),
    column-gutter: 0.4cm,
    {
      align(center, text(weight: "bold", fill: rb85, size: 10pt)[#iso-name("85") · I = 5/2])
      level-panel("85")
    },
    {
      align(center, text(weight: "bold", fill: rb87, size: 10pt)[#iso-name("87") · I = 3/2])
      level-panel("87")
    },
  ),
  caption: [Hyperfine structure of the two D lines, drawn from the offsets implied by the transition table. The ground and excited manifolds use separate vertical scales (axis break); within an isotope the excited-state splittings (D1 left, D2 right) share one. Each connector is an allowed F → F′ line; its thickness is Steck's relative strength S#sub[FF′].],
)

// -- Figure: line positions and the Doppler-broadened absorption ---------------
#let rb-spectrum(line, temp: 330, width: 16.2, marker: none) = {
  let ts = rb-data.transitions.filter(t => t.line == line)
  let ref = rb-data.centers_thz.at("85").at(line)
  let pos(t) = (t.thz - ref) * 1e3 // GHz from the Rb85 centre of this line
  let xs = ts.map(pos)
  let lo = calc.floor(calc.min(..xs) - 1.2)
  let hi = calc.ceil(calc.max(..xs) + 1.2)
  let lx = 0.4
  let pw = width - lx - 0.3
  let X(gh) = lx + (gh - lo) / (hi - lo) * pw
  let (h1, gap, h2) = (2.4, 0.55, 3.3)
  let base1 = h1
  let base2 = h1 + gap + h2
  let total = base2 + 1.1

  // Doppler-broadened, optically thin absorption. Weight of a line:
  // abundance x ground-level population x relative strength S_FF'.
  let sigma(iso) = doppler-fwhm(iso, line, temp) / (2 * calc.sqrt(2 * calc.ln(2)))
  let weight(t) = {
    let iso = str(t.isotope)
    abundance.at(iso) * (2 * t.f_ground + 1) / (2 * (two-i.at(iso) + 1)) * t.strength
  }
  let n = 520
  let grid-x = range(n + 1).map(i => lo + (hi - lo) * i / n)
  let curve(iso) = grid-x.map(x => ts
    .filter(t => str(t.isotope) == iso)
    .map(t => weight(t) * calc.exp(-calc.pow(x - pos(t), 2) / (2 * calc.pow(sigma(iso), 2))))
    .sum())
  let (c85, c87) = (curve("85"), curve("87"))
  let peak = calc.max(..range(n + 1).map(i => c85.at(i) + c87.at(i)))

  canvas(width, total, {
    // panel frames
    seg(lx, base1, lx + pw, base1, 0.7pt + hairline)
    seg(lx, base2, lx + pw, base2, 0.9pt + navy)
    // marker (e.g. the application's default sweep centre)
    if marker != none {
      let mx = X((marker.thz - ref) * 1e3)
      seg(mx, 0.1, mx, base2, (paint: warm-ink, thickness: 0.8pt, dash: "dashed"))
      lbl(mx + 0.12, 0.28, marker.label, w: 4.6, anchor: "l", size: 7pt, fill: warm-ink, weight: "bold")
    }
    // sticks: position and relative strength
    for t in ts {
      let x = X(pos(t))
      let h = 0.15 + 1.75 * t.strength
      seg(x, base1, x, base1 - h, 1.3pt + iso-color(t.isotope))
    }
    // cluster labels: one per (isotope, ground F)
    for iso in ("85", "87") {
      for f in ts.filter(t => str(t.isotope) == iso).map(t => t.f_ground).dedup() {
        let group = ts.filter(t => str(t.isotope) == iso and t.f_ground == f)
        let cx = group.map(pos).sum() / group.len()
        let stick-top = group.map(t => base1 - (0.15 + 1.75 * t.strength)).sorted().first()
        lbl(X(cx), calc.max(stick-top - 0.18, 0.55), [#iso-name(iso) F = #f], w: 2.0, size: 6.8pt, fill: iso-color(iso), weight: "bold")
      }
    }
    // absorption: one filled profile per isotope, summed outline on top
    for (iso, c) in (("85", c85), ("87", c87)) {
      let top-pts = range(n + 1).map(i => (X(grid-x.at(i)) * 1cm, (base2 - c.at(i) / peak * (h2 - 0.2)) * 1cm))
      place(top + left, polygon(
        fill: iso-color(iso).transparentize(62%),
        stroke: 0.8pt + iso-color(iso),
        (X(lo) * 1cm, base2 * 1cm),
        ..top-pts,
        (X(hi) * 1cm, base2 * 1cm),
      ))
    }
    lbl(lx + 0.1, h1 + gap + 0.18, [Doppler-broadened absorption · optically thin · T = #temp K · natural abundance], w: 12, anchor: "l", size: 7pt, fill: muted)
    lbl(lx + 0.1, base1 + 0.2, [#sym.arrow.t line strength S#sub[FF′]], w: 4, anchor: "l", size: 6.8pt, fill: muted)
    // axis
    for tick in range(lo, hi + 1) {
      seg(X(tick), base2, X(tick), base2 + 0.12, 0.7pt + navy)
      lbl(X(tick), base2 + 0.38, minus(str(tick)), w: 0.9, size: 7pt)
    }
    lbl(lx + pw / 2, base2 + 0.82, [detuning of the transition from the #iso-name("85") #line centre (GHz)], w: 9, size: 7.5pt, fill: muted)
  })
}

// -- Figure: the frequency scales on one logarithmic axis -------------------
#let scale-ladder() = {
  let lv = rb-data.levels_ghz
  // adjacent excited-state hyperfine splittings (Hz) for both isotopes and lines
  let splits = ()
  for iso in ("85", "87") {
    for line in ("D1", "D2") {
      let fs = lv.at(iso).at(line).keys().map(int).sorted()
      for i in range(fs.len() - 1) {
        splits.push(calc.abs(lv.at(iso).at(line).at(str(fs.at(i + 1))) - lv.at(iso).at(line).at(str(fs.at(i)))) * 1e9)
      }
    }
  }
  let ground = ("85", "87").map(iso => {
    let v = lv.at(iso).ground.values()
    (calc.max(..v) - calc.min(..v)) * 1e9
  })
  let dopp = ()
  for iso in ("85", "87") {
    for line in ("D1", "D2") {
      for temp in (295, 345) { dopp.push(doppler-fwhm(iso, line, temp) * 1e9) }
    }
  }
  let fine = (rb-data.centers_thz.at("85").D2 - rb-data.centers_thz.at("85").D1) * 1e12
  // (label, lowest Hz, highest Hz, colour)
  let rows = (
    ([natural linewidth Γ/2π], 5.75e6, 6.07e6, muted),
    ([isotope shift #super[87]Rb − #super[85]Rb], 77.58e6, 78.10e6, muted),
    ([excited-state hyperfine splittings], calc.min(..splits), calc.max(..splits), blue-ink),
    ([Doppler width, FWHM (295–345 K)], calc.min(..dopp), calc.max(..dopp), warm-ink),
    ([ground-state hyperfine splitting], calc.min(..ground), calc.max(..ground), navy),
    ([fine-structure splitting D2 − D1], fine, fine, navy),
  )
  let (lo-d, hi-d) = (6, 13.2) // log10 Hz
  let (x0, x1) = (5.6, 15.6)
  let X(hz) = x0 + (calc.log(hz, base: 10) - lo-d) / (hi-d - lo-d) * (x1 - x0)
  let row-h = 0.85
  let ytop = 0.35
  let H = ytop + rows.len() * row-h + 1.2
  let freq-text(hz) = if hz >= 1e12 { [#fmt(hz / 1e12, 2) THz] } else if hz >= 1e9 { [#fmt(hz / 1e9, 2) GHz] } else if hz >= 1e8 { [#fmt(hz / 1e6, 0) MHz] } else { [#fmt(hz / 1e6, 1) MHz] }

  figure(
    canvas(16.4, H, {
      // decade grid
      for (p, name) in ((6, "1 MHz"), (7, "10 MHz"), (8, "100 MHz"), (9, "1 GHz"), (10, "10 GHz"), (11, "100 GHz"), (12, "1 THz"), (13, "10 THz")) {
        let x = X(calc.pow(10.0, p))
        seg(x, ytop - 0.1, x, ytop + rows.len() * row-h, 0.4pt + hairline)
        lbl(x, ytop + rows.len() * row-h + 0.3, name, w: 1.6, size: 6.8pt, fill: muted)
      }
      seg(x0, ytop + rows.len() * row-h, x1, ytop + rows.len() * row-h, 0.8pt + navy)
      for (i, (name, a, b, c)) in rows.enumerate() {
        let y = ytop + (i + 0.5) * row-h
        lbl(x0 - 0.2, y, name, w: 5.3, anchor: "r", size: 8pt)
        let (xa, xb) = (X(a), X(b))
        let ranged = b > a * 1.001
        if ranged and xb - xa > 0.14 {
          place(top + left, dx: xa * 1cm, dy: (y - 0.17) * 1cm, rect(width: (xb - xa) * 1cm, height: 0.34cm, fill: c.transparentize(15%), radius: 2pt))
        } else {
          place(top + left, dx: (xa - 0.07) * 1cm, dy: (y - 0.22) * 1cm, rect(width: 0.14cm, height: 0.44cm, fill: c, radius: 1pt))
        }
        let note = if ranged { [#freq-text(a) – #freq-text(b)] } else { freq-text(a) }
        let near-right = xb > x1 - 3.2
        if near-right { lbl(xa - 0.12, y, note, w: 4.2, anchor: "r", size: 7.2pt, fill: c) } else { lbl(xb + 0.15, y, note, w: 4.2, anchor: "l", size: 7.2pt, fill: c) }
      }
    }),
    caption: [The frequency scales of the rubidium D lines on one logarithmic axis. Excited-state hyperfine structure sits inside the Doppler width of a warm cell, so it is mostly unresolved; the ground-state splitting is several Doppler widths and resolves each isotope into two groups.],
  )
}

// -- Figure: detection loss ------------------------------------------------------
#let loss-figure() = {
  let (x0, x1, y0, y1) = (1.6, 11.4, 0.4, 6.2) // plot rectangle (cm)
  let (eta-lo, db-max) = (0.5, 10.5)
  let X(eta) = x0 + (eta - eta-lo) / (1 - eta-lo) * (x1 - x0)
  let Y(db) = y0 + (-db) / db-max * (y1 - y0) // db is negative; 0 dB at the top
  let measured(s, eta) = 10 * calc.log(1 - eta + eta * calc.pow(10.0, -s / 10), base: 10)
  let series = ((3, rb85), (6, blue-ink), (10, rb87))
  let n = 50
  figure(
    canvas(13.4, 7.3, {
      for db in range(0, 11, step: 2) {
        seg(x0, Y(-db), x1, Y(-db), 0.4pt + hairline)
        lbl(x0 - 0.12, Y(-db), minus(str(-db)), w: 1.0, anchor: "r", size: 7pt)
      }
      for k in range(0, 6) {
        let eta = 0.5 + 0.1 * k
        seg(X(eta), y1, X(eta), y1 + 0.1, 0.7pt + navy)
        lbl(X(eta), y1 + 0.38, [#calc.round(eta * 100)%], w: 1.2, size: 7pt)
      }
      seg(x0, y0, x0, y1, 0.8pt + navy)
      seg(x0, y1, x1, y1, 0.8pt + navy)
      seg(X(0.8), y0, X(0.8), y1, (paint: warm-ink, thickness: 0.7pt, dash: "dashed"))
      lbl(X(0.8) + 0.1, y0 + 0.2, [η = 80 %], w: 1.6, anchor: "l", size: 7pt, fill: warm-ink, weight: "bold")
      for (s, c) in series {
        for i in range(n) {
          let (ea, eb) = (0.5 + 0.5 * i / n, 0.5 + 0.5 * (i + 1) / n)
          seg(X(ea), Y(measured(s, ea)), X(eb), Y(measured(s, eb)), 1.5pt + c)
        }
        lbl(x1 + 0.12, Y(measured(s, 1.0)), [intrinsic #minus(str(-s)) dB], w: 2.2, anchor: "l", size: 7.2pt, fill: c, weight: "bold")
      }
      lbl(x0 + (x1 - x0) / 2, y1 + 0.85, [total detection efficiency η], w: 6, size: 7.5pt, fill: muted)
      lbl(0.55, y0 - 0.15, [observed noise (dB re shot noise)], w: 5, anchor: "l", size: 7.5pt, fill: muted)
    }),
    caption: [Optical loss mixes vacuum into the squeezed field: V#sub[meas] / V#sub[vac] = 1 − η + η V#sub[field] / V#sub[vac]. A 10 dB source seen with 80 % total efficiency shows only −5.5 dB.],
  )
}

// -- Table: every transition, generated from the same data -----------------------
#let transition-table() = {
  let groups = ()
  for line in ("D1", "D2") {
    for iso in ("85", "87") {
      groups.push((line, iso, rb-data.transitions.filter(t => t.line == line and str(t.isotope) == iso)))
    }
  }
  let cells = ()
  for (gi, (line, iso, rows)) in groups.enumerate() {
    for (i, t) in rows.enumerate() {
      if i == 0 {
        cells.push(table.cell(rowspan: rows.len(), align: center + horizon, fill: if calc.odd(gi) { blue-pale } else { white })[*#line*])
        cells.push(table.cell(rowspan: rows.len(), align: center + horizon, fill: if calc.odd(gi) { blue-pale } else { white })[#text(fill: iso-color(iso), weight: "bold")[#iso-name(iso)]])
      }
      let f = if calc.odd(gi) { blue-pale } else { white }
      cells.push(table.cell(fill: f)[F = #t.f_ground → F′ = #t.f_excited])
      cells.push(table.cell(fill: f, align: right)[#fmt(t.thz, 9)])
      cells.push(table.cell(fill: f, align: right)[#fmt(t.detuning_ghz, 4, plus: true)])
      cells.push(table.cell(fill: f, align: right)[#fmt(t.strength, 3)])
    }
  }
  figure(
    table(
      columns: (auto, auto, 1fr, 1.3fr, 1.3fr, 0.8fr),
      stroke: 0.5pt + hairline,
      fill: none,
      inset: (x: 7pt, y: 4pt),
      table.header(
        table.cell(fill: blue-soft)[*Line*],
        table.cell(fill: blue-soft)[*Isotope*],
        table.cell(fill: blue-soft)[*Transition*],
        table.cell(fill: blue-soft, align: right)[*Frequency (THz)*],
        table.cell(fill: blue-soft, align: right)[*From centre (GHz)*],
        table.cell(fill: blue-soft, align: right)[*S#sub[FF′]*],
      ),
      ..cells,
    ),
    caption: [The twenty hyperfine transitions of `Rb_transitions`, with the strength factor each carries in the figures. “From centre” is the transition frequency minus that isotope's own fine-structure centre — the number the Rb page of the TUI shows.],
    kind: table,
  )
}
