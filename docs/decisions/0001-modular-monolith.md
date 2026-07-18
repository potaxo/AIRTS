# ADR 0001: Preserve a modular monolith with a stable Simulation facade

**Status:** Accepted

## Context

AIRTS needs deterministic domain behavior, direct and future language control adapters,
persistence, replay, and observable automation without the deployment and synchronization cost of
distributed services. The original simulation implementation also accumulated unrelated system
responsibilities in one file.

## Decision

AIRTS remains one installable Python package. `airts.simulation.Simulation` is the public facade,
authoritative state owner, command boundary, and fixed-tick orchestrator.

Cohesive deterministic behavior lives under `airts.systems`. World state, navigation mechanisms,
adapters, and presentation have shallow canonical packages described by
[ADR 0005](0005-canonical-module-ownership.md). Domain systems do not depend on Pygame or a model
provider, and the simulation runs headlessly.

Refactoring proceeds behind the stable facade. Command behavior, persistence and replay schemas,
tick and event order, and seeded determinism change only through an explicit documented contract.

## Consequences

Subsystems can be understood and tested independently while installation and runtime remain simple.
The facade may expose narrow delegation methods to internal systems, but it does not duplicate their
implementations. Module size is not an architectural boundary: code is split by responsibility,
invariant, and reason to change.
