# Infinity Docs Plan

> **Purpose:** turn `docs/` from a collection of good chapters into a coherent technical manual that is easy to navigate, maintain, and extend.
>
> **Rule of thumb:** the **README is the map**, the **docs are the manual**, and the **code/docstrings are authoritative**.

## Status

The work is intentionally staged. Each phase has a clear endpoint so documentation can be improved in focused commits without losing the thread.

- [x] Phase 0 — README as the entry point
- [x] Phase 1 — Documentation landing page and structure
- [ ] Phase 2 — Architecture as the canonical developer map
- [ ] Phase 3 — Measurement lifecycle
- [ ] Phase 4 — TUI/device boundary and adding devices
- [ ] Phase 5 — VICP / scope architecture
- [ ] Phase 6 — Rubidium physics chapter polish
- [ ] Phase 7 — ADR cleanup and final documentation pass

---

## Phase 0 — README as the entry point

**Goal:** make the repository understandable within a minute without turning the README into a second manual.

Completed in the README refresh:

- [x] Put installation and the primary run command first.
- [x] Give the TUI a compact visual overview.
- [x] Explain the high-level workflow and architecture boundary.
- [x] Keep the practical instrument, keyboard, console, configuration, data, and development references.
- [x] Move deep architecture and scientific material into the technical guide.
- [x] Add a concise documentation index.
- [x] Keep the README short enough to scan.

**Done when:** a new user can install, launch, orient themselves, and find the right deeper document without reading the source tree.

---

## Phase 1 — Documentation landing page and structure

**Goal:** make `docs/` feel like one manual rather than a set of independent files.

### Structure

- [ ] Make `docs/main.typ` the clear entry point for the technical guide.
- [ ] Give the guide a deliberate top-level structure that mirrors how people use it:
  1. **Understand iyzee** — architecture and software boundaries.
  2. **Use iyzee** — measurement workflow, TUI, and devices.
  3. **Understand the experiment** — MXA measurements and rubidium physics.
  4. **Project decisions** — concise ADRs.
- [ ] Introduce a small documentation landing/index page with one-sentence descriptions and obvious reading paths.
- [ ] Decide and document the canonical location for ADRs (prefer `docs/adr/` over mixing them with chapter files).
- [ ] Keep helper/figure material out of the reader-facing navigation unless it is genuinely useful as a standalone document.
- [ ] Ensure the generated PDF has one coherent contents hierarchy rather than exposing implementation/helper files.

### Presentation

- [ ] Keep the existing visual identity: dark blue/navy, restrained callouts, strong headings, readable tables.
- [ ] Use diagrams where they explain relationships or flows better than prose.
- [ ] Avoid decorative material that does not carry information.
- [ ] Make each chapter start with a short statement of **what it answers** and **who should read it**.

### Navigation

- [ ] Add cross-links between related chapters where the reader naturally needs to move next.
- [ ] Make source-code links point to stable, meaningful files/functions where this materially helps understanding.
- [ ] Add a final "Where to go next" or equivalent navigation cue to each major part.

**Done when:** someone opening the documentation cold can tell what the manual contains, where to start, and where to go for architecture, operation, or science.

**Phase 1 result:** `docs/README.md` is the web-facing documentation map; `docs/main.typ` is the compiled-guide entry point; the PDF contains only the four reader-facing chapters in deliberate order; helpers remain implementation details; source anchors point to `main`; chapter navigation is explicit; and `docs/adr/` is the canonical home for future ADRs.

**Deliverable:** completed.

---

## Phase 2 — Architecture as the canonical developer map

**Goal:** make the architecture chapter the single authoritative explanation of how the software fits together.

### Content

- [ ] Replace implementation inventory with a small number of stable concepts.
- [ ] Establish the central boundary:
  `TUI / script / IPython → Lab session → experiment procedures → device drivers → hardware`.
- [ ] Explain ownership of instrument state, connection lifecycle, serialization/locking, and result data.
- [ ] Clearly distinguish:
  - device machinery,
  - experiment procedures,
  - application/session state,
  - TUI composition.
- [ ] Explain where new functionality belongs and where it does **not** belong.
- [ ] Identify the important interfaces instead of cataloguing every class.

### Diagrams

- [ ] Add one canonical layer diagram.
- [ ] Add a focused ownership/data-flow diagram where useful.
- [ ] Add a lifecycle/state diagram for connection or session state if it clarifies the prose.

### Code anchors

- [ ] Link the architectural claims to the most important source files/functions.
- [ ] Keep links sparse and purposeful; do not turn the chapter into generated API documentation.

**Done when:** a contributor can answer "where should this code live?" from this chapter alone.

---

## Phase 3 — Measurement lifecycle

**Goal:** document one real measurement from configuration to stored result.

### Workflow

- [ ] Describe the common acquisition path:
  `connect → configure → acquire → validate/normalize → analyze → store → inspect/export`.
- [ ] Choose a representative measurement and trace it end-to-end.
- [ ] Show where instrument state enters the procedure.
- [ ] Explain what is recorded alongside measured data and why.
- [ ] Explain how a saved result can later be inspected without reconnecting hardware.

### Practical example

- [ ] Include a compact Python example for an MXA or scope acquisition.
- [ ] Show the transition from low-level device operations to an experiment-level result.
- [ ] Clearly separate example code from authoritative implementation details.

**Done when:** a reader can follow one measurement through the software without having to reverse-engineer the code.

---

## Phase 4 — TUI/device boundary and adding devices

**Goal:** make it obvious how interactive presentation relates to reusable hardware machinery.

### Boundary

- [ ] Document the rule: **devices know hardware; experiments know measurements; the TUI composes and presents them.**
- [ ] Show a small device-side example and a small TUI-side example.
- [ ] Explain asynchronous workers only where they matter to that boundary and responsiveness.
- [ ] Document how the live session exposes connected instruments to the console/TUI.

### Adding a device

- [ ] Write a short contributor recipe:
  1. identify the transport/protocol,
  2. implement the reusable device operations,
  3. integrate the device into the lab/session,
  4. define connection-state behavior,
  5. add TUI integration only where useful,
  6. add focused tests,
  7. document the user-facing behavior.
- [ ] Point to one existing device as the reference implementation.
- [ ] State which parts are optional versus required.

**Done when:** adding a new instrument has an obvious path that does not begin by copying a TUI screen.

---

## Phase 5 — VICP / scope architecture

**Goal:** document the scope stack well enough that transport or synchronization failures can be diagnosed without archaeology.

### Stack

- [ ] Show the relationship:
  `scope workflow → LeCroy abstraction → VICP transport → TCP socket → hardware`.
- [ ] Explain the transport framing at the level required to understand the implementation.
- [ ] Document connection establishment and teardown.
- [ ] Document timeouts and partial/incomplete responses.
- [ ] Explain what constitutes a lost connection and what state must be invalidated.
- [ ] Explain waveform transfer and decoding at a conceptual level.
- [ ] Distinguish transport failures from scope-command failures and from invalid waveform data.

### Diagnostics

- [ ] Add a small failure-state diagram.
- [ ] Give concrete pointers for debugging a timeout, dropped socket, or malformed waveform response.
- [ ] Keep protocol detail limited to what helps users and contributors maintain the implementation.

**Done when:** the VICP/scope chapter can be used as a debugging reference during a real hardware failure.

---

## Phase 6 — Rubidium physics chapter polish

**Goal:** make the scientific chapter rigorous, readable, and directly connected to the experiment.

### Scientific structure

- [ ] Atomic structure and relevant Rb D1/D2 transitions.
- [ ] Hyperfine structure and the transitions used experimentally.
- [ ] Doppler broadening and other relevant line-broadening mechanisms.
- [ ] Saturation, optical pumping, and the role of polarization.
- [ ] Polarization self-rotation:
  - [ ] physical picture,
  - [ ] circular polarization components,
  - [ ] detuning and resonance slope,
  - [ ] nonlinear atom-light interaction.
- [ ] Experimental geometry and signal path.
- [ ] Expected spectra and how they should be interpreted.
- [ ] Quantum-noise / squeezing interpretation and measurement observables.

### Software connection

- [ ] End with a concrete section mapping the physics to the iyzee experiment:
  - what is controlled,
  - what is measured,
  - what the MXA sees,
  - what the scope/wavemeter contribute,
  - what gets stored.
- [ ] Keep scientific notation, assumptions, and definitions consistent throughout.
- [ ] Use diagrams for level structure and experimental signal flow where they carry real explanatory value.

**Done when:** the chapter reads as a scientific explanation of the actual experiment, not as a detached physics appendix.

---

## Phase 7 — ADR cleanup and final documentation pass

**Goal:** preserve important design knowledge without duplicating the architecture chapter.

### ADRs

- [ ] Move ADRs into the canonical ADR location.
- [ ] Keep each ADR compact:
  - **Context**
  - **Decision**
  - **Consequences**
  - **Alternatives considered**
- [ ] Keep ADRs for decisions that are likely to be revisited or questioned.
- [ ] Remove duplicated exposition that belongs in the architecture chapter.
- [ ] Add ADRs only when the decision carries long-lived architectural knowledge.

### Final pass

- [ ] Check every internal documentation link.
- [ ] Check chapter/outline hierarchy in the compiled guide.
- [ ] Check diagrams for readability and consistency.
- [ ] Check that terminology is consistent across README, guide, and code.
- [ ] Remove stale implementation details.
- [ ] Remove prose that a diagram, table, or source link communicates more clearly.
- [ ] Compile the guide locally and through CI.
- [ ] Verify that the documentation artifact is the intended final PDF.

**Done when:** the docs are cohesive, navigable, technically useful, and maintainable without growing into a second codebase.

---

## Working rules for the whole project

These rules apply to every phase:

- **Prefer stable concepts over file-by-file inventories.**
- **Prefer diagrams over paragraphs when relationships are the point.**
- **Prefer one canonical explanation over duplicated explanations.**
- **Link to code when a concrete implementation anchor helps; do not reproduce source.**
- **Keep the README lightweight.**
- **Preserve useful technical knowledge; de-crust by removing duplication and accidental complexity, not by deleting substance.**
- **Treat the compiled guide as a manual, not as generated API documentation.**
- **Keep each PR/commit logically focused so the documentation can be reviewed and the checklist can be advanced safely.**

## Checkpoint convention

Update this file as the work progresses:

1. Tick completed items.
2. Add a short note under the active phase when an important decision changes the planned structure.
3. Keep unfinished work in the phase where it belongs rather than creating a second informal TODO list.
4. At the end of each phase, record the resulting structure/content in the PR description or commit message.

This file is the living roadmap for the documentation refactor.
