# ADR 0008: Use one deterministic local-steering movement pipeline

**Status:** Accepted

## Context

AIRTS accumulated separate large-force reservation, coherent-flow, and local movement paths.
Controller selection depended on group-size thresholds, topology, automation membership, and
retained solver state. Direct orders also persisted a second `command_target` identity so a
specialized large-force tail could recognize completed members.

These mechanisms made ordinary pathfinding and formation behavior difficult to follow. Fixes for
one mode repeatedly added state, thresholds, invalidation rules, persistence concerns, and
regressions in another mode. That complexity is disproportionate for a research prototype whose
first requirement is inspectable deterministic behavior.

## Decision

Every mobile unit uses one deterministic movement pipeline:

1. deterministic formation planning assigns unique passable stations when a group needs them;
2. a cached weighted four-direction routing service provides terrain- and building-safe paths;
3. one local-steering and physical-collision loop advances every active unit.

Group routes use at most four destination-derived shared lanes, then branch toward individual
stations. This bounds route-field variety without introducing a second controller.

Existing formation members retain their station while it remains valid and belongs to the current
compact station set. New or displaced members receive the remaining stations deterministically.
A stalled member already inside the destination's local neighborhood may adopt a collision-safe
reached point as its new authoritative station, avoiding indefinite orbit around an obstructed cell
center.

The implementation has no large-force lattice, coherent-flow controller, size-based solver switch,
or persisted `command_target`. A direct order is represented by its active movement target and
route. Completed orders leave no hidden crowd membership.

The headless `Simulation` remains authoritative. Route fields, spatial indexes, steering
neighborhoods, and other optimization state are derived and rebuildable. Entity positions,
movement targets, active routes, automation targets, and formation station maps are authoritative.

## Consequences

Movement has one call path and one set of speed, terrain, collision, progress, and failure rules.
Performance work focuses on shared route caches, spatial broadphase queries, bounded route work, and
reuse of local data rather than alternate solvers.

Dense crowds may queue or deform and the local solution is not globally optimal. AIRTS accepts that
limitation in exchange for behavior a contributor can understand, test, save, and replay. A future
crowd algorithm must demonstrate a failing workload, preserve deterministic authority, and replace
this decision explicitly.
