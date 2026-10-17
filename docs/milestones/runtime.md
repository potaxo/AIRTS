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
Move (including FIFO queued waypoints), stop, hold, attack, spatial edits, and automation actions
share the same validation rules.
Failures identify their operation, subject, and reason; no invalid proposal silently changes state.

A unit has at most one active controlling assignment and may retain a suspended assignment during
explicit repair. Direct control detaches units, records override, and leaves remaining members
working. Creating a new patrol/defend on explicitly selected units replaces their older ordinary
assignment even at lower priority. Background claims use emergency status, priority, and recency.
Replacement also removes selected builders from waiting construction jobs while preserving other
builders' membership and progress. Rejected commands retain prior intent. Queued movement appends
to direct movement; the first queued move replaces another controlling order. New ordinary orders
clear selected units' movement queues.
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
The original example and custom maps without sites retain unrestricted sandbox placement.
[skirmish.py](../../src/airts/world/skirmish.py) owns the new fixed map geometry.

Each owner has one integer resource balance. Completed income buildings contribute to a cached
per-owner income total, settled every ten ticks (one game second), even without an automation.
Construction completion, upgrade completion and removal update that total; ordinary settlement
does not scan entities or maintain per-building timers. Newly completed buildings join the next
global settlement; completion during that tick's automation pass follows that tick's settlement.
Initialization and loading rebuild totals from complete entities. Profile rates are credited per second.
[economy.py](../../src/airts/systems/economy.py) owns income execution;
[units/buildings.py](../../src/airts/world/units/buildings.py) owns profile income and upgrade prices.
Sandbox and match maps use the same production amounts, without a sandbox income override.
Matter Synthesizers can be built on ordinary valid terrain without a resource site. Their high
construction price and lower output make them a supplementary economy when sites are exhausted.
Only completed income buildings earn resources. There is no resource-production action or economy hotkey
in the UI. The historical economy command remains a balance-target tracker for existing callers
and supported saves; it does not enable or accelerate income.
### Entity upgrades

Extractors and Matter Synthesizers start at T1 and upgrade sequentially to T3. Other kinds have
no upgrades configured. Costs use the existing single resource; no second currency is introduced.

| Kind | T1 output / s | T2 output / s | T1 -> T2 cost / time | T3 output / s | T2 -> T3 cost / time |
| --- | --- | --- | --- | --- | --- |
| Extractor | 4 | 8 | 120 / 30s | 16 | 360 / 60s |
| Matter Synthesizer | 2 | 4 | 200 / 45s | 8 | 600 / 90s |

`UpgradeEntityCommand` validates ownership, completion, capability, available resources and idle
upgrade state before charging once. One upgrade can run per entity. Ordinary production continues
at the current tier; completion switches the absolute target profile before that tick's income
settlement without resetting the global income interval. `CancelUpgradeCommand` returns the full paid
cost once and discards progress. Destruction/reclamation removes progress without an upgrade refund.
Stop and ordinary assignments do not cancel upgrades. Paused games advance neither timer.

The reusable `UpgradeState` belongs to each entity; immutable `UpgradeDefinition` transitions and
catalog bindings supply costs, durations and target profiles. The shared
[upgrades.py](../../src/airts/systems/upgrades.py) executes timing and transactions without kind-specific
branches. `Entity.profile` resolves the current tier. Current transitions change only income;
future stat upgrades must route the affected subsystem through the effective profile. Physical
footprint/collision changes are outside this implementation.
Saves retain tier, paid remaining time and the simulation tick; loading neither charges nor pays
again. [Persistence](persistence.md) owns format rejection and replay diagnostic support.
The selection panel shows tier, output, next cost/duration, remaining time and a progress bar.
Same-type selections group by tier and running/unfinished state; each button applies to its group.
Insufficient aggregate resources disable that group's upgrade action. Enemy inspection is read-only.

### Developer income and unit production

Developer mode is a player-only setting submitted through `SetDeveloperModeCommand`, defaulting
to off for new simulations. Economy credits an additional 2,000 resources after each ten enabled
ticks (one game second), independently of building income. A changed toggle resets the partial
interval; repeating the same setting preserves it. Pause and completed matches advance no ticks.
Resource events identify the bonus as `DEVELOPER_MODE_INCOME`.
`SetAIIncomeMultiplierCommand` switches all non-player building income between 1x and 5x,
independently of the player bonus; ownership/schema validation rejects other multipliers and
non-player callers. AI planning uses the effective income. The resource header reports the player's
total income per game second, including the bonus. The developer subpanel's local speed setting
scales elapsed time by 0.5x, 1x, 2x or 4x while the headless core retains fixed 10 Hz steps.
Configuration and seed govern reproducible enemy spawning. Direct `Simulation` construction
disables ambient spawning by default; [README](../../README.md#run) owns CLI defaults and controls.

Factories produce scouts, light/heavy tanks, light/heavy artillery, tank destroyers, and builders; completed command centers produce
only builders through the same paid production path. The existing `factory_id` field identifies
either production source, preserving command and parameter tags. Immutable
[unit profiles](../../src/airts/world/units/catalog.py) own per-unit production ticks: scouts take
2 seconds, builders 2.5, light tanks 4, light artillery 5.5, tank destroyers 6, heavy tanks 6.5
and heavy artillery 8.5 at the fixed 10 Hz tick.
Player and AI sources share these timings. Each mixed-batch item uses its own kind's time,
including after reducing the current item; pause/preemption preserves paid progress.
[simulation.py](../../src/airts/simulation.py) owns construction progress constants.
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
Both paths use the shared spawn-clearance service and the shared entity-ID allocator. Production
controllers retain only the latest 128 produced entity IDs for inspection; events remain the
complete audit trail. The recent-ID list stays bounded during continuous production.
`Produce + Defend` retargets each selected factory's existing loop without changing its unit kind
and attaches only subsequently produced units to a shared production defense. Production sources
with the same owner and exact spatial target join one live controller, with one assembly circle and
one entry under Units for inspection, pause/resume, auto-repair and cancellation. Polygons gather;
polylines spread along the line. Ordinary manually created defenses and other owners stay separate.
Retargeting a factory affects only its future units; already produced units keep their orders.
Editing the shared controller's target or auto-repair updates every linked source. Joining an
existing controller inherits its auto-repair setting; an explicit auto-repair edit changes the group.
Moving a controller onto another existing production defense is rejected; factories can instead
join that controller through Produce + Defend. Canceling the controller detaches future
reinforcements while factories keep producing; pausing it also holds subsequently joined units.
Factories remain independent owners of costs, queues, progress and production pause/cancel.

Construction-capable units construct the kinds in `BUILDABLE_KINDS`; current profiles grant that
capability to builders, not command centers. Entity profiles own costs, build speeds, and build ranges.
Placement validates capability, ownership, bounds, terrain and real building footprints.
Remote orders are owner-only blueprints: they do not occupy
space, reserve resource sites, block movement or become combat targets. Blueprints may overlap.
Builders travel even without funds, then wait in range for money or mobile occupants to leave.
Placement is revalidated on arrival; a blueprint covered by a real building fails and the queue
continues. Only when work starts does a job charge once and publish an unfinished building into
authoritative occupancy. Each assigned builder contributes
only within its build range of the footprint and outside it. Builders stop at clear positions within
that range rather than requiring contact with the perimeter.
Multiple builders accelerate one job without duplicating cost or the building. Unfinished
buildings are visible and attackable, block movement, and cannot produce income/units or repair.
Their health grows with work while retaining accumulated damage. Destruction fails the associated
job and releases builders to the next order. Canceling work or losing every builder leaves the
physical scaffold; reissuing construction at an unclaimed friendly scaffold resumes progress
without another charge. Such scaffolds count as surviving assets but not productive factories.

Shift-placement appends FIFO jobs and keeps placement active. Shift-left-drag creates only a
presentation draft: grid-aligned origins follow the drag direction, spaced by the selected footprint
on its dominant axis. Release submits each site through the shared construction command in start-to-end
order; invalid sites are skipped with a visible count. A zero-length drag is a queued click.
Right-click during the drag cancels the whole unsubmitted line without commands or costs.
[presentation/build_placement.py](../../src/airts/presentation/build_placement.py) owns draft geometry. Waiting blueprints reserve neither
space, resources nor builders; activation claims the surviving builders. A repair interruption
retains the construction assignment for return. Ordinary placement
replaces pending jobs for those builders only after validation, including control claims, succeeds.
Rejected placement preserves accepted queues. Right-click closes placement without issuing movement or
altering accepted work. An unstarted occupied site waits with `SITE_OCCUPIED`; commit occupancy before
publishing the scaffold. Queue, progress, damage, resource, and assignment state survive supported saves.

Reclaim-capable mobile units can reclaim any owned physical building through `ReclaimBuildingCommand`. They approach
within build range, stop, and subtract their profile's reclaim rate from its health each tick.
Multiple builders contribute independently. Only the builder taking the final health point removes
the building through authoritative removal and refunds `construction_cost * 70 // 100` once to its owner.
Damage and scaffold progress do not change this refund because a published scaffold was already paid
in full. Starting buildings priced at zero
refund zero; blueprints have no physical target and cannot be refunded. Concurrent builders cannot
refund twice; destruction by another cause at any point yields no refund. Source/job, occupancy,
selection and target cleanup reuse normal removal. Stop/move/new assignments cancel the builder's reclaim intent.
[reclamation.py](../../src/airts/systems/reclamation.py) owns execution; reclaim target selection is a
Builder action in the right sidebar.

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
Enemy box/double-click selections are presentation-only inspections; domain grounding retains only
owned entities. Player entities take precedence in mixed boxes and combined selections. Hostility
uses team relationships, and enemy inspection never grants command authority.
Double-click expansion is scoped to the clicked entity's owner, kind and canvas bounds.

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
