# ADR 0006: Retain stable formation and traffic identities incrementally

**Status:** Accepted

## Context

Shared-target defenses correctly produced unique collision-safe stations, but every factory spawn
could globally rematch all existing defenders. Established units repeatedly turned toward new
stations instead of continuing their routes. At the same time, large-force traffic discarded its
whole reservation cache when membership changed or a defender docked. Four continuous factories
therefore combined station churn, repeated lattice reconstruction, visible shaking, severe p99
work, and occasional stale-slot movement toward a building.

The required behavior is stronger than eventual uniqueness: an unchanged entity should keep its
valid station and traffic identity while units join, leave, or finish docking. Compact formations
must still contract after removals, and topology changes must still invalidate unsafe derived state.

## Decision

Defense geometry remains a deterministic, center-first, collision-safe slot sequence. Whenever
membership changes, AIRTS retains every live entity's previous unique station that is still in the
new slot set. Only new entities and entities whose old station fell outside a contracted slot prefix
are deterministically assigned to the remaining slots. Same-owner defend automations with the same
target perform this retention over their combined membership, including reinforcements from
independent factories. The resulting station map and deployment slots remain inspectable and
serialized.

Large-force reservation state is incremental while lattice spacing is unchanged. Surviving traffic
members keep their source slots, departed members are removed, and only newly admitted members are
placed in nearest safe free slots. Duplicate retained slots are an invariant failure. Docking at a
valid defend station makes that unit an exact anchor but does not flush unrelated traffic state.
Navigation or building-topology invalidation still clears the complete cache, and an unsafe
candidate crossing terrain or static occupancy invalidates it before movement can enter the cell.

The controller reserves a deterministic slice of a very large open force per tick and physically
updates only planned queue members or bodies still traveling to a retained reservation. Logical
ownership still advances only after physical arrival, displacement remains capped by
`speed * TICK_SECONDS`, hostile and held units remain exact anchors, and ordinary same-owner idle
units may yield. The traffic cache is derived runtime state: save/load and replay reconstruct it
deterministically rather than serializing it.

This decision supersedes ADR 0003's blanket invalidation on docking or any membership change, and
any implication that a shared defense may be wholly reassigned when its membership changes. ADR
0003's deterministic CPU-resident saturation controller, collision limits, topology rules, and
bridge-completeness contract remain in force.

## Consequences

Continuous production no longer reverses established defenders or performs a global assignment on
every spawn. Casualty removal reconciles all matching defenses globally while retaining every valid
survivor station. Incremental cache maintenance avoids repeated work and preserves visible
identity. Contraction still moves outer survivors into the compact new prefix, so stability is not
allowed to keep an oversized formation forever.

The retained assignment is intentionally not a fresh global minimum-distance matching. A newcomer
may receive a farther free station than it would under a complete rematch; stable motion and bounded
work take precedence. Incremental derived state also requires explicit invalidation at topology and
unsafe-candidate boundaries, which is enforced as a fail-fast invariant rather than concealed by an
occupancy exception.

Focused acceptance evidence includes:

* `test_continuous_factory_reinforcements_preserve_existing_defend_stations` and
  `test_large_force_never_moves_an_idle_friendly_through_a_factory` in
  `tests/integration/test_large_force_behavior_regressions.py`;
* `test_four_continuous_factories_defend_without_stutter_or_deep_overlap` in
  `tests/performance/test_crowd_congestion_performance.py`;
* the 1,000-unit gathering expansion and contraction contracts in
  `tests/performance/test_large_army_performance.py`; and
* the unchanged identity, speed-cap, overlap, 400-unit bridge, replay-determinism, and Real FPS
  contracts.
