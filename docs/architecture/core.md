# AIRTS Core Architecture

This document owns the current simulation, command, automation, world-state, persistence, replay,
and presentation contracts. Movement and routing are described separately in the
[movement architecture](movement.md).

[Design overview](../design.md) · [Research roadmap](../roadmap.md)

## Authoritative simulation

`airts.simulation.Simulation` is the public facade, state owner, and tick orchestrator. It owns:

- the loaded map and entities;
- resource balances;
- active and historical automations;
- entity assignments and temporarily suspended assignments;
- player-created spatial references and grounded selection;
- projectiles, visibility, and structured events;
- the deterministic seed, current tick, and command history.

The simulation advances at a fixed 10 Hz. Presentation rate and input polling do not alter tick
duration. A headless caller can construct a simulation, submit commands, advance ticks, save it, and
replay it without initializing Pygame.

Within a tick, the facade invokes systems in a stable order: economy and configured enemy
generation, automation work, movement and collision, projectiles and combat, then visibility.
Systems may mutate authoritative state only while called by the facade.

## Command boundary

Commands are typed, serializable values defined in `airts.commands`. The frontend, replay adapter,
tests, and future language adapters use the same command boundary.

Before mutation, command handlers validate the relevant combination of:

- schema and parameter ranges;
- entity existence, ownership, and capability;
- spatial reference existence and geometry;
- terrain, building footprint, and reachability;
- resource availability;
- automation ownership and assignment conflicts.

A rejected command returns a structured failure and leaves authoritative state unchanged. Accepted
commands and validation failures are recorded as structured events. System actions such as entity
removal also use replayable commands when they affect authoritative state.

Direct player commands have immediate authority over the selected entities. Move, stop, hold, and
attack commands detach conflicting normal automation assignments rather than allowing two
controllers to drive one unit. The affected automation records the override and continues with its
remaining members when possible.

## Automation model

Implemented automation kinds are:

- patrol;
- defend;
- production;
- construction;
- reinforcement;
- repair and return;
- economy.

An automation stores its kind, owner, title, priority, original instruction, creation source,
assigned entity IDs, typed parameters, current status, reason code, and transition history. Its
parameters and lifecycle are serializable.

Lifecycle states are `PROPOSED`, `VALIDATING`, `AWAITING_CONFIRMATION`, `ACTIVE`, `WAITING`,
`PAUSED`, `BLOCKED`, `COMPLETED`, `FAILED`, and `CANCELED`. Only explicit legal transitions are
allowed. A same-state transition cannot silently change its reason.

Each entity has at most one active automation assignment. Repair-and-return is the deliberate
exception to ordinary reassignment: it suspends a valid previous assignment and revalidates it
after repair. A unit with no assignment returns to its recorded pre-repair position. If neither
resume path is valid, the failure remains visible rather than being concealed by an arbitrary idle
transition.

Automations with a source of future units may wait with no current members. Other automations are
canceled or failed when their required entities or target capability no longer exist. Pausing,
resuming, cancellation, priority changes, and supported target changes pass through ordinary
commands.

## World and spatial model

The game map is a validated static rectangular grid. Each cell has one terrain kind and a movement
cost. Water and rock are impassable; roads, grass, forest, and bridges are passable with different
costs. Buildings occupy hard grid footprints. Mobile units use continuous positions and physical
collision rather than claiming exclusive grid cells from one another.

Entity behavior derives from immutable profiles for health, vision, footprint, speed, weapons,
production cost, construction cost, and builder capability. The simulation owns current health,
position, state, active target, route, attack state, and progress evidence.

UI input normalizes into three geometry types:

- a point target;
- a polyline target;
- a polygon region.

Rectangles become polygons and freehand input is simplified into a valid polygon. Spatial
references receive stable IDs and may be selected, named where supported, replaced as whole
objects, or deleted. Deleting a route or region explicitly cancels dependent automations.

The current runtime does not have map-defined semantic regions, vertex-level editing, or
multi-region automation semantics.

## Economy, construction, and production

Every owner has one integer resource balance. Resource generators credit their owner on a fixed
tick interval without requiring an automation. Economy automations observe authoritative balances
and complete when their target is reached.

Builders construct factories, repair hubs, and resource generators. Placement validates bounds,
terrain, footprints, ownership, and builder capability before reserving a site. Builders contribute
work only while within build range. A Shift-appended construction queue reserves non-overlapping
sites in FIFO order; a waiting job charges resources only when it becomes active. Completed work
does not publish a building until its footprint is clear.

Factories share one authoritative production path for finite queues and continuous loops. Finite
work has priority and a continuous loop resumes afterward. Each factory owns its timing, resource
reservation, queue, spawn search, and events. A production loop may attach its current and future
units to one line or area defense without merging factory state in the frontend.

## Combat, repair, and visibility

Armed units may fire opportunistically at visible hostile units in range without losing a valid
move, patrol, defend, or return route. Explicit attack orders route toward a valid interaction area
and stop pursuit once weapon range is reached. Projectiles resolve deterministically; a projectile
whose target disappears finishes its visible trip to the last destination without applying damage.

Repair chooses the first reachable destination class in this order: repair hub, friendly factory,
then command center. Within a class it uses path cost and deterministic tie-breaking. Repair work
does not begin until the unit reaches the destination.

Visibility is player-specific. The simulation tracks exact currently visible cells and persistent
explored cells. Presentation may display that state but does not calculate or override it. There is
currently no line-of-sight occlusion or last-known enemy record.

## Persistence, replay, and events

Complete-state saves use an explicit schema and reject incompatible versions. They preserve the
map, seed, tick, entities, resources, automations, assignments, spatial references, visibility,
projectiles, construction and production work, configured enemy generation, events, and other
authoritative continuation state.

Routing caches, spatial indexes, local steering neighborhoods, and other reconstructible
optimization state are derived and are not persistence contracts. Loading rebuilds them from
authoritative state.

Replay stores the initial scenario and deterministic configuration together with submitted
commands. Re-execution verifies the resulting authoritative state and events. Replay never records
presentation frames as simulation input.

The event log uses stable sequence numbers and supports JSON Lines export. Commands, validation,
movement, path failures, automation transitions, production, construction, resources, repair,
combat, visibility, and spatial editing record structured evidence where relevant.

## Presentation boundary

AIRTS has one interactive `pygame-ce` software frontend. It owns input modes, selection display,
panels, camera transforms, construction previews, help, and settings. It reads simulation state and
submits commands; it does not advance domain behavior except by requesting fixed simulation ticks.

The frontend may cache terrain, transformed geometry, and text. Those caches are presentation-only
and never influence hit testing, paths, combat, visibility, persistence, or replay. Window closure
and exceptional exits release Pygame resources cleanly.

## Engineering boundaries

AIRTS remains a shallow modular monolith. Split modules by responsibility and reason to change, not
by a line-count target. Avoid generic `utils`, `helpers`, or `manager` modules. Preserve one
canonical import path for each implementation concern and keep the `Simulation` facade stable.

Architecture tests enforce dependency direction, canonical package ownership, the public facade,
and documentation-link integrity. Behavior tests are grouped by unit, integration, movement, and
performance intent.
