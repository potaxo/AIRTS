# Runtime contract

[Design](../design.md) owns scope; [performance](performance.md) owns rendering and workload gates.
This document owns world input, command authority, economy, construction and interaction.
Exact schemas/profiles live in their code owners.

## World and spatial input

- Static weighted grid; grass, road, forest, water, rock, and bridge terrain. Entity definitions:
  [units/catalog.py](../../src/airts/world/units/catalog.py); footprints and occupancy:
  [map.py](../../src/airts/world/map.py). Buildings are hard obstacles;
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
Move, stop, hold, attack, spatial edits, and automation actions share the same validation rules.
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

## Related contracts

- [Movement](movement.md): orders, routing, patrol/defense, combat, contact, Hold and repair.
- [Persistence](persistence.md): save reliability, replay diagnostics and compatibility.
- [Opponents](opponents.md): opening slots, AI policy, elimination and team results.

## Economy, production, and construction

In the default skirmish, resource generators are replaced by resource extractors. Their historical
`resource_generator` map/command/save tag is retained (`RESOURCE_EXTRACTOR` is an enum alias).
Maps with `resource_sites` restrict extractor placement to an exact site's grid origin through
the shared construction validator. One real building, including unfinished construction, occupies a site; a
destroyed extractor frees it for either side. Income is unlimited and equal across sites.
The original example and custom maps without sites retain sandbox placement and income behavior.
[skirmish.py](../../src/airts/world/skirmish.py) owns the new fixed map geometry.

Each owner has an integer resource balance. Generators add periodic income even without an
automation; [economy.py](../../src/airts/systems/economy.py) owns the interval and sandbox extractor
override, while [units/catalog.py](../../src/airts/world/units/catalog.py) owns profile income and prices.
Matter Synthesizers can be built on ordinary valid terrain without a resource site. Their high
price and low output make them a supplementary economy when sites are exhausted: six times the
extractor price for one fifth of its match income, with a four-minute payback after completion.
Only completed income buildings earn resources. There is no resource-production action or economy hotkey
in the UI. The historical economy command remains a balance-target tracker for existing callers
and supported saves; it does not enable or accelerate income.
Configuration and seed govern reproducible enemy spawning. Direct `Simulation` construction
disables ambient spawning by default; [README](../../README.md#run) owns CLI defaults and controls.

Factories produce scouts, light/heavy tanks, and builders; completed command centers produce
only builders through the same paid production path. The existing `factory_id` field identifies
either production source, preserving command and parameter tags;
[simulation.py](../../src/airts/simulation.py) owns default build timing and construction progress.
Finite requests run FIFO; finite work preempts the continuous loop, which resumes afterward.
`ReduceProductionCommand` removes one unfinished unit of the requested kind from the newest
matching finite request, including saved batch sequences. It preserves other items and their
progress, cancels an exhausted request, and advances the source queue immediately. Canceling
the current paid unit discards its progress without refund; continuous loops are unaffected.
A newer loop replaces the previous unfinished loop without discarding finite requests.
No spawn space/resources means waiting; source destruction fails the job. Entity IDs remain unique.
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

Builders construct factories, repair hubs, extractors, and Matter Synthesizers, not command centers. Entity profiles
own costs, build speeds, and build ranges. Placement validates capability, ownership, bounds,
terrain and real building footprints. Remote orders are owner-only blueprints: they do not occupy
space, reserve resource sites, block movement or become combat targets. Blueprints may overlap.
Builders travel even without funds, then wait in range for money or mobile occupants to leave.
Placement is revalidated on arrival; a blueprint covered by a real building fails and the queue
continues. Only when work starts does a job charge once and publish an unfinished building into
authoritative occupancy. Each assigned builder contributes
only within its build range of the footprint and outside it. Builders route to the perimeter first.
Multiple builders accelerate one job without duplicating cost or the building. Unfinished
buildings are visible and attackable, block movement, and cannot produce income/units or repair.
Their health grows with work while retaining accumulated damage. Destruction fails the associated
job and releases builders to the next order. Canceling work or losing every builder leaves the
physical scaffold; reissuing construction at an unclaimed friendly scaffold resumes progress
without another charge. Such scaffolds count as surviving assets but not productive factories.

Shift-placement appends FIFO jobs and keeps placement active. Waiting blueprints reserve neither
space, resources nor builders; activation claims the surviving builders. A repair interruption
retains the construction assignment for return. Ordinary placement
replaces pending jobs for those builders only after validation, including control claims, succeeds.
Rejected placement preserves accepted queues. Right-click closes placement without issuing movement or
altering accepted work. An unstarted occupied site waits with `SITE_OCCUPIED`; commit occupancy before
publishing the scaffold. Queue, progress, damage, resource, and assignment state survive supported saves.

## Interaction

Window/input composition remains in the app. Camera state and transforms have an independent owner;
selection/production controls, automation inspection/scroll, settings/help and shared font painting
live in `presentation/ui/`. Panels emit actions that the app submits through existing commands.
Unit details expose relative mass and collision radius alongside existing attributes.

[README controls](../../README.md#controls) owns keys, menu/rail layout and user actions;
[performance](performance.md#fps-and-rendering) owns rendering, interpolation and frame metrics.
Automation panels, linked grouping, inspection and scrolling show only the human owner's work.
Input uses valid actions for the selected kind/count; building hit tests use visible footprints,
and friendly hits take precedence over generous enemy click areas. Accepted actions use commands.

Camera motion uses presentation elapsed time and remains available while paused; focus loss stops
held motion, naming/modals suppress it, and pan/zoom stay within the map (small maps center).
Pause is local presentation state. Loading clears presentation selection/inspection even when
the adapter restores domain selections. Window close stops before another tick/draw; normal and
exceptional exits release GPU objects, events, fonts, display and Pygame state.

## Query lifetime

Unfinished-automation indexes rebuild on load and update at lifecycle changes; panels, tick work,
formation coordination and factory queues avoid scanning terminal history. History remains inspectable.
Patrol/defense queries share per-owner snapshots and per-target indexes only within an automation
pass. Production membership growth and construction invalidate them; they never cross movement/
combat phases. Visibility unions reuse unchanged source groups, refresh observation ticks, and
invalidate on owner/position/radius or membership changes.
