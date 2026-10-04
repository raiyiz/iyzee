// Shared Typst packages and visual language for the iyzee documentation.
#import "@preview/physica:0.9.8": *
#import "@preview/merman:0.3.0": mermaid, mermaid-figure

#let navy = rgb("#0B3155")
#let navy-dark = rgb("#07243E")
#let blue-ink = rgb("#173A5E")
#let blue-soft = rgb("#EAF2F8")
#let blue-pale = rgb("#F5F8FB")
#let hairline = rgb("#B9C9D8")
#let muted = rgb("#5B6B78")
#let white = rgb("#FFFFFF")
#let warm = rgb("#F7F1E8")
#let green-soft = rgb("#EDF5EF")

#show link: set text(fill: navy)
#show heading.where(level: 1): set text(fill: navy, weight: "bold", size: 15pt)
#show heading.where(level: 2): set text(fill: blue-ink, weight: "bold", size: 12pt)
#show heading.where(level: 3): set text(fill: blue-ink, weight: "bold", size: 10.5pt)

#let hero(kicker, title, subtitle) = block(
  fill: navy-dark,
  inset: (x: 16pt, y: 15pt),
)[
  #text(fill: white, size: 8pt, weight: "bold")[#smallcaps(kicker)]
  #v(0.35em)
  #text(fill: white, size: 23pt, weight: "bold")[#title]
  #v(0.35em)
  #text(fill: rgb("#DCE8F2"), size: 10.5pt)[#subtitle]
]

#let callout(title, body, tone: "note") = {
  let fill = if tone == "warning" { warm } else if tone == "result" { green-soft } else { blue-soft }
  block(
    fill: fill,
    stroke: (left: 3pt + navy),
    inset: (x: 10pt, y: 8pt),
  )[
    #text(fill: navy, weight: "bold")[#title]
    #v(0.25em)
    #body
  ]
}

#let src-link(path, line: none) = {
  let anchor = if line == none { "" } else { "#L" + str(line) }
  link("https://github.com/raiyiz/iyzee/blob/main/" + path + anchor)[source]
}

#let diagram(source, caption: none, width: 94%) = mermaid-figure(
  source,
  document-context: true,
  width: width,
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