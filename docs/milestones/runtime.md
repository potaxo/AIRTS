# Runtime contract

[Design](../design.md) owns scope; [performance](performance.md) owns rendering and workload gates.
This document describes implemented behavior and identifies reserved schema capabilities.
Exact schemas/profiles live in their code owners.

## World and spatial input

- Static weighted grid; grass, road, forest, water, rock, and bridge terrain. Entity profiles and
  footprints: [map.py](../../src/airts/world/map.py). Buildings are hard obstacles;
  mobile-unit exclusion belongs to physical colliders, not an impassable cell wall.
- Point, polyline, rectangle, and freehand inputs normalize to `PointTarget`, `PolylineTarget`,
  and `PolygonRegion`. Rectangles/freehand become validated polygons.
- Typed selections and spatial references have stable IDs. Naming persists a user region;
  edits replace whole geometry. Deleting a used reference cancels affected automations explicitly.
  Ownership validation is atomic; destroyed entities are pruned from selection.
- Visibility is owner-specific: visible, explored, and unexplored cells. Future provider inputs
  must distinguish visible evidence, observation history, and inference from complete internal state.

## Commands and automations

[commands.py](../../src/airts/contracts/commands.py) owns tagged commands, validation and control claims;
[automations.py](../../src/airts/contracts/automations.py) owns parameters and legal lifecycle transitions.
Move, stop, hold, attack, spatial edits, and automation actions share deterministic validation.
Failures identify their operation, subject, and reason; no invalid proposal silently changes state.

A unit has at most one active controlling assignment and may retain a suspended assignment during
explicit repair. Direct control detaches units, records override, and leaves remaining members
working. Creating a new patrol/defend on explicitly selected units replaces their older ordinary
assignment even at lower priority. Background claims use emergency status, priority, and recency.
Empty ordinary work is canceled; valid future-unit sources may wait. Terminal work leaves live UI.
Manual factory override pauses its controller; resume must reclaim the factory.

Automation edits validate all requested fields before committing changes, including linked production
defense. `modified_tick` records parameter/title/priority edits as well as lifecycle transitions;
an edit does not add a lifecycle transition.

Creation follows proposed -> validating -> active. The schema also permits `awaiting_confirmation`,
but the current command/UI flow does not enter it or provide a confirmation action.
Waiting can resume when resources/capacity return, blocked records unavailable action, and failed
records unrecoverable loss. Pause/resume/cancel and all terminal transitions retain structured
reasons and history.
Supported templates: patrol, defend, production, construction, reinforcement, repair-and-return,
and economy. Reinforcement transfers eligible units and fails when its target is invalidated.

## Movement and combat

One movement controller handles all army sizes, with shared group-path planning for direct commands
and patrol/defend dispatch.

Static routing uses reverse Dijkstra fields over four-neighbor weighted terrain. A bounded LRU cache
shares each destination field across units; changing building obstacles changes the cache key.
Group orders preserve translated positions, fitting the group inside map edges. When terrain or
buildings prevent that placement, they allocate reachable destination slots. Ordinary mobile units
remain local collision obstacles, so an occupied destination can wait for them to move. Groups share
an approach route and branch through clear terrain to their destinations.
Movement shortens visible route segments and submits all actual displacement through the local
contact controller described below. Opposing groups alternate processing priority and prefer a common
passing side. Sustained stalls trigger budgeted replanning; a blocked order stays queued.
Numeric movement, lookahead, and exact visibility unions run in serial compiled CPU loops without
fastmath or parallel reductions. Compilation happens during simulation construction; compilation
errors propagate. Numeric buffers and navigation caches rebuild after load and do not enter saves.

Line defenses spread along the line. Area defenses use separated stations, expanding small gathering
areas into hexagonal slots with a unit-width aisle between neighbors. This deliberately replaces
maximum-density packing: the aisles reduce contact pressure, while settled units can yield and
recover their established stations without reservations or global rematching. Same-owner defenses with the same target share unique stations and retain
valid slots on membership changes. Patrol and defense route dispatch have per-controller and global
budgets. Crowd throughput, separation, and frame-time requirements remain owned by
[performance](performance.md#crowd-and-routing); they must be measured, not inferred from the design.
Ordinary defenses can also acquire deployment slots when their initial stations lack clearance;
save/load preserves those expanded stations without changing the gathering flag.

### Contact, yielding and Hold anchors

The controller replaces path-only sequential movement, fixed holders and mobile whole-cell route
obstacles. Its scope includes contact prediction/certification in navigation, intent and all-body
submission in movement, Hold command state, defense recovery and save/replay. It retains one
controller, the public facade, command authority, deterministic 10 Hz execution and existing gates.

Idle and Stop units can yield through bounded passive displacement without acquiring a movement
order or returning to their previous positions. Moving units retain their orders while yielding.
Friendly and hostile mobile units remain physical colliders. Buildings, impassable terrain and map
edges remain hard constraints; neither pushing nor recovery may tunnel, exchange identities or
exceed the existing per-tick total displacement limit. Mobile units, including holders, must not
become impassable whole-cell walls in static routing. Safe waiting is valid when space is unavailable.

Accepting HoldPosition establishes an authoritative anchor at the unit's current position. Contact
can displace its body without replacing that anchor, clearing Hold, changing control ownership or
enabling enemy pursuit. With pressure relieved and a clear reachable anchor, it returns through the
same constrained movement controller, including ticks with no ordinary movement paths. Recovery
must settle within a small arrival tolerance rather than oscillate or oppose passing traffic with
unbounded force. Occupied or incompatible anchors can delay recovery; safety takes precedence over
exact arrival. Reissuing HoldPosition establishes a new anchor at the current position. Stop, Move
and other accepted control replacements end Hold recovery; rejected commands preserve it.

Passive contact does not fabricate movement-started/completed events for an absent movement order.
Occupancy and spatial queries must reflect every submitted displacement, including pathless units.
Defense stations retain their identities when their units yield; safe recovery must not repeatedly
remap stations or immediately undo contact displacement. Hold anchors and defense stations are
distinct owners of intent, not competing independent physical controllers.

Navigation generates a preferred displacement, predicts bounded contact corrections, then certifies
each submitted swept disc against static supporting planes and relative-motion contact planes.
Stable ID order supplies separation normals for identical centers; existing overlap recovers
gradually without becoming deeper. Every mobile body participates, including pathless bodies and
enemies. Static buildings, terrain and boundaries do not yield. One speed circle bounds the total
step: contact and recovery cannot add another independently capped displacement. Passive yielding
and recovery use a lower speed allowance than active travel. A bounded side step away from incoming
traffic opens a lane instead of dragging stationary bodies through the entire formation.

Two deterministic contact sweeps transmit short-chain pressure; longer chains can advance over
multiple ticks. Continuous certification is mandatory even when prediction cannot fully resolve
pressure, so exhausted local work reduces throughput instead of permitting penetration. Original
position buckets include both units' full displacement envelopes and remain valid throughout the
solve. Stable resting bodies need no displacement certificate; approaching movers still certify
against them and wake passive contact. All changed bodies update occupancy before projectiles and
combat read final positions; automation intent is generated before this movement phase.

Hold has an arrival dead zone and a contact quiet interval. Actual incoming pressure suppresses its
own return preference; it resumes low-speed recovery after the interval. Internal recovery does
not acquire control or emit player movement-started/completed events. Nonconflicting anchors may
request finite local yielding along a return corridor; a substantially displaced returner takes
precedence over a neighbor's small settling correction. The latter waits after yielding, avoiding
opposed spring forces. Conflicting goals or a stationary occupant at the anchor cannot be evicted
indefinitely; safe waiting takes precedence. Settled defenders use the same weak recovery behavior,
retain station IDs and assignments, and wait under pressure. Initial defense deployment and enemy
pursuit remain their existing business intents rather than being weakened into station recovery.

Ordinary path progress/stall recovery still uses the shared route budgets. Hold uses its own internal
waypoints, a retry interval and the existing global route allowance; it never replans every tick.
A failed static route sleeps until building obstacles change (the terrain is static). Its recorded
failed-obstacle snapshot prevents an unreachable anchor from consuming route budget forever;
unavailable dynamic space is handled locally and does not modify the anchor. A newly invalid
recovery segment is discarded and retried within that same allowance. Recovery ends by stopping
inside the arrival tolerance, without snapping coordinates or promising impossible exact arrival.

Executable acceptance belongs to [movement tests](../../tests/integration/test_movement.py),
[large-force regressions](../../tests/integration/test_large_force_behavior_regressions.py), and
[save/load tests](../../tests/integration/test_persistence.py). Hold fixes an anchor, not the body's
coordinates at every tick.

Opportunistic fire does not cancel locomotion, including defenders traveling past enemies outside
their assigned defense area. Only an actual pursuit can interrupt that travel; explicit attack
pursuit stops at weapon range.
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
replaces pending jobs for those builders only after validation, including control claims, succeeds.
Rejected placement preserves accepted queues. Right-click closes placement without issuing movement or
altering accepted work. A completed occupied site waits with `SITE_OCCUPIED`; commit occupancy before
publishing a building. Queue, progress, resource, and assignment state survive save/replay.

## Persistence and presentation

[persistence.py](../../src/airts/adapters/persistence.py) and
[replay.py](../../src/airts/adapters/replay.py) own versioned JSON schemas and rejection of malformed
or incompatible input. Saves preserve complete authoritative state, selections, queues, combat,
movement progress, events, and counters; replay compares the final `snapshot()` and ordered events
against recorded expectations, rather than comparing the full `export_state()` representation.
Saves use v10 without traffic reservations. Anchors, internal recovery waypoints/retry tick,
contact quiet timers, failed static-route obstacle snapshots and established-defense arrival state
are authoritative: losing them can change later movement or consume different route budgets.
They round-trip with ordinary movement progress, ownership, combat, counters and events. Spatial
buckets, numeric scratch buffers, contact candidates and shared navigation fields are disposable
caches and rebuild after load. Presentation history is not saved. Mobile units may share occupancy
cells, while buildings remain exclusive obstacles. Loading v10 preserves subsequent deterministic
execution, including saves taken while a holder is displaced and still recovering.

The loader supports v7/v8/v9 saves. Legacy holders use their recorded body position as the migration
anchor; those files never recorded the original anchor, so it cannot be recovered. Recovery state
starts fresh and old-run trajectories are not promised. v7/v8 traffic reservations are validated
and discarded and their obsolete congestion/progress state is reset. Saving again writes v10;
other older save schemas remain unsupported. Replays use v9 and explicitly reject earlier schemas,
including v8: their expected trajectories/events belong to the previous movement behavior. There
is no automatic replay migration. New replay verification still compares snapshot and ordered
events; save continuation tests additionally compare complete exported authoritative state.

[events.py](../../src/airts/contracts/events.py) provides ordered, filterable events and JSON Lines export.

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
