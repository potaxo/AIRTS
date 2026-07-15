# AIRTS Engineering Architecture

This document owns repository structure, dependency direction, validation strategy, and durable engineering-change policy.

[Design index](../design.md) · [Roadmap](../roadmap.md)

---

# 30. Development Philosophy

AIRTS should use a combination of deliberate design and fast iteration.

The project should avoid both extremes:

```text
Too little design:
“Build an AI RTS game.”
→ inconsistent architecture
→ fragile prototype

Too much design:
specify every class and method before coding
→ unnecessary complexity
→ slow progress
```

The preferred loop is:

```text
define architectural invariants
→ implement one bounded vertical slice
→ test real behavior
→ identify problems
→ revise the design
→ implement the next milestone
```

The design documentation set is a living source of truth.

It should be updated when implementation reveals that an assumption is incorrect.

---

# 31. Engineering Change Policy

`AGENTS.md` owns contributor workflow and validation instructions. This document owns durable
technical decisions and must change whenever implementation work changes architecture, behavior,
scope, dependencies, performance strategy, or a user-visible feature.

Every architecture modification, feature upgrade, optimization, or improvement must record the
following in the owning design document or as a new bounded milestone:

* the behavior or constraint that changed;
* the component that remains authoritative;
* effects on dependency direction, determinism, validation, persistence, or replay;
* explicit exclusions and remaining limitations;
* the test or other acceptance evidence that defines the contract.

Milestones should remain coherent and reviewable. Do not add later-phase capability merely because
it is mentioned in the roadmap, and do not turn this document into a class-by-class implementation
manual.

## 31.1 Module boundary and refactoring policy

AIRTS is a modular monolith. `airts.simulation.Simulation` is the stable public facade,
authoritative state owner, and tick orchestrator. Authoritative world state types live under
`airts.world`; routing and spatial-query mechanisms live under `airts.navigation`; cohesive
simulation behaviors live under `airts.systems`; persistence and replay live under
`airts.adapters`; and Pygame/OpenGL code lives under `airts.presentation`. Runtime dependencies
point from presentation and adapters toward the facade, from the facade toward systems, and from
systems toward navigation, world, and stable top-level contracts. World and navigation code never
depend on systems, adapters, presentation, or the facade.

Split modules by responsibility, invariant, and reason to change rather than by a fixed line
count. A large cohesive module may be acceptable; a smaller module with unrelated responsibilities
is not. Avoid generic dumping grounds such as `utils`, `helpers`, or `manager`, and introduce a
subpackage only when it represents a durable boundary.

Refactor one responsibility at a time behind stable interfaces. Establish the test baseline first,
preserve the documented `airts.Simulation` facade, commands, persistence and replay schemas, tick
and event order, and seeded determinism, then run focused tests before the full suite. Internal
implementation paths are not parallel public facades: moved code has one canonical package owner.
A flag-day rewrite requires an explicit milestone and migration contract.

## 31.2 Repository ownership

| Location | Responsibility |
| --- | --- |
| `src/airts/world/` | Authoritative entities, maps, occupancy, visibility, and projectiles |
| `src/airts/navigation/` | Deterministic routing, steering support, and spatial indexing |
| `src/airts/systems/` | Command execution and tick-driven domain behavior |
| `src/airts/adapters/` | Persistence and replay at the simulation boundary |
| `src/airts/presentation/` | Input, inspection, software rendering, and OpenGL rendering |
| `tests/unit/` | Isolated domain-component contracts |
| `tests/integration/` | Facade, adapter, milestone, and cross-system behavior |
| `tests/movement/` | Collision and swarm behavior with specialized fixtures |
| `tests/performance/` | Explicit workload and frame-rate contracts |
| `tests/architecture/` | Dependency and documentation structure policies |

The old top-level compatibility modules have been removed under ADR 0005. Application and renderer
code is imported from `airts.presentation`; world state from `airts.world`; routing, collision
primitives, and spatial indexing from `airts.navigation`; authoritative tick behavior from
`airts.systems`; and persistence or replay from `airts.adapters`. In particular,
`airts.systems.movement` owns movement behavior while `airts.navigation.collision` owns collision
radii and steering candidates. No second `movement.py` or top-level re-export may obscure that
boundary.

`tests/architecture/test_architecture_boundaries.py` enforces dependency direction, canonical
imports, absence of the removed modules, unique source basenames, a clean repository root, and the
stable `Simulation` facade. This structure expresses ownership, not a target file size, and
packages should remain shallow until a new durable boundary is demonstrated. Removing the
transitional imports is a bounded prototype source migration, not permission to change command,
save, replay, event, or deterministic behavior.

---
