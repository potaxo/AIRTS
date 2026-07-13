# ADR 0001: Preserve a modular monolith with a stable Simulation facade

**Status:** Accepted

## Context

AIRTS needs deterministic domain behavior, multiple control adapters, persistence, replay, and
high-unit-count performance without the operational complexity of distributed services. The
original simulation module accumulated command validation, automation execution, movement,
combat, economy, construction, and production in one class implementation.

## Decision

AIRTS remains one installable Python package. `airts.simulation.Simulation` stays the public,
authoritative facade and state owner. Cohesive deterministic behavior lives in internal modules
under `airts.systems`; those modules may depend on domain types but not on Pygame, renderers, or
language-model providers. Runtime imports point from the facade to systems. Type-only references
back to `Simulation` are allowed for strict static checking.

Refactoring follows behavior-preserving extractions. Public imports, command schemas, persistence,
replay, tick order, event order, and seeded determinism remain stable unless an explicit milestone
changes them.

Canonical implementation ownership is expressed by five shallow packages: `airts.world`,
`airts.navigation`, `airts.systems`, `airts.adapters`, and `airts.presentation`. Existing top-level
imports for moved modules remain compatibility re-exports, while internal source uses canonical
paths. Executable architecture tests enforce the dependency direction and compatibility identity.

## Consequences

Subsystems can be understood and tested independently while deployment remains simple. The facade
continues to expose some private delegation methods so existing internal collaborators and tests do
not require a flag-day rewrite. Line count is a review signal, not an architectural boundary;
modules are split only when responsibilities and reasons to change differ.
Tests mirror behavior intent rather than the source tree exactly: unit, integration, movement,
performance, and architecture suites can be selected independently without changing the default
full-suite command.
