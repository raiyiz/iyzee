// iyzee technical guide: one book, four parts.
//
//   typst compile docs/main.typ build/docs/main.pdf
//
// Page layout, heading numbering and the single table of contents live here.
// Chapters (docs/chapters/) contribute content only; see requirements.typ.
#import "chapters/requirements.typ": *

#set document(
  title: "iyzee technical guide",
  author: "iyzee",
  keywords: ("iyzee", "architecture", "MXA", "rubidium", "squeezing"),
)

#set page(
  margin: (x: 2.2cm, top: 2.3cm, bottom: 2cm),
  header: context {
    let here-page = here().page()
    // No running header on a part's opening page (the banner says it already).
    let opening = query(heading.where(level: 1)).any(h => h.location().page() == here-page)
    let parts = query(heading.where(level: 1).before(here()))
    if here-page > 1 and not opening and parts.len() > 0 {
      set text(size: 8pt, fill: muted)
      smallcaps[iyzee]
      h(1fr)
      parts.last().body
      v(-0.4em)
      line(length: 100%, stroke: 0.4pt + hairline)
    }
  },
  footer: context {
    set text(size: 8pt, fill: muted)
    h(1fr)
    counter(page).display("1 / 1", both: true)
  },
)

#set text(size: 10pt)
#set par(justify: true, leading: 0.58em)
#set heading(
  offset: 1,
  numbering: (..n) => {
    let n = n.pos()
    if n.len() == 1 { [Part #numbering("I", n.at(0))] } else { numbering("1.1", ..n.slice(1)) }
  },
)

#show link: set text(fill: navy)
#show heading.where(level: 1): set text(fill: white, size: 24pt, weight: "bold")
#show heading.where(level: 1): set block(above: 0pt, below: 0pt)
#show heading.where(level: 2): set text(fill: navy, weight: "bold", size: 14pt)
#show heading.where(level: 2): it => block(above: 1.7em, below: 0.9em, sticky: true)[
  #it
  #v(-0.5em)
  #line(length: 100%, stroke: 0.6pt + hairline)
]
#show heading.where(level: 3): set text(fill: blue-ink, weight: "bold", size: 11pt)
#show heading.where(level: 3): set block(above: 1.3em, below: 0.6em, sticky: true)
#show heading.where(level: 4): set text(fill: blue-ink, weight: "bold", size: 10pt)

#set table(
  stroke: 0.5pt + hairline,
  inset: 6pt,
  fill: (_, y) => if y == 0 { blue-soft } else if calc.odd(y) { blue-pale } else { white },
)
#show figure.caption: set text(size: 9pt, fill: muted)
#show figure: set block(above: 1.3em, below: 1.3em)
#show raw.where(block: false): set text(fill: blue-ink)

// -- cover and contents ----------------------------------------------------
#block(width: 100%, fill: navy-dark, inset: (x: 22pt, y: 26pt))[
  #text(fill: rgb("#9DB8D0"), size: 9pt, weight: "bold", tracking: 0.1em)[LABORATORY CONTROL AND MEASUREMENT]
  #v(0.5em)
  #text(fill: white, size: 34pt, weight: "bold")[iyzee]
  #v(0.2em)
  #text(fill: white, size: 15pt)[Technical guide]
  #v(0.9em)
  #text(fill: rgb("#DCE8F2"), size: 10.5pt)[
    How the software is built, how the analyzer measures noise, how the
    terminal UI and instruments fit together, and the rubidium physics the
    experiment rests on.
  ]
]

#v(1em)

#callout(
  "How to read this guide",
  [
    The four parts can be read in order or on their own. *Architecture* states
    the ownership, locking and layering rules everything else follows.
    *MXA and measurements* connects analyzer state to the quantities iyzee
    records. *TUI and devices* is the operator and driver reference.
    *Rubidium* is the physics layer: atomic structure, spectroscopy and
    squeezing. The source code is authoritative; where prose and code
    disagree, the implementation and its tests win.
  ],
)

#v(0.8em)

#show outline.entry.where(level: 1): it => {
  v(0.7em, weak: true)
  strong(text(fill: navy, it))
}
#outline(title: [Contents], depth: 2, indent: 1.4em)

// -- parts -----------------------------------------------------------------
#include "chapters/architecture.typ"
#include "chapters/mxa-and-measurements.typ"
#include "chapters/tui-and-devices.typ"
#include "chapters/rubidium-physics.typ"

#v(2em)
#align(center)[
  #line(length: 30%, stroke: 0.5pt + hairline)
  #v(0.4em)
  #text(style: "italic", fill: muted)[Always strive for improvement, always be humble.]
]
