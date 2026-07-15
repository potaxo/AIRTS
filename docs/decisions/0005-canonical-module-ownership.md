# ADR 0005: Require canonical module ownership

**Status:** Accepted

## Context

The modular-monolith extraction left twelve one-line top-level compatibility modules alongside
their authoritative implementations under `airts.world`, `airts.navigation`, `airts.systems`,
`airts.adapters`, and `airts.presentation`. Movement was additionally represented by both
`airts.navigation.movement` and `airts.systems.movement`. Although the re-exports preserved old
imports, they made repository ownership ambiguous, allowed two files with the same responsibility
name, and doubled the import surface that architecture tests and contributors had to understand.

AIRTS is still a research prototype. Its supported stability contract is the
`airts.simulation.Simulation` facade, `airts.Simulation`, command behavior, persistence and replay
schemas, event and tick order, and seeded results. The moved implementation modules were never a
second supported facade.

## Decision

Every implementation concern has one canonical module path:

| Concern | Canonical owner |
| --- | --- |
| Application and OpenGL presentation | `airts.presentation.app` and `airts.presentation.opengl_renderer` |
| Entities, maps, occupancy, projectiles, and visibility | focused modules under `airts.world` |
| Pathfinding and spatial indexing | focused modules under `airts.navigation` |
| Collision radii and steering candidates | `airts.navigation.collision` |
| Authoritative movement and recovery behavior | `airts.systems.movement` |
| Persistence and replay | `airts.adapters.persistence` and `airts.adapters.replay` |

The former top-level modules `app`, `entities`, `map_model`, `movement`, `occupancy`,
`opengl_renderer`, `pathfinding`, `persistence`, `projectiles`, `replay`, `spatial_index`, and
`visibility` are removed rather than retained as re-export shims. The former
`airts.navigation.movement` name is replaced by `airts.navigation.collision`, leaving
`movement.py` as an unambiguous systems responsibility. Source and tests import canonical paths.

Executable architecture rules reject imports from the removed paths, the return of a removed
top-level module, duplicate non-`__init__.py` source basenames, and Python scripts scattered at the
repository root. `airts.Simulation is airts.simulation.Simulation` remains an explicit test.

This decision supersedes only ADR 0001's compatibility-re-export clause. ADR 0001's modular
monolith, dependency direction, and stable `Simulation` facade remain in force.

## Consequences

Repository browsing and dependency review now reveal one owner for each concern, and imports cannot
silently drift back toward transitional paths. The architecture remains a shallow modular
monolith; this cleanup does not introduce services, new packages, command changes, state changes,
or serialization changes.

Code outside this repository that imported the undocumented transitional modules must migrate to
the canonical paths. AIRTS accepts that bounded source break during the prototype phase instead of
maintaining permanent duplicate modules. The supported facade and runtime behavior remain stable.

`tests/architecture/test_architecture_boundaries.py` is the focused acceptance contract. The normal
unit, integration, movement, performance, persistence, and replay suites verify that canonicalizing
imports did not change behavior.
