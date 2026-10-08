// Generated from the code and the guide by scripts/docs_index.py. Nothing here is typed by hand.
#import "requirements.typ": *

#part(
  "Appendix, generated",
  "Code map",
  "Every module, the layer it belongs to, which guide sections cite it, and which way its imports point.",
  id: "part-code-map",
)

This appendix is computed, not written. It answers two questions a reader of either side asks:
*I am reading this code: where is it explained?* and *I am reading this section: which code is it about?*
The second is answered inline throughout the guide by #raw("#code") links and "Where this lives" tables; the first is below.
A module's docstring may also point back with a `:guide:` role, which the build checks.

#let layer-names = (
  config: "Configuration",
  devices: "Device drivers",
  experiment: "Experiment and analysis",
  session: "Session and workflows",
  tui: "Terminal UI",
  entry: "Package root",
)

// page of a labelled section, as text
#let page-of(lbl) = context {
  let found = query(label(lbl))
  if found.len() > 0 [p. #found.first().location().page()] else []
}

#let section-link(entry) = {
  if entry.label != "" {
    [#link(label(entry.label))[#entry.heading] #text(fill: muted, size: 8pt)[#page-of(entry.label)]]
  } else { [#entry.heading #text(fill: muted, size: 8pt)[(#entry.chapter)]] }
}

#let public-kinds = ("class", "function", "method")
#let is-public(name, s) = {
  s.kind in public-kinds and not name.split(".").any(part => part.starts-with("_"))
}

= Modules by layer <map-modules>

The columns count *public* classes, functions and methods, and how many of them the guide cites directly (`#code`).
Low numbers are not errors; they show where the guide is thin.

#let modules-in(layer) = index.modules.pairs().filter(((name, m)) => m.layer == layer).sorted(key: ((name, m)) => name)

#for layer in ("config", "devices", "experiment", "session", "tui", "entry") {
  let mods = modules-in(layer)
  if mods.len() == 0 { continue }
  [== #layer-names.at(layer)]
  let rows = ()
  for (name, m) in mods {
    let own = index.symbols.pairs().filter(((q, s)) => s.path == m.path and is-public(q, s))
    let cited = own.filter(((q, s)) => q in index.xref.refs)
    let sections = ()
    for (q, s) in own {
      for entry in index.xref.refs.at(q, default: ()) {
        if not sections.any(e => e.heading == entry.heading and e.chapter == entry.chapter) { sections.push(entry) }
      }
    }
    for label in m.guide {
      let found = index.xref.labels.at(label, default: none)
      if found != none and not sections.any(e => e.label == label) {
        sections.push((chapter: "", heading: found.split(": ").slice(1).join(": "), label: label))
      }
    }
    rows.push(link(blob(m.path))[#raw(name.replace("iyzee.", ""))])
    rows.push(text(size: 8pt)[#m.lines])
    rows.push(text(size: 8pt)[#cited.len()/#own.len()])
    rows.push(text(size: 8.5pt)[
      #if m.summary != "" [#m.summary.] \
      #if sections.len() > 0 { text(size: 8pt)[→ #sections.slice(0, calc.min(sections.len(), 4)).map(section-link).join([; ])] }
    ])
  }
  table(
    columns: (auto, auto, auto, 1fr),
    stroke: (x, y) => (bottom: 0.4pt + hairline),
    inset: (x: 5pt, y: 3.5pt),
    fill: none,
    align: (left + top, right + top, right + top, left + top),
    table.header([*Module*], [*Lines*], [*Cited*], [*What it is, and where the guide covers it*]),
    ..rows,
  )
}

= Import graph <map-imports>

Edges are internal imports, with every module under `tui/` collapsed into one node and the edges into `config` (which nearly everything uses) left out for legibility. Arrows point from the importing module to the one it uses, and
they point *down* the layers; `tests/test_layering.py` fails if any import points up, and if anything outside the TUI imports Textual.

#let node-id(name) = name.replace("iyzee.", "").replace(".", "_")
#let in-tui(name) = name.starts-with("iyzee.tui")
#let group(name) = if in-tui(name) { "tui" } else { node-id(name) }

#let edges = {
  let seen = ()
  for (name, deps) in index.imports.pairs() {
    for dep in deps {
      let from = group(name)
      let to = group(dep)
      let skip = from == to or name == "iyzee" or dep == "iyzee" or to == "config"
      if not skip and (from, to) not in seen { seen.push((from, to)) }
    }
  }
  seen.sorted()
}

#diagram(
  "flowchart LR\n" + edges.map(((a, b)) => "  " + a + " --> " + b).join("\n"),
  caption: [Internal import graph, generated from the code. Arrows point down: the TUI uses everything, the drivers use only `config`.],
  width: 100%,
  height: 12cm,
)

= Guide sections and the code they cite <map-sections>

#let by-chapter = {
  let groups = (:)
  for (q, entries) in index.xref.refs.pairs() {
    for entry in entries {
      let key = entry.chapter + " › " + entry.heading
      let current = groups.at(key, default: (entry: entry, symbols: ()))
      if q not in current.symbols { current.symbols.push(q) }
      groups.insert(key, current)
    }
  }
  groups
}

#table(
  columns: (1.4fr, 2fr),
  stroke: (x, y) => (bottom: 0.4pt + hairline),
  inset: (x: 5pt, y: 3.5pt),
  fill: none,
  table.header([*Guide section*], [*Code it cites*]),
  ..by-chapter.keys().sorted().map(key => {
    let g = by-chapter.at(key)
    (
      text(size: 8.5pt)[#section-link(g.entry)],
      text(size: 8pt)[#g.symbols.sorted().map(q => link(sym-url(q))[#raw(q.replace("iyzee.", ""))]).join([, ])],
    )
  }).flatten(),
)
