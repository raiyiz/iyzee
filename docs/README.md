# iyzee documentation

The repository has two documentation layers:

- **[README](../README.md)** — quick start, orientation, everyday commands, and configuration.
- **Technical guide** — architecture, device interaction, measurements, and rubidium physics.

The guide is compiled from [`main.typ`](main.typ) and its chapters. The source code and tests remain authoritative; the guide explains the concepts and boundaries that make the code easier to work with.

## Where to start

| You want to... | Start with |
| --- | --- |
| Install and run iyzee | [Repository README](../README.md) |
| Understand the software structure | [Architecture](chapters/architecture.typ) |
| Understand the TUI and instrument boundary | [TUI and devices](chapters/tui-and-devices.typ) |
| Understand MXA measurements | [MXA and measurements](chapters/mxa-and-measurements.typ) |
| Understand the Rubidium experiment | [Rubidium physics](chapters/rubidium-physics.typ) |
| Work on the documentation itself | [Infinity Docs Plan](infinity-docs-plan.md) |

## Technical guide

The compiled guide follows this reading order:

### I. Architecture

**[Architecture](chapters/architecture.typ)**

The canonical developer map: ownership, layering, connection state, locking, shared machinery, and the boundary between TUI, experiments, devices, and hardware.

**Read this first when changing the software structure.**

### II. TUI and devices

**[TUI and device interaction](chapters/tui-and-devices.typ)**

How the interactive application, live session, device drivers, and embedded console fit together. This is the operator-facing and device-integration reference.

**Read this when working on the TUI or adding instrument behavior.**

### III. MXA and measurements

**[MXA and measurements](chapters/mxa-and-measurements.typ)**

How analyzer state becomes a measurement: SCPI control, RBW/VBW, averaging, noise quantities, sweeps, and persisted results.

**Read this when changing measurement procedures or analyzer behavior.**

### IV. Rubidium

**[Rubidium physics](chapters/rubidium-physics.typ)**

Atomic structure, D-line spectroscopy, warm-vapor effects, polarization self-rotation, quantum-noise transfer, and the connection to the actual measurement.

**Read this for the scientific background behind the experiment.**

## Repository structure

```text
docs/
├── main.typ                         # compiled guide entry point
├── chapters/                        # reader-facing technical chapters
│   ├── architecture.typ
│   ├── tui-and-devices.typ
│   ├── mxa-and-measurements.typ
│   ├── rubidium-physics.typ
│   ├── requirements.typ             # shared Typst helpers/style
│   └── rb-figures.typ               # Rubidium figure/data helpers
├── data/                            # generated/supporting scientific data
└── infinity-docs-plan.md            # living documentation roadmap
```

Helper files support the guide but are not themselves reader-facing chapters.
The public structure is intentionally small: start with `docs/README.md`, then
follow the compiled guide or the specific chapter that matches the task.

## Architectural decisions

Long-lived architectural decisions belong under [`adr/`](adr/). ADRs are
decision records, not reader-facing chapters, and are not included in the
compiled guide's contents.

## Build the guide

From the repository root:

```sh
scripts/ci.sh docs
```

The resulting PDF is built as:

```text
build/docs/iyzee-guide.pdf
```

The same documentation build is exercised by CI.

## Documentation principles

The guide deliberately does **not** try to document every function or module. It should explain:

- stable architectural concepts,
- real measurement and hardware workflows,
- important design decisions,
- the scientific meaning of the data.

Prefer one clear explanation over duplicated explanations. Use diagrams for relationships and state flows, and link to source code when a concrete implementation anchor is useful.

The [Infinity Docs Plan](infinity-docs-plan.md) is the living checklist for extending and restructuring this guide.
