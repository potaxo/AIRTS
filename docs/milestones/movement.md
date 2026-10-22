# Movement and combat

[Design](../design.md) owns product priorities; [runtime](runtime.md) owns command authority and
economy. This document owns movement, combat, repair and contact behavior. Workload sizes,
timing gates and work-counter definitions belong to [performance](performance.md).

## Orders and routing

One movement controller serves every army size. Direct commands and patrol/defend dispatch share
group-path planning. Ordinary Move always gathers into separated compact arrival slots and reuses
weighted group routing, independently of click count, distance, zoom and army size. Members split
near the arrival area. There is no double-click movement mode or distance threshold.

`SetFormationCommand` records selected mobile units' relative offsets and cohort identity. Future
direct Move commands, including FIFO legs, translate that saved layout instead of compacting it.
Map-edge placement shifts the whole layout; blocked or compressed final layouts reject the move
with `FORMATION_DESTINATION_BLOCKED`, preserving prior intent. Mixed selections preserve each enabled
cohort; unmarked members gather normally. Joint destinations that overlap across cohorts reject
atomically with `FORMATION_COHORTS_OVERLAP`; enabling one cohort for the selection resolves conflicting
layouts. Disabling or recapturing a cohort preserves existing jobs
and destinations but removes their previous coordination metadata; only future moves use the new mode.

`systems/formations.py` coordinates these direct moves through the existing movement writer. All
active members use the minimum terrain-adjusted step. Clear final approaches steer toward common
translated slots; constrictions keep existing certified paths with bounded longitudinal stretching.
The original offsets survive compression and restore once the cohort has a clear approach. Arrived
members displaced by later arrivals restore their slots through shared-goal routing within the
existing combat-route allowance; admission rotates and failures retain intent with a diagnostic.
A completed strict leg waits for its cohort, including displaced arrived members, before dispatching
its FIFO tail, including repeated identical destinations. Membership and speed caps derive from live
units, so deaths/subset overrides cannot leave a dead leader or speed limit behind. Existing routing
allowances and collision certificates remain authoritative. Automatic combat support cannot replace
an active strict move; in-range opportunistic firing remains available. Explicit attacks, repair,
construction and automation control clear active movement coordination without disabling the setting.
Queued moves use preceding movement goals as their source geometry; a preceding target order
instead validates from the unit's current reachable component. Validation does not allocate per-slot
search fields. Dispatching a queued leg first checks the same cheap direct segment as initial
group planning; obstacles or slower terrain require the weighted field instead. Mobile units
are local colliders, not whole-cell static walls; occupied destinations
may wait. Groups share an approach route before branching.

Queued movement also meters search work by expanded cells. An admitted search completes before
further cold searches wait for the next tick; settled cached routes and direct segments remain
eligible within the shared route allowance. Waiting keeps the complete FIFO and final destinations.
Admission rotates so deferred members make progress.

Actual travel speed is the mobile profile's base speed divided by terrain movement cost.
Grass retains base speed; forest uses two thirds, road four thirds and bridge five quarters.
Each tick samples the terrain under the unit's original center once; entering another terrain
changes its speed allowance on the next tick. Travel, collision yielding and Hold/defense return
share this adjusted allowance, with the existing lower cap for passive movement. Static terrain
clearance remains mandatory; water and rock remain impassable.

Static routing uses four-neighbor weighted reverse Dijkstra fields and a bounded LRU cache keyed
by destinations and building obstacles. Fields retain an indexed-heap frontier and expand until
the requested start is settled. Connectivity validation avoids materializing unused paths.
Gathering caches complete expanding disks, connected reachability and clearance; eviction must
not lose established valid stations. Cache limits bound targets, candidate slots and region references.
An unreachable firing ring remains queued in `WAITING`, with one failure diagnostic until its
target/obstacles change or the order is replaced; eviction/load must not preserve a stale failure.
Combat groups pursuers by target and weapon range for batch connectivity against the same exact
static component labels. Every requester remains eligible; route admission, rotation and failure
diagnostics retain their existing budgets and stable ordering.

The current implementation shares cell centers and preserves compensated path-prefix sums and
cost/index ties. Group branches scan numeric shared coordinates in one compiled pass and materialize
only the retained path, avoiding a full Point/cell expansion before choosing a branch. Normal
navigation retains centerline shortcuts and the physical solver's
certified wall sliding. Sustained stalled travel and Hold return use swept-disc lookahead against
terrain, buildings and map edges, including rounded corners; recovery routes retain cell-center
connectors for offset endpoints where needed. The existing blocked marker retains this clearance
mode through budgeted replanning until actual waypoint progress or completion, rather than losing
it when a replacement waypoint resets the local stall counter. Single-unit gathering retains a
safe allocated slot when the clicked center cannot fit the body. Sixteen-waypoint lookahead
permits terrain no slower than its endpoints
(with grass as the minimum allowance), so forest travel can skip intermediate cell centers while
cheaper routes still reject shortcuts across slower terrain. Embedded building endpoints retain
per-unit exception masks. Front order follows the submitted segment through turns; group priority
alternates by tick, with stable ID ties and a common passing side.
These are current execution details, subject to the [engineering change policy](../architecture/engineering.md#change-policy).

Movement prepares a contiguous snapshot, solves all mobile bodies, then batch-classifies numeric
feedback before submitting world changes in stable body order. The feedback pass computes cell
crossings, route progress/arrival and recovery distances together. Quiet bodies omit Python
writeback only after collision participation; old pressure, route/recovery intent, solver status
or actual displacement always require submission. Pressure clearing and quiet timers remain live.
Obstacle snapshots are shared by identity in batched lookahead; only embedded endpoint exceptions
need a distinct mask, avoiding whole-building-set comparisons per routed unit.

Engineering references include Emerson's
[Supreme Commander 2 flow-field tiles](https://www.gameaipro.com/GameAIPro/GameAIPro_Chapter23_Crowd_Pathfinding_and_Steering_Using_Flow_Field_Tiles.pdf)
for shared route work and [Recoil's movement stages](https://github.com/beyond-all-reason/RecoilEngine/blob/master/rts/Sim/MoveTypes/GroundMoveType.cpp)
for separating calculation from state submission. These are design references, not replacement
gameplay contracts or hardware-performance evidence for AIRTS.

## Patrol, defense and combat

- Armed patrollers interrupt for the nearest hostile in their own vision, retain pursuit, then
  resume the interrupted leg after destruction or departure beyond the patrol margin. That margin
  is measured outside the point disk, polyline or filled polygon, not from the moving unit.
- Line and area patrol members advance independently on entering their current waypoint's
  arrival disk, without waiting for a fixed station or the rest of the army. The ordinary disk
  radius is two cells; short legs use 35% of adjacent nonzero leg lengths, with a minimum of
  0.75 cells, the group's maximum body diameter plus 0.12 cells, or
  `1.5 * maximum_body_radius * sqrt(member_count)`, whichever is largest. Large armies can
  pass near a waypoint as a formation instead of feeding every member through a small disk;
  their ordinary arrival radius expands to this minimum when it exceeds two cells.
  When the active traveling group's center enters that disk, nearby followers within twice
  the radius may also continue their dispatched leg. Distant stragglers still approach it.
  This prevents front ranks circulating in place from blocking the tail outside a small patrol
  region. Combat/repair interruptions are excluded from the traveling center.
  Dense generated routes therefore do not recreate a sub-unit arrival bottleneck.
  A waypoint may lie on terrain or a building when its disk has a reachable, body-clear approach.
  Validation requires every member to reach each disk; it does not require reaching the center.
  Members share bounded cached entry points and multi-goal navigation fields, using the group's
  maximum unit radius. Patrol has no per-member stationary slot/capacity requirement. Static
  obstacle changes invalidate approach caches; mobile occupancy remains collision work.
  Route admission rotates within the existing budgets, including small regions.
- Builders patrol to repair the nearest damaged, completed owned building within their vision
  and patrol margin, then resume after full repair or removal. Ownership remains active throughout
  either interruption; pause, cancel and manual replacement stop it through the command boundary.
- Line defenses cover their full frontage. When one rank cannot safely fit the army, they add
  parallel ranks, extending short lines as needed while preserving the line's direction. Partial
  ranks also span the frontage; neither ordinary nor gathering-marked line defenses become a
  central or endpoint disk. Safe existing stations survive shared membership changes where possible,
  with unit-width aisles between ranks and between columns in expanded formations. Casualties
  retain the shared immutable slot geometry instead of rebuilding the whole surviving formation,
  and factory reinforcements use the same allocator. Ordinary area defenses cover the filled region
  with spatially distributed stations. Explicit gathering for points/areas keeps its compact layout.
  Factory creation and retargeting validate future units with the same line geometry and body
  clearance as actual deployment. An obstructed midpoint can use a clear parallel rank; a
  passable midpoint still needs enough space for the produced unit's body.
  Area defenses expand small gathering areas into hexagonal slots with a unit-width aisle.
  Same-owner defenses sharing a target
  retain unique stations across membership changes. Ordinary defenses may expand unsafe deployment
  slots without changing their gathering flag; those stations survive saving.
- Ordinary patrol/defense dispatch admits 16 routes per controller per tick; gathering defenses
  may use the global 32-route automation allowance. Every route consumes a global claim. Controllers
  rotate by tick; defense admits front ranks first. Combat visits all members even after exhaustion.
- Opportunistic firing preserves locomotion, including travel outside an assigned defense area.
  Human and AI direct movement keeps control until arrival, including deferred route admission and
  FIFO transitions: firing never stops or redirects travel to chase or assist. Explicit attack
  pursuit stops at weapon range. Ordered
  targets persist until destruction or replacement. Pursuit may fire at enemies encountered along
  its route without replacing that target or advancing the FIFO; the ordered target takes priority
  whenever it is in range. Units awaiting bounded attack
  dispatch cannot acquire an incidental target or join combat support before activation.
  A stale firing destination can refresh after one second of route travel, within the combat budget.
- Destroyed explicit attack targets turn surviving mobile attackers' orders into gathering at the
  target's last known position. Patrol/defense encounters instead resume their existing assignment.
- Actual firing recruits nearby allied units whose profiles explicitly enable combat support.
  Builders and auxiliary profiles default to no support. Hold, paused work and emergency repair
  retain control. Recruitment is limited to idle units with no pending direct work, or active
  patrol/defense assignments within their pursuit boundaries. Direct movement, attack, repair,
  reclamation and pending FIFO work cannot be replaced by support, regardless of control source
  or elapsed time. Recruitment takes effect
  after the firing pass, so it cannot cascade into additional shots in that same pass.
- Successful player unit-control orders suppress support recruitment for five seconds of simulation
  time on the affected units. Further orders, including queued waypoints, restart the window;
  selection, rejected commands and internal AI/automation commands do not. Player pause or retarget
  of an assignment also cancels its existing automatic pursuit. Ordinary in-range firing and explicit
  attack orders remain available. Hold excludes recruitment until another order replaces
  it, even after the micro window expires. Pausing does not consume the window; saves retain its age.
- Direct orders execute FIFO per unit across movement, attack and builder repair, preserving
  kind, destination, target identity and shared movement goal across saves. A movement leg must
  finish before the following job starts; explicit attacks finish when their target disappears,
  repairs when the building is fully healed. Appending does not interrupt current direct work.
  Dispatch rotates within a bounded order allowance. Movement claims the shared routing allowance
  before background automations; activating attack/repair intent leaves routing capacity for those
  systems' actual paths instead of charging twice. Queued marches share their group goal's weighted
  approach and branch near their compact destinations. Movement route failures wait with a
  diagnostic and retain the pending destination. Removed, fully repaired or otherwise invalid
  queued targets are skipped with an `order_skipped` diagnostic, preserving the FIFO tail.
  After an attack with pending orders, units continue that queue instead of inserting an extra
  gathering move at the destroyed target. Existing target gathering remains for attacks without
  subsequent orders. An ordinary replacement or Stop/Hold clears every pending order kind.
- In the current combat pass, route admission rotates with the tick and firing uses stable entity
  order. Same-target/range firing rings are shared only within that pass. Projectile impacts remove
  casualties immediately in projectile-ID order; references, affected defense stations and builder
  queue restarts are coordinated before the phase ends. Direct removal completes before returning.
  No partial removal batch enters a save. Dead attack-memory references are cleared as well.
- Firing-ring generation filters terrain, the target footprint and exact radial boundaries in a
  compiled batch, retaining row-major candidate order and the existing interaction-point fallback.
  Dynamic building clearance remains the route planner's responsibility. Kernels warm before
  gameplay so the first pursuit does not pay compilation inside its command/tick.
- Ordinary projectiles track their target, retain their last destination after target removal,
  remain visible until arrival, and cannot damage bystanders. Scout, light/heavy tank and turret
  projectile speeds are doubled from their original profiles; damage and cadence are unchanged.
- Both light and heavy artillery estimate a ground impact from the target's actual displacement
  in the current movement tick. A constant-velocity intercept accounts for shell travel time;
  no observed movement aims at the current center. The estimate stays inside map bounds and
  does not inspect future orders or paths, so stopping, turns and obstacles can defeat the prediction.
  Prediction runs only on an actual shot and reuses movement scratch; it adds no target scan,
  pathfinding or future world simulation. A faster, non-interceptable target receives a lead
  estimate based on one nominal flight time. Shells lock that ground point at launch and never track. Arrival
  always explodes there, including after source/target removal or an empty impact. Within a three-cell
  radius, hostile units and buildings take `ceil(40 * (1 - distance / 3))` damage, measured between
  their current selection center and the impact. The boundary and beyond take zero; allies are spared.
  Victims resolve in stable entity order using shared damage attribution and casualty cleanup.
  Artillery travels at 10 cells/second, half the ordinary light tank shell's 20 cells/second;
  Light artillery uses the same fixed-impact falloff with damage 20, radius 1.5, speed 14 and
  a 1.6-second firing interval; its range is 9 and movement speed 3. Tank destroyers use ordinary
  tracking, single-target shots: damage 45, range 9, speed 26 and a 2.5-second firing interval,
  with 85 health and movement speed 3. They have no hidden armor bonus or splash damage.
  Other ordinary weapon speeds still differ by kind. Projectile and trace definitions belong to world
  entity owners. [Appearance](../architecture/appearance.md#shared-style) owns visual feedback.

## Contact, yielding and Hold anchors

Idle/Stop units may yield passively without gaining an order or returning to their old position.
Moving units keep their orders. All mobile bodies, including enemies and pathless units, participate.
Buildings, impassable terrain and map edges are hard constraints. No body may tunnel, exchange
identity, teleport, or exceed one total per-tick displacement cap. Passive yielding/recovery uses
the existing lower allowance; it cannot add a second capped step to active travel. Safe waiting is
valid when capacity is unavailable. Every actual displacement updates occupancy before combat.

Mobile profiles have a positive relative mass, independent of collision radius, speed and display
size. Contact prediction retains travel/passive intent priority but divides each body's correction
share by its mass. Lighter bodies therefore take more correction in otherwise comparable contact.
Unequal-mass opposing travel also participates in correction prediction; equal-mass separated
travel retains direct certification. If walls or a speed circle prevent the lighter body from taking
its allocated correction, one bounded capacity pass redistributes the unfulfilled share. The extra
pair evaluation contributes to contact counters. Mass applies to actual travel contact, while
voluntary lane-opening and weak return remain geometric avoidance rather than impact impulses.
Mass does not increase speed allowances, override walls or create collision damage.
Compressed-component release and joint anchor return remain geometric
safety/recovery solves, rather than impact impulses. [Mass contact tests](../../tests/integration/test_mass_contact.py)
cover relative yielding, Hold ownership, swept clearance and continuation through saves/replay.

Hold fixes the current position as an authoritative anchor. Yielding preserves that anchor, control
ownership and absence of enemy pursuit. Reissuing Hold replaces the anchor; accepted Stop/Move or
other control replacements end recovery, while rejected commands preserve it. Defense station
identities remain intact during yielding and recovery. These intents share the same physical
controller rather than separate competing forces.

The current solver predicts travel/contact corrections, then certifies swept discs against static
supporting planes and relative-motion pair constraints. Two contact sweeps retain overlap discovery
and actual travel/opening predictions. Original-position buckets cover full displacement envelopes.
Existing overlap must not deepen: swept distance is at least
`min(initial_distance, radius_i + radius_j)`. Prediction failure never permits penetration.
Unchanged resting bodies need no displacement certificate; approaching bodies still certify
against them and wake passive yielding.

### Coordinated release and return

Compressed contact components and legal components with sustained stalled travel or compatible
return demand may receive a joint feasible release. Conflicting resting destinations stay waiting
intents rather than evicting occupants. Stable identity directions separate coincident centers
without reversing distinct-center separation. Affine expansion/finite corridor directions respect
static planes, each body's speed circle and internal/external pair clearance. Internal corners
covered by adjacent blocked cells cannot fabricate barriers along a continuous wall.

Recovery admission extends contacts through the bounded passive step envelope; a finite queue
visits each admitted body once and includes separated fringe discs that could consume capacity.
Active prediction retains contact neighborhoods; passive yielding inspects the needed displacement
envelope. External discs constrain release and join coordination upon contact. Passive bodies use
the coherent release without applying pairwise prediction twice; active travel keeps its preference.

Compatible Hold anchors and arrived defense stations form destination regions. First departure
beyond arrival tolerance records a request tick. The oldest request opens its return corridor,
with stable ID ties and the passive half-speed cap, while unrelated regions act independently.
Geometric promotion must not change business masks into strong travel or create a player command.
Followers whose complete original weak step is independently clear may return without waiting;
steps needing another body's displacement wait for coordination.

For a legal region with one weak opener and no strong pressure, finite two-sided/one-sided transverse
fields open a lane without transporting the formation; the leader receives no transverse seed.
After arrival, its released phase persists until the compatible region settles. Sparse active-set
projection forms the joint anchor gradient; dependent displacement rays retain a common scale and
normal compensation. Blend, release and return share one speed circle and full static/internal/
external certificates. Scaling cannot increase anchor error relative to the release seed, but
opening need not reduce it every tick. Weak gradients discarded by joint return are not predicted
again. Projection may omit pairs outside both passive envelopes with numerical margin; final
certification must still cover complete envelopes, including strong traffic's full-speed motion.
Operation accounting and limits are owned by [performance](performance.md#contact-solver-measurement).

During opening in a legal region with direct compatible return targets and no nearby strong traffic,
the solver may probe the same certified joint anchor gradient before the opener arrives. It adopts
the entire certified trial only when every affected opening leader improves over the existing seed
and makes at least half its trial displacement allowance of anchor progress (or finishes a closer
anchor). Microscopic improvements retain opening coordination. Acceptance ends only the derived
opening promotion, not its ticket: request ages persist and actual arrival releases the request.
Other followers' incidental motion cannot pin the oldest request indefinitely. All speed/static/
swept constraints and numerical operation accounting remain active.

No certified advance or sustained region-wide stall opens another pending request. Unfulfilled
requests retain age/progress; a fulfilled body displaced again starts a new request and baseline.
Strong external pressure invalidates released phases without losing weak intent. Conflicting goals
remain unchanged and wait. Graphs, directions and leaders rebuild; request age/phase are authoritative.

### Settling and progress

Hold uses an arrival dead zone and contact quiet interval. Incoming pressure, including successful
lateral yielding, suppresses return and refreshes that interval; recovery resumes at low speed
afterward. Arrived defenders retain pending recovery during quiet waiting. Internal displacement
does not acquire control or emit player movement-started/completed events. Completing ordinary Move
requires reaching its destination, permits later yielding, and does not create a Hold anchor.

Replanning the same waypoint cannot erase a stall or count oscillation as progress. Hold uses
internal waypoints, retry intervals and the shared route allowance, not per-tick replanning.
Failed static routes sleep until building obstacles change; dynamic occupants do not rewrite the
anchor. Invalid recovery segments retry within budget. Recovery settles inside tolerance without
coordinate snapping. With reachable capacity, overlap scenarios require penetration reduction,
selected-unit extraction/completion, legal spacing and eventual settling. Missing certified capacity
may leave a blocked component; this is not proof that free space is physically absent.

## Repair

Repair-and-return selects health strictly below the command threshold, prefers hubs,
then resumes suspended work or returns to the prior position. AI and the manual repair shortcut
submit the same single group command. Enrollment filters healthy units first, snapshots completed
owned repair sources once, and selects by source priority, straight-line center distance and ID.
Service areas and static connectivity are reused across the command; unreachable sources are
skipped. Enrollment constructs no complete movement paths. Actual travel and free service slots
are resolved later within the existing per-controller/global route allowances. A full facility can
wait or trigger reselection. Reselection compares reachable route costs within a priority tier,
pruning candidates with a conservative road-cost lower bound; it never routes lower-priority
factories after a usable hub is found. An already selected source uses direct ID lookup.
Patrol/defense assignments can opt
into automatic repair; disabled assignments never retreat solely because of low health. Enabled
assignments check once per simulated second using the UI repair threshold and shared automation
route allowance, submitting emergency repair through the same validated command boundary.
Only currently controlled members qualify; paused assignments and members already repairing are
excluded. Full health resumes the suspended assignment. Disabling the option stops new retreats
without canceling repairs already underway. Unavailable facilities reject the individual repair
with the ordinary diagnostic; other members continue their work and later checks retry.
API threshold defaults and rates/ranges live in code; UI switches and the shortcut belong to
[README](../../README.md#controls).
Completed hubs heal one other owned mobile unit or building at a time within their center-based radius,
retaining it until full health, departure, reclamation or destruction, then choosing the nearest wounded target
with stable ID ties. Builders qualify; repair-and-return cannot bypass range or concurrent capacity.
An unfinished building receives construction work through the shared
[construction service](../../src/airts/systems/construction.py), then ordinary healing after completion.
Blueprints are not entities and cannot be repaired. Buildings targeted for reclamation are excluded
from automatic hub service. Unfinished hubs cannot supply service.
Travel stops as soon as a unit is in service range with disc clearance, even while its old path
has remaining waypoints. Overlapping arrivals choose another clear slot; traveling repair destinations
avoid both present occupants and other incoming destinations. Static area goals share a routing field;
short terrain-certified segments connect the chosen local slot without per-unit full-map fields.
When a source has no reachable slot, another eligible source is tried; total lack of capacity waits
and retries within the existing global route allowance. A pushed unit outside range resumes travel.
[repair_positions.py](../../src/airts/systems/repair_positions.py) owns service geometry and capacity.
Factory/command-center destinations retain explicit service. Direct builder repair routes to clear
cells within its footprint-based working range of a damaged or unfinished owned building, without
requiring an adjacent cell; replacement/automation assignment
cancels it, and queued construction waits. Repairs cost no resources.

Armed completed buildings use the shared projectile combat pass but never pursue or move. Unfinished
turrets cannot fire. Weapon cadence is an immutable profile interval in fixed ticks. Fixed turrets
fire every 0.6 seconds; ordinary scout/tank weapons retain one second. Heavy artillery has longer
range, slower travel and a 2.5-second firing interval.
Its fixed-impact splash rules are defined above.

## Verification owners

- [Combat](../../tests/integration/test_economy_combat.py) and [artillery](../../tests/integration/test_artillery_combat.py): ordinary projectile speed, fixed impacts, splash falloff, source/target removal, casualties and save/replay continuation.
- [Artillery presentation](../../tests/integration/test_artillery_presentation.py): matching software/GPU shell arcs, size, blast radius, animation and expiry without domain mutation.
- [Movement](../../tests/integration/test_movement.py), [large-force behavior](../../tests/integration/test_large_force_behavior_regressions.py), and [overlap recovery](../../tests/integration/test_overlap_recovery.py): orders, speed, clearance, identity, extraction and progress.
- [Hold/defense return](../../tests/integration/test_hold_recovery_capacity.py) and [continuation](../../tests/integration/test_hold_recovery_continuation.py): interrupted pressure, independent regions, original anchors, every-tick safety and settling.
- [Projection geometry](../../tests/unit/test_collision_recovery.py): singular rows, coupled rays and total displacement caps.
- [Persistence](../../tests/integration/test_persistence.py): authoritative restoration under [save policy](persistence.md#save-reliability-and-replay-policy).
