# ADR 0005: Require canonical module ownership

**Status:** Accepted

## Context

An earlier refactor left top-level compatibility modules beside their implementations in shallow
packages. Movement also had names in both navigation and system layers. The duplicate paths made
ownership unclear and expanded the import surface a contributor had to understand.

AIRTS is a research prototype. Its supported stability contract is the `Simulation` facade,
commands, persistence and replay behavior, tick and event order, and seeded results—not every
historical internal module path.

## Decision

Each concern has one canonical owner:

| Concern | Canonical owner |
| --- | --- |
| Pygame software presentation | `airts.presentation.app` |
| Entities, maps, occupancy, projectiles, and visibility | focused modules under `airts.world` |
| Routing and spatial indexing | focused modules under `airts.navigation` |
| Collision radii and steering candidates | `airts.navigation.collision` |
| Authoritative movement and recovery | `airts.systems.movement` |
| Other tick-driven behavior | focused modules under `airts.systems` |
| Persistence and replay | `airts.adapters.persistence` and `airts.adapters.replay` |

Former top-level compatibility modules are removed rather than retained as re-export shims. Source
and tests import canonical paths. `airts.Simulation is airts.simulation.Simulation` remains an
explicit public-facade contract.

Architecture tests reject forbidden dependency directions, removed compatibility imports,
ambiguous implementation ownership, and repository-root Python scripts.

## Consequences

Repository browsing reveals one owner for each concern. Code that used undocumented transitional
imports must migrate, which is an accepted prototype-stage source break. Canonical ownership does
not introduce services or change authoritative behavior.
