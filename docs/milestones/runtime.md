# Runtime contract

[Design](../design.md) owns scope; [performance](performance.md) owns rendering and workload gates.
This document describes implemented behavior and identifies reserved schema capabilities.
Exact schemas/profiles live in their code owners.

## World and spatial input

- Static weighted grid; grass, road, forest, water, rock, and bridge terrain. Entity profiles and
  footprints: [map_model.py](../../src/airts/world/map_model.py). Buildings are hard obstacles;
  mobile-unit exclusion belongs to physical colliders, not an impassable cell wall.
- Point, polyline, rectangle, and freehand inputs normalize to `PointTarget`, `PolylineTarget`,
  and `PolygonRegion`. Rectangles/freehand become validated polygons.
- Typed selections and spatial references have stable IDs. Naming persists a user region;
  edits replace whole geometry. Deleting a used reference cancels affected automations explicitly.
  Ownership validation is atomic; destroyed entities are pruned from selection.
- Visibility is owner-specific: visible, explored, and unexplored cells. Future provider inputs
  must distinguish visible evidence, observation history, and inference from complete internal state.

## Commands and automations

[commands.py](../../src/airts/commands.py) owns tagged, serializable commands;
[automations.py](../../src/airts/automations.py) owns parameters and legal lifecycle transitions.
Move, stop, hold, attack, spatial edits, and automation actions share deterministic validation.
Failures identify their operation, subject, and reason; no invalid proposal silently changes state.

A unit has at most one active controlling assignment and may retain a suspended assignment during
explicit repair. Direct control detaches units, records override, and leaves remaining members
working. Creating a new patrol/defend on explicitly selected units replaces their older ordinary
assignment even at lower priority. Background claims use emergency status, priority, and recency.
Empty ordinary work is canceled; valid future-unit sources may wait. Terminal work leaves live UI.
Manual factory override pauses its controller; resume must reclaim the factory.

Creation follows proposed -> validating -> active. The schema also permits `awaiting_confirmation`,
but the current command/UI flow does not enter it or provide a confirmation action.
Waiting can resume when resources/capacity return, blocked records unavailable action, and failed
records unrecoverable loss. Pause/resume/cancel and all terminal transitions retain structured
reasons and history.
Supported templates: patrol, defend, production, construction, reinforcement, repair-and-return,
and economy. Reinforcement transfers eligible units and fails when its target is invalidated.

## Movement and combat

All static move/patrol/defend/repair/production/combat routes use shared deterministic routing with
bounded fields and fair per-controller/global budgets. Dynamic unit-penalty replans are uncached.
Weighted four-neighbor reachability, terrain/building clearance, and speed bounds remain required.
Patrol groups follow the same cycle direction. Small defenses use exact stations; undersized targets
expand into unique reachable holding space. Same-owner, same-target defenses coordinate membership
and retain valid stations on growth; contraction can reassign outer survivors into compact slots.
Line defenses cover the full polyline. Glow follows the authoritative assembly radius.

Current movement uses continuous steering/push for small forces and topology-sensitive manual
traffic, with coherent translation/reservation slots for large forces. Idle friendlies may yield;
held units, stationary enemies, and docked defenders are anchors. Reservations retain surviving
identities while spacing is valid; topology changes invalidate unsafe derived state. Queueing must
not trigger unbounded replanning or cut across water. Large expanded defenses may adopt valid,
pressure-free arrival positions inside their envelope; small groups retain exact stations.
[Movement tests](../../tests/movement/test_swarm_movement.py) and
[crowd contracts](performance.md#crowd-and-routing) retain the acceptance details.

Opportunistic fire does not cancel locomotion. Explicit attack pursuit stops at weapon range.
Projectiles retain their last target destination: a removed target leaves the shot/trace alive
until arrival, with no damage to bystanders. Repair is explicitly requested, selects units strictly
below the default 30% health threshold, prefers repair hubs, and restores suspended work or returns
to the previous position. Low health alone does not replace the current automation.

## Economy, production, and construction

Each owner has an integer resource balance. Generators add periodic income even without an
automation; [economy.py](../../src/airts/systems/economy.py) owns the amount and interval.
An economy automation tracks an owner-balance target; it does not enable or accelerate income.
Configuration and seed govern reproducible enemy spawning. Direct `Simulation` construction
disables ambient spawning by default; [README](../../README.md#run) owns CLI defaults and controls.

Factories produce scouts, light/heavy tanks, and builders through one paid production path;
[simulation.py](../../src/airts/simulation.py) owns default build timing and construction progress.
Finite staged quantities run FIFO; finite work preempts the continuous loop, which resumes
afterward. A newer loop replaces the previous unfinished loop without discarding finite requests.
No spawn space/resources means waiting; source destruction fails the job. IDs are deterministic.
`Produce + Defend` retargets each selected factory's existing loop without changing its unit kind
and attaches existing/future units to a linked defense. Polygons gather; polylines spread along the
line. Factories remain independent owners of costs, queues, and progress.

Builders construct factories, repair hubs, and generators, not command centers. Entity profiles
own costs, build speeds, and build ranges. Placement validates capability, ownership, bounds,
terrain, footprints, and reservations. One job charges once; each assigned builder contributes
only within its build range of the footprint and outside it. Builders route to the perimeter first.
Multiple builders accelerate one job without duplicating cost or the building.

Shift-placement appends FIFO jobs and keeps placement active. Waiting jobs reserve footprints,
not resources or builders; activation claims available builders and charges once. Ordinary placement
replaces pending jobs for those builders. Right-click closes placement without issuing movement or
altering accepted work. A completed occupied site waits with `SITE_OCCUPIED`; commit occupancy before
publishing a building. Queue, progress, resource, and assignment state survive save/replay.

## Persistence and presentation

[persistence.py](../../src/airts/adapters/persistence.py) and
[replay.py](../../src/airts/adapters/replay.py) own versioned JSON schemas and rejection of malformed
or incompatible input. Saves preserve complete authoritative state, selections, queues, combat,
movement progress, events, and counters; replay compares the final `snapshot()` and ordered events
against recorded expectations, rather than comparing the full `export_state()` representation.
Loaders reject schema versions other than their current constants; no automatic migration exists.
Derived navigation/traffic caches rebuild deterministically; presentation history is not saved.
[events.py](../../src/airts/events.py) provides ordered, filterable events and JSON Lines export.

The UI has status/scrollable automation, pannable canvas, and selection/action rails. It displays
valid controls for the selected kind and selected count. Double-click selects visible friendlies
of one kind; Escape clears selection/placement/inspection. Same-kind actions broadcast ordinary
commands to the compatible selection. Linked production/area defense share a management item;
scrollbars clamp/backfill when work disappears. Placement previews validity and job progress.
Settings owns save/load/reset, resolution, and timing. UI loading clears presentation selection
and inspection even though the persistence adapter restores the saved domain selection.
[README](../../README.md#controls) owns keys and [CLI limitations](../../README.md#run).
Window close stops before another tick/draw. Normal and exceptional exits release GPU objects,
events, fonts, display, and Pygame state. Building hit tests use visible footprints.
