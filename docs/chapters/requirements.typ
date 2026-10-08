// Shared Typst packages, visual language and helpers for the iyzee guide.
//
// Every chapter starts with `#import "requirements.typ": *` and opens with
// `#part(...)`. Page layout, numbering and the table of contents live in
// `main.typ`, so a chapter only contributes content.
#import "@preview/physica:0.9.8": *
#import "@preview/merman:0.3.0": mermaid, mermaid-figure

// -- palette ---------------------------------------------------------------
#let navy = rgb("#0B3155")
#let navy-dark = rgb("#07243E")
#let blue-ink = rgb("#173A5E")
#let blue-soft = rgb("#EAF2F8")
#let blue-pale = rgb("#F5F8FB")
#let hairline = rgb("#B9C9D8")
#let muted = rgb("#5B6B78")
#let white = rgb("#FFFFFF")
#let warm = rgb("#F7F1E8")
#let warm-ink = rgb("#7A4B12")
#let green-soft = rgb("#EDF5EF")
#let green-edge = rgb("#2F6B45")
// Isotope colours, shared by every Rubidium figure (and the TUI page).
#let rb85 = rgb("#1B7F9E")
#let rb87 = rgb("#C4601A")

// -- code links ------------------------------------------------------------
// The guide never hard-codes a line number, a default value or a file layout.
// `scripts/docs_index.py` reads the code and writes `data/code-index.json`
// (run by `scripts/ci.sh docs` before compiling); everything below asks it.
// A name that no longer exists, or became ambiguous, stops the build with the
// reference that broke instead of silently linking to the wrong line.
#let index = json("../data/code-index.json")

// Where "source" links point. CI passes the commit being built, so every link
// in a PDF lands on the exact lines of the code that PDF describes:
//   typst compile --input ref=<commit> docs/main.typ ...
#let repo-url = sys.inputs.at("repo", default: "https://github.com/raiyiz/iyzee")
#let repo-ref = sys.inputs.at("ref", default: "main")

#let blob(path, start: none, end: none) = {
  let anchor = if start == none { "" } else if end == none or end == start {
    "#L" + str(start)
  } else { "#L" + str(start) + "-L" + str(end) }
  repo-url + "/blob/" + repo-ref + "/" + path + anchor
}

// `name` is a dotted suffix of a qualified symbol: `Lab.connect`,
// `scope_workflows.apply_channel_settings`, or just `LockedProxy` when unique.
#let resolve(name) = {
  let hits = index.lookup.at(name, default: ())
  if hits.len() > 1 {
    let source = hits.filter(h => not h.starts-with("tests."))
    if source.len() == 1 { hits = source }
  }
  if hits.len() == 0 { panic("unknown code symbol `" + name + "`: it was renamed or removed") }
  if hits.len() > 1 {
    panic("ambiguous code symbol `" + name + "`: " + hits.join(", ") + " (qualify it)")
  }
  (hits.first(), index.symbols.at(hits.first()))
}

#let sym-url(name) = {
  let (_, s) = resolve(name)
  blob(s.path, start: s.start, end: s.end)
}

// A link to the exact lines that define a symbol, rendered as code. (Named `code`, not
// `sym`, which would shadow Typst's built-in symbol module used by the physics part.)
#let code(name, label: none) = {
  let (_, s) = resolve(name)
  let shown = if label != none { label } else if s.kind in ("function", "method", "test") {
    name + "()"
  } else { name }
  link(blob(s.path, start: s.start, end: s.end))[#raw(shown)]
}

// A link to a whole file (existence is checked by `scripts/check_doc_links.py`).
#let file(path, label: none) = link(blob(path))[#raw(if label != none { label } else { path })]

// "Where this lives": the symbols a section is about, with the first line of each
// docstring pulled from the code, so the guide shows what the code claims.
#let anchors(..names, title: "Where this lives in the code") = {
  let rows = ()
  for name in names.pos() {
    let (qualified, s) = resolve(name)
    rows.push(code(name))
    rows.push(text(size: 8pt, fill: muted)[#s.kind])
    rows.push(if s.summary == "" { text(fill: muted)[—] } else { [#s.summary.] })
    rows.push(text(size: 8pt, fill: muted)[#raw(s.path.split("/").last()), L#s.start–#s.end])
  }
  block(width: 100%, breakable: false, above: 1em, below: 1em)[
    #text(size: 8pt, weight: "bold", fill: navy, tracking: 0.06em)[#upper(title)]
    #v(0.2em)
    #table(
      columns: (auto, auto, 1fr, auto),
      stroke: (x, y) => (bottom: 0.4pt + hairline),
      inset: (x: 5pt, y: 3.5pt),
      fill: none,
      align: (left + top, left + top, left + top, left + top),
      ..rows,
    )
  ]
}

// "Pinned by tests": the tests that fail if the behavior described stops being true.
#let tested-by(..names) = {
  let items = names.pos().map(name => {
    let (_, s) = resolve(name)
    let words = s.path.split("/").last().replace(".py", "")
    [#link(blob(s.path, start: s.start, end: s.end))[#raw(name)]]
  })
  block(width: 100%, above: 0.8em, below: 1em, inset: (left: 9pt, y: 2pt), stroke: (left: 1.5pt + green-edge))[
    #text(size: 8pt, weight: "bold", fill: green-edge, tracking: 0.06em)[PINNED BY TESTS]
    #v(0.15em)
    #text(size: 8.5pt)[#items.join([ · ])]
  ]
}

// -- facts read from the imported code -------------------------------------
#let fact-raw(path) = {
  let node = index.facts
  for key in path.split(".") {
    if type(node) == dictionary and key in node { node = node.at(key) } else if (
      type(node) == array and key.match(regex("^\\d+$")) != none
    ) { node = node.at(int(key)) } else {
      panic("unknown fact `" + path + "` (no `" + key + "`): the code changed shape")
    }
  }
  node
}

#let number-text(x) = {
  if type(x) == int { str(x) } else if x == calc.round(x) and calc.abs(x) < 1e15 {
    str(int(x))
  } else { str(x) }
}

// `fact("sweeps.bandwidth.avg_count")` -> 200, as it is in the code today.
#let fact(path) = {
  let v = fact-raw(path)
  if type(v) in (int, float) { number-text(v) } else { str(v) }
}

// Value with an SI prefix: `fact-si("sweeps.bandwidth.res_bw_hz", "Hz")` -> 24 kHz.
#let si(value, unit) = {
  let v = float(value)
  let a = calc.abs(v)
  let (scale, prefix) = if a == 0 { (1, "") } else if a >= 1e9 { (1e9, "G") } else if a >= 1e6 {
    (1e6, "M")
  } else if a >= 1e3 { (1e3, "k") } else if a >= 1 { (1, "") } else if a >= 1e-3 { (1e-3, "m") } else if (
    a >= 1e-6
  ) { (1e-6, "µ") } else { (1e-9, "n") }
  let scaled = v / scale
  let rounded = calc.round(scaled, digits: 4)
  [#number-text(rounded)\u{a0}#prefix#unit]
}
#let fact-si(path, unit) = si(fact-raw(path), unit)
// Milliseconds stored as an integer in the code, shown in seconds.
#let fact-ms(path) = si(fact-raw(path) / 1000, "s")

// -- structure -------------------------------------------------------------
// A "part" is one chapter file: a banner on a fresh page plus a level-1
// heading (numbered "Part I", ...). Sections inside it are level 2 and
// restart at 1, because `main.typ` sets `heading(offset: 1)`.
#let part(kicker, title, subtitle, id: none) = {
  pagebreak(weak: true)
  block(width: 100%, fill: navy-dark, inset: (x: 16pt, y: 15pt), breakable: false)[
    #text(fill: rgb("#9DB8D0"), size: 8pt, weight: "bold", tracking: 0.08em)[#upper(kicker)]
    #v(0.3em)
    #[#heading(level: 1, outlined: true)[#title]#if id != none { label(id) }]
    #v(0.2em)
    #text(fill: rgb("#DCE8F2"), size: 10.5pt)[#subtitle]
  ]
  v(0.9em)
}

#let callout(title, body, tone: "note") = {
  let fill = if tone == "warning" { warm } else if tone == "result" { green-soft } else { blue-soft }
  let bar = if tone == "warning" { warm-ink } else { navy }
  block(
    width: 100%,
    fill: fill,
    stroke: (left: 3pt + bar),
    inset: (x: 10pt, y: 8pt),
    breakable: false,
  )[
    #text(fill: bar, weight: "bold")[#title]
    #v(0.25em)
    #body
  ]
}

// A short, quotable invariant (replaces Markdown-style "> ..." lines).
#let pull(body) = block(
  width: 100%,
  inset: (left: 11pt, y: 3pt),
  stroke: (left: 2pt + hairline),
)[#text(style: "italic", fill: blue-ink)[#body]]

// -- figures ---------------------------------------------------------------
// Non-linear graphs stay Mermaid. `height` bounds tall graphs, which would
// otherwise be scaled up to the full page width.
#let diagram(source, caption: none, width: 82%, height: 7.5cm) = mermaid-figure(
  source,
  document-context: true,
  width: width,
  height: height,
  typography: (size: 10pt),
  theme-name: "base",
  theme: (
    background: "#FFFFFF",
    primaryColor: "#E2EDF5",
    primaryTextColor: "#0B3155",
    primaryBorderColor: "#0B3155",
    lineColor: "#0B3155",
    secondaryColor: "#F1F6FA",
    secondaryTextColor: "#173A5E",
    secondaryBorderColor: "#7A9BB5",
    tertiaryColor: "#F7F9FB",
    tertiaryTextColor: "#173A5E",
    tertiaryBorderColor: "#9EB3C4",
  ),
  caption: caption,
)

// Native left-to-right chain. A Mermaid chain of six or seven nodes is scaled
// down until its text is unreadable; this wraps the labels instead.
#let flow(..steps, caption: none) = {
  let items = steps.pos()
  let cells = ()
  for (i, step) in items.enumerate() {
    if i > 0 { cells.push(align(center + horizon, text(fill: navy, weight: "bold", size: 11pt)[→])) }
    cells.push(block(
      width: 100%,
      fill: blue-soft,
      stroke: 0.8pt + navy,
      radius: 4pt,
      inset: (x: 4pt, y: 7pt),
      align(center, text(size: 8.5pt, fill: navy)[#step]),
    ))
  }
  let columns = ()
  for i in range(items.len()) {
    if i > 0 { columns.push(13pt) }
    columns.push(1fr)
  }
  figure(grid(columns: columns, column-gutter: 2pt, align: horizon, ..cells), caption: caption)
}
