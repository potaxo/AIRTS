# Movement and combat

[Design](../design.md) owns product priorities; [runtime](runtime.md) owns command authority and
economy. This document owns movement, combat, repair and contact behavior. Workload sizes,
timing gates and work-counter definitions belong to [performance](performance.md).

## Orders and routing

One movement controller serves every army size. Direct commands and patrol/defend dispatch share
group-path planning. Move preserves compatible translated formation positions, fitting them
inside map edges; terrain, buildings or compressed source geometry instead require reachable,
separated destination slots. Explicit `gathering` allocates slots around the target. Missing flags
in supported histories use formation preservation. Mobile units are local colliders, not whole-cell
static walls; occupied destinations may wait. Groups share an approach route before branching.

Static routing uses four-neighbor weighted reverse Dijkstra fields and a bounded LRU cache keyed
by destinations and building obstacles. Fields retain an indexed-heap frontier and expand until
the requested start is settled. Connectivity validation avoids materializing unused paths.
Gathering caches complete expanding disks, connected reachability and clearance; eviction must
not lose established valid stations. Cache limits bound targets, candidate slots and region references.
An unreachable firing ring remains queued in `WAITING`, with one failure diagnostic until its
target/obstacles change or the order is replaced; eviction/load must not preserve a stale failure.

The current implementation shares cell centers, computes segment cost once, preserves compensated
path-prefix sums and cost/index ties, and batches sixteen-waypoint lookahead with farthest-first
terrain/corner checks. Embedded building endpoints retain per-unit exception masks. Front order
follows the submitted segment through turns; group priority alternates by tick, with stable ID ties
and a common passing side. Stalls trigger budgeted replanning without clearing their progress history.
These are current execution details, subject to the [engineering change policy](../architecture/engineering.md#change-policy).

## Patrol, defense and combat

- Armed patrollers interrupt for the nearest hostile in their own vision, retain pursuit, then
  resume the interrupted leg after destruction or departure beyond the patrol margin. That margin
  is measured outside the point disk, polyline or filled polygon, not from the moving unit.
- Builders patrol to repair the nearest damaged, completed owned building within their vision
  and patrol margin, then resume after full repair or removal. Ownership remains active throughout
  either interruption; pause, cancel and manual replacement stop it through the command boundary.
- Line defenses spread stations along the line; area defenses separate stations, expanding small
  gathering areas into hexagonal slots with a unit-width aisle. Same-owner defenses sharing a target
  retain unique stations across membership changes. Ordinary defenses may expand unsafe deployment
  slots without changing their gathering flag; those stations survive saving.
- Ordinary patrol/defense dispatch admits 16 routes per controller per tick; gathering defenses
  may use the global 32-route automation allowance. Every route consumes a global claim. Controllers
  rotate by tick; defense admits front ranks first. Combat visits all members even after exhaustion.
- Opportunistic firing preserves locomotion, including travel outside an assigned defense area.
  Actual pursuit can interrupt travel and explicit attack pursuit stops at weapon range. Ordered
  targets persist until destruction or replacement; a stale firing destination can refresh after
  one second of route travel, within the combat budget. Mixed selections attack with armed members.
- In the current combat pass, route admission rotates with the tick and firing uses stable entity
  order. Same-target/range firing rings are shared only within that pass. Projectile impacts remove
  casualties immediately in projectile-ID order; references, affected defense stations and builder
  queue restarts are coordinated before the phase ends. Direct removal completes before returning.
  No partial removal batch enters a save. Dead attack-memory references are cleared as well.
- A projectile retains its last destination after target removal, remains visible until arrival,
  and cannot damage bystanders. Projectile and trace definitions belong to world entity owners.

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

Repair-and-return is explicit: select health strictly below the command threshold, prefer hubs,
then resume suspended work or return to the prior position. Low health alone does not override
automation. API threshold defaults and rates/ranges live in code; the UI shortcut belongs to
[README](../../README.md#controls).
Completed hubs heal one friendly mobile target at a time within their center-based radius,
retaining it until full health, departure or destruction, then choosing the nearest wounded body
with stable ID ties. Builders qualify; repair-and-return cannot bypass range or concurrent capacity.
Travel stops as soon as a unit is in service range with disc clearance, even while its old path
has remaining waypoints. Overlapping arrivals choose another clear slot; traveling repair destinations
avoid both present occupants and other incoming destinations. Static area goals share a routing field;
short terrain-certified segments connect the chosen local slot without per-unit full-map fields.
When a source has no reachable slot, another eligible source is tried; total lack of capacity waits
and retries within the existing global route allowance. A pushed unit outside range resumes travel.
[repair_positions.py](../../src/airts/systems/repair_positions.py) owns service geometry and capacity.
Factory/command-center destinations retain explicit service. Direct builder repair approaches a
damaged completed owned building within footprint-based range; replacement/automation assignment
cancels it, and queued construction waits. Repairs cost no resources.

Armed completed buildings use the shared projectile combat pass but never pursue or move. Unfinished
turrets cannot fire. Weapon cadence is an immutable profile interval in fixed ticks (existing units
retain one second); artillery has longer range, slower travel and a 2.5-second firing interval.
It uses the existing single-target projectile rules.

## Verification owners

- [Movement](../../tests/integration/test_movement.py), [large-force behavior](../../tests/integration/test_large_force_behavior_regressions.py), and [overlap recovery](../../tests/integration/test_overlap_recovery.py): orders, speed, clearance, identity, extraction and progress.
- [Hold/defense return](../../tests/integration/test_hold_recovery_capacity.py) and [continuation](../../tests/integration/test_hold_recovery_continuation.py): interrupted pressure, independent regions, original anchors, every-tick safety and settling.
- [Projection geometry](../../tests/unit/test_collision_recovery.py): singular rows, coupled rays and total displacement caps.
- [Persistence](../../tests/integration/test_persistence.py): authoritative restoration under [save policy](persistence.md#save-reliability-and-replay-policy).
