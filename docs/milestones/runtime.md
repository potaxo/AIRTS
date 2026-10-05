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
Group orders preserve physically compatible translated positions, fitting the group inside map
edges. When terrain, buildings, or overlapping source geometry prevent that placement, they allocate
reachable separated destination slots using the existing group planner. An already compressed
selection cannot become a set of mutually overlapping final goals. Ordinary mobile units
remain local collision obstacles, so an occupied destination can wait for them to move. Groups share
an approach route and branch through clear terrain to their destinations.
Movement shortens visible route segments and submits all actual displacement through the local
contact controller described below. The front key within each processing group uses the current
waypoint direction through turns and merges. Group priority still alternates each tick, with stable
IDs resolving ties. Opposing groups prefer a common passing side. Sustained stalls trigger budgeted
replanning; a blocked order stays queued.
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
obstacles. Contact certification also replaces the zero-displacement assumption for unsubmitted
members of an overlapping contact component. Its scope includes geometry and coordinated release
in navigation, intent and all-body submission in movement, spawn/goal capacity, Hold command state,
defense recovery and save/replay. It retains one
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

Two deterministic contact sweeps predict local travel and yielding. Compressed connected contacts,
and legal contact components with a sustained stalled travel or compatible return request, also
receive a joint feasible release trajectory before individual travel preferences are refined.
The existing monotonic progress counter admits this demand; conflicting resting destinations
remain waiting intents and cannot use repeated release to evict their occupants.
For distinct centers, a common dilation gives
`n_ij · (v_i - v_j) = alpha * d_ij >= 0`; opposing contact normals can pin a single
body against stationary neighbors even though this simultaneous motion is feasible. Stable ID
embedding supplies consistent directions at identical centers without reversing distinct-center
separation. Static supporting planes choose a feasible expansion center; bounded affine directions
allow release along an open corridor. Internal corners covered by a nearer adjacent blocked cell
use that cell's supporting plane for their combined rectangle; redundant cell-corner planes must
not obstruct tangential motion along a continuous wall. External discs cap the common component
step and participate when they reach contact, so recovery cannot transfer penetration to an
initially separated fringe.
Admitted recovery regions extend the contact graph through a bounded recovery-step envelope:
separated fringe discs that would consume that step's capacity join local release before they can
pin the interior. A finite queue visits each admitted body once and joins its existing contact
component. Active travel prediction retains contact-sized neighborhoods; passive endpoints also
inspect the displacement envelope needed to yield to incoming motion. This extends coordination
without changing physical radii or contact certification. A component containing every
body has no external disc constraints to enumerate; all internal safety constraints remain certified.
Passive bodies prefer the coherent release without adding the pairwise prediction a second time;
active travel retains its command preference. Individual refinement retains the certified release
and one shared speed circle. This is local contact geometry within the existing controller.

Compatible Hold anchors and already-arrived defense stations form local destination regions.
A body's first departure beyond its existing arrival tolerance records a return request tick;
the earliest retained request opens its corridor before the region returns together. Request age,
with stable ID only as the tie break, preserves priority across quiet waiting and local contact
changes. The opening body receives the passive half-speed allowance through geometric coordination,
without acquiring a player command. Original business active/stationary masks still own route
steering; a geometrically promoted weak returner must not acquire strong travel's 45-degree idle
or opposing-traffic detour. Unrelated destination regions coordinate independently.
The opening queue does not suspend harmless returns elsewhere in its destination region. A
follower retains its original weak route when its complete proposed step is independently clear
of every local disc and static obstacle. Returns needing another body's displacement wait for
coordination; bodies actually participating in release retain that certified release preference.

For a legal contact component with one weak opening body and no strong pressure, navigation first
opens a transverse lane. With `h` perpendicular to its submitted return segment and
`s_i = (p_i - p_leader) · h`, the field `v_i = beta * f(s_i) * h` uses a monotone side function.
Consequently every internal pair has `n_ij · (v_i - v_j) >= 0`, while the leader receives no
transverse seed. Two-sided and either one-sided fields are finite alternatives; shared scaling
respects static planes and every body's existing passive cap. External discs remain certified.
This frees space beside the corridor instead of transporting the whole formation away from the
anchor. Compressed contacts and other release geometry retain the certified affine directions.

Once the opening request reaches its anchor/station tolerance, its released phase remains until
the compatible region settles. Navigation projects the joint anchor gradient through a sparse
local active-set solve. Binding edges and dependent displacement rays share a trajectory scale;
independently clipping their return contributions can discard a feasible common rotation or
translation. A minimal common normal compensation handles touching configurations whose straight
return chord would penetrate. The complete blend with any existing release seed shares one speed
circle and static, internal and external certificates. Its scale does not increase the region's
anchor error relative to that seed; this does not claim that opening work decreases error every
tick. No pair's non-deepening constraint is relaxed.
Joint return generates its displacement once. It does not also predict an individual weak route
gradient that would later be discarded; such a forecast can report pressure from motion that is
never submitted. Real travel/opening forecasts and actual overlap discovery remain in both contact
sweeps. The joint trajectory still receives complete internal, external and static certification.
The internal projection omits pairs beyond both members' combined passive step envelopes, with
a numerical margin. Their distance throughout the tick is at least `d - cap_i - cap_j > R`, so
they cannot constrain any actually submitted half-speed trajectory. Intermediate projection
directions remain untrusted; the complete blend caps and final full-envelope certificates remain
mandatory. External strong traffic uses its own full travel envelope.

If joint return has no certified advance, or all remaining requests have accumulated sustained
lack of progress, the region opens another pending request. Unfulfilled requests retain their age
and monotonic progress history. A previously fulfilled request that is displaced again enters as
a new request with a new distance baseline; its old near-anchor best distance cannot invalidate
the new progress measurement or permanently outrank waiting companions. External strong pressure
invalidates the released phase while preserving the original weak business intent. Conflicting
anchors/stations remain unchanged and wait safely. Local graphs, numeric directions and leaders
rebuild each tick; the authoritative request tick/released phase survives save/load.

Existing overlap is allowed at the start of a recovery window, including legacy map/save positions
and explicitly injected test preparations. Swept pair distance never falls below
`min(initial_distance, radius_i + radius_j)`. With sufficient reachable capacity, the executable
[overlap recovery scenarios](../../tests/integration/test_overlap_recovery.py) require substantial
penetration reduction, all selected members to escape and complete, and eventual legal disc spacing;
their observation windows include continued recovery after extraction. Pressure-free bodies settle.
No unit teleports, changes identity or receives an unsolicited order. Components constrained by
walls or exhausted local capacity may wait; `contact_blocked_component_count` diagnoses missing
certified release capacity without promising impossible full separation. Conflicting Hold anchors
and defense stations remain intact as waiting intents and cannot undo recovery by recreating overlap.

Continuous certification is mandatory even when prediction cannot fully resolve
pressure, so exhausted local work reduces throughput instead of permitting penetration. Original
position buckets include both units' full displacement envelopes and remain valid throughout the
solve. Stable resting bodies need no displacement certificate; approaching movers still certify
against them and wake passive contact. All changed bodies update occupancy before projectiles and
combat read final positions; automation intent is generated before this movement phase.

Hold has an arrival dead zone and a contact quiet interval. Actual incoming pressure suppresses its
own return preference; successful lateral yielding also reports that pressure and refreshes the
quiet interval. A feasible yielding step must not immediately restart an opposing anchor return on
the next tick. Low-speed recovery resumes after the interval. Internal recovery does
not acquire control or emit player movement-started/completed events. Nonconflicting anchors may
request finite local yielding along a return corridor; retained request age coordinates opening
and common return rather than repeatedly reversing priority as offset magnitudes change.
Conflicting goals or a stationary occupant at the anchor cannot be evicted indefinitely; safe
waiting takes precedence. Settled defenders use the same weak recovery behavior,
retain station IDs and assignments, and wait under pressure. Initial defense deployment and enemy
pursuit remain their existing business intents rather than being weakened into station recovery.
Displaced arrived defenders keep a pending weak recovery path during their quiet interval, with
motion suppressed in the shared controller; completion/settling must not hide unfinished recovery.
Idle bodies after Move completion can still yield to later arrivals. They must actually reach their
accepted destinations before completion and settle when pressure ends; completion does not create
a Hold anchor at that destination. Internal extraction scenarios additionally require every selected
member to physically leave the unselected formation.

Ordinary path progress/stall recovery still uses the shared route budgets. Replanning to the same
next waypoint does not erase the accumulated lack of progress; small oscillations cannot count as
sustained advance. Hold uses its own internal
waypoints, a retry interval and the existing global route allowance; it never replans every tick.
A failed static route sleeps until building obstacles change (the terrain is static). Its recorded
failed-obstacle snapshot prevents an unreachable anchor from consuming route budget forever;
unavailable dynamic space is handled locally and does not modify the anchor. A newly invalid
recovery segment is discarded and retried within that same allowance. Recovery ends by stopping
inside the arrival tolerance, without snapping coordinates or promising impossible exact arrival.

Executable acceptance belongs to [movement tests](../../tests/integration/test_movement.py),
[large-force regressions](../../tests/integration/test_large_force_behavior_regressions.py), and
[save/load tests](../../tests/integration/test_persistence.py). Compatible resting-destination
recovery additionally belongs to [return behavior tests](../../tests/integration/test_hold_recovery_capacity.py)
and [return continuation/replay tests](../../tests/integration/test_hold_recovery_continuation.py):
100/400/1,000 bodies, multiple directions and displaced members, mixed profiles, Hold and arrived
defense, repeated/interrupted strong pressure, independent regions, every-tick half-speed/swept
clearance, all-member original-anchor arrival and subsequent every-tick stability. Complete saves
continue both opening and common return with identical exported state; normal-command replay is
separate from explicitly prepared legal displacement. Independent
[geometry tests](../../tests/unit/test_collision_recovery.py) cover singular projection rows,
coupled ray certification and the shared total displacement cap. Hold fixes an anchor, not the
body's coordinates at every tick.

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
Factory and ambient spawn candidates check actual disc capacity against local authoritative units,
including adjacent occupancy cells and mixed radii. Empty cells alone do not establish clearance;
physically compatible mobile discs may share a cell. A blocked paid factory item retains its progress
and waits with `SPAWN_BLOCKED`. Ambient spawning searches the remaining seeded candidate order and
defers when capacity is unavailable. The bundled starting map has legal disc clearance; custom map
cell validation alone is not a physical separation guarantee, so legacy initial mobile overlaps
use the same bounded recovery contract rather than being frozen or silently scattered.
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
Saves use v11 without traffic reservations. Anchors, internal recovery waypoints/retry tick,
contact quiet timers, failed static-route obstacle snapshots and established-defense arrival state
are authoritative: losing them can change later movement or consume different route budgets.
The first weak-return request tick and its released phase also affect subsequent coordination and
are saved with existing movement progress, ownership, combat, counters and events. A pending Hold
return can own progress toward its original anchor without an ordinary player path; its best
distance, no-progress count and route ticks survive load. Spatial buckets, numeric scratch buffers,
contact graphs, release geometry, derived leaders, per-tick work diagnostics and shared navigation
fields rebuild from authoritative positions and intent. Presentation history is not saved. Mobile
units may share occupancy cells, while buildings remain exclusive obstacles.

v11 requires both recovery request fields explicitly. A request tick is null or a non-boolean
integer from zero through the saved tick; the released flag is boolean and cannot be true without
a request. After restoring assignments, the loader requires a movable Hold owner with its original
anchor, or an arrived `DEFENDING` unit with a valid unpaused defense assignment and station.
Released members may already be within arrival tolerance while their local companions still return.
Pathless Hold progress must match its saved anchor and carry a request; ordinary movement progress
still matches the next path waypoint. Malformed owner/progress state is rejected rather than
normalized into a different intent.

The loader supports v7 through v10 saves. v7/v8/v9 holders use their recorded body position as the
migration anchor; those files never recorded the original anchor, so it cannot be recovered.
Their contact recovery state starts fresh. v10 preserves recorded anchors, contact recovery and
defense arrival state. All four older versions initialize the new request tick/released phase to
null/false; their progress retains the previous path semantics. v7/v8 traffic reservations are
validated and discarded and their obsolete congestion/progress state is reset. Saving again writes
v11; other older save schemas remain unsupported. Migration continues under the corrected behavior
and does not promise the previous implementation's future trajectories.

Replays use v10 and explicitly reject earlier schemas, including published v9: their expected
trajectories/events belong to the previous movement/spawn behavior. There is no automatic replay
migration. New replay verification still compares snapshot and ordered events; save continuation
tests additionally compare complete exported authoritative state.

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
