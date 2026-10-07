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
// Isotope colours, shared by every Rubidium figure (and the TUI page).
#let rb85 = rgb("#1B7F9E")
#let rb87 = rgb("#C4601A")

// -- source links ----------------------------------------------------------
// One place decides which ref the "source" links point at. `scripts/
// check_doc_links.py` validates the anchors against the working tree.
#let repo-url = "https://github.com/raiyiz/iyzee/blob/"
// The branch the guide describes; every linked path exists there. Switch to "main"
// once the current layout is merged.
#let repo-ref = "flirr"

#let src-link(path, line: none) = {
  let anchor = if line == none { "" } else { "#L" + str(line) }
  link(repo-url + repo-ref + "/" + path + anchor)[source]
}

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
