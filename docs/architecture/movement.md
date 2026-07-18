# AIRTS Movement Architecture

This document owns current pathfinding, formation, steering, collision, arrival, and blocked-unit
behavior.

[Design overview](../design.md) · [Core architecture](core.md) ·
[ADR: single movement pipeline](../decisions/0008-single-movement-pipeline.md)

## Goals

Movement favors understandable deterministic behavior over a collection of specialized crowd
solvers. The same initial state, seed, and commands produce the same routes, formation stations,
positions, collisions, events, and failures.

The system has three stages:

```text
command or automation target
        -> deterministic formation stations
        -> cached four-direction routes
        -> one local-steering and collision pipeline per tick
```

Every active unit uses this pipeline. Group size, map topology, automation kind, and unrelated idle
units do not switch the controller into another movement algorithm.

## Authoritative and derived state

Authoritative movement state includes each entity's position, current movement target, remaining
route, state, and progress evidence. Patrol and defense automations also own their target geometry
and inspectable station assignments.

Derived state includes cached navigation fields, building-cell snapshots, spatial indexes, nearby
collider records, and steering candidates. Derived state may be discarded and rebuilt without
changing command meaning, persistence, or replay.

A direct move exists while its target and route are active. There is no separate persisted
`command_target`, completed-order identity, reservation lattice, coherent-flow state, or solver
mode cache.

## Four-direction routing

Routes use the grid's four cardinal neighbors. A route never takes a diagonal grid shortcut through
water, rock, or a building corner. Terrain costs are authoritative: roads and bridges may be cheaper
than grass, while forest is more expensive.

The routing service:

- applies deterministic goal and neighbor tie-breaking;
- treats terrain and building footprints as static topology;
- finds a valid interaction point around building targets;
- caches shared reverse fields for compatible goals and obstacle state;
- routes a commanded group through at most four destination-derived shared lanes before units
  branch toward their stations;
- shares cached direction data across units without sharing mutable entity state;
- bounds route construction work per controller and per tick;
- invalidates affected caches when authoritative topology changes;
- reports an explicit pathfinding failure when no route exists.

One weighted reverse-field builder handles every terrain mix. Replans that depend on current
dynamic unit positions are not stored as static shared fields.

## Formation stations

Group commands and persistent patrol or defense work assign distinct destinations rather than
sending every unit to one coordinate. Stations are generated deterministically from the target
geometry and unit collision spacing.

Every station must be:

- inside the map;
- on passable terrain;
- clear of building footprints;
- reachable from the relevant group when the command is accepted;
- unique within its coordinated formation.

Point and undersized-area targets may expand into nearby passable space so the objective does not
imply that every body must overlap inside the original geometry. Polyline stations use the length
of the route or defense line rather than concentrating only at vertices.

Membership changes preserve identity where it remains valid. An existing member keeps its station
when that station is still passable, reachable, unique, and part of the current compact station set.
Only newcomers and members whose stations became invalid or fell outside a contracted formation are
assigned to remaining stations. This prevents routine reinforcement from turning established units
around while still allowing a formation to contract or respond to topology changes.

Formation station maps are authoritative automation state and are serialized. Route caches and
local steering state are not.

## Local steering and collision

On every movement tick, active units are processed in canonical entity order. Each unit:

1. consumes route points it has reached;
2. chooses a desired step toward its next route point;
3. inspects nearby bodies through a deterministic spatial index;
4. evaluates a bounded set of local steering candidates;
5. clamps motion against terrain, buildings, and collider contact;
6. applies deterministic overlap correction and physical yielding;
7. moves no farther than `speed * TICK_SECONDS`;
8. records progress, arrival, yielding, blockage, or failure evidence.

Nearby spatial queries keep ordinary work local rather than comparing every possible unit pair.
Mass and collision radius affect physical response, but every mobile kind follows the same control
flow. Held units and immovable terrain or buildings remain fixed.

Local steering may flex a formation while units pass obstacles or one another. The authoritative
goal is the assigned station or route target, not preservation of rigid visual ranks during transit.

## Arrival and recovery

A unit completes a movement leg when it reaches its current target within the movement tolerance.
If a formation unit stalls after safely entering the target's local neighborhood, it may adopt that
collision-safe reached point as its station instead of orbiting an unreachable cell center. The
updated station remains authoritative and serializable. The consumed route is cleared and the unit
enters the state required by its command or automation. A completed direct move leaves no hidden
solver membership that can move the unit again.

Progress tracking distinguishes slow forward motion from a true stall. A stalled unit may request a
bounded deterministic replan around current fixed obstacles. If the target is no longer reachable,
the command or automation records an explicit blocked or failed result. Recovery never teleports a
unit, ignores terrain, or exceeds its per-tick speed.

## Performance strategy

Movement performance comes from shared cached routing, bounded route work, deterministic spatial
broadphase queries, reuse of nearby collider data, and avoiding unnecessary replans. It does not
come from size thresholds, reduced collision rules, hidden unit removal, or switching to a second
large-force solver.

Timing tests are machine-dependent diagnostics. Correctness contracts protect deterministic replay,
four-direction topology, unique valid stations, station retention, speed bounds, collision safety,
eventual progress where capacity exists, and explicit failure where it does not.

## Known limitations

The local controller is not ORCA, ClearPath, or a continuous crowd-flow solver. Very dense opposing
groups can form queues, formations can deform in narrow passages, and local choices are not a global
minimum-time solution. A target with insufficient reachable surrounding capacity can remain
blocked.

These limitations are preferable to hidden solver changes and complex persisted crowd state in the
current research prototype. A different movement algorithm requires measured evidence, a clear
deterministic replay contract, and a superseding architecture decision.
