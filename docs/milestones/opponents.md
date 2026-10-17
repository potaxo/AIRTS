# Skirmish opponents and results

[skirmish.py](../../src/airts/world/skirmish.py) owns opening configuration;
[opponent_ai.py](../../src/airts/systems/opponent_ai.py) owns rule-based decisions;
[systems/skirmish.py](../../src/airts/systems/skirmish.py) owns elimination and results.
[Runtime](runtime.md) owns shared command/economy rules, and [persistence](persistence.md) owns saved memory.

## Skirmish configuration and results

The four-slot opening setup has one human, AI or closed slots, and teams 1-4, with at least two
opposing teams. Immutable `match_slots` stores controllers/teams. Closed slots keep terrain and
resource sites but no assets. In-match alliance changes, gifting and revival are not implemented.
All units/buildings are visible without fog overlay and resource sites are public. Allies cannot
be combat/defense targets; explicit allied attacks are rejected. Physical collision still applies.
Each active slot starts with a command center, factory, extractor, builder, scout and funds;
exact map geometry, funds and income belong to code.

An owner with no completed factory, command center or builder is eliminated but keeps remaining
assets. Unfinished buildings are assets but do not qualify for production viability. An owner
without any surviving unit/building is out; blueprints are not assets. Funds, paused production
and blocked exits do not eliminate a completed source. Losing all builders fails construction.

Results evaluate after a complete tick. Victory requires all other teams to have no surviving
units or buildings, including unfinished buildings. Eliminated owners continue fighting with
their remaining armies. No surviving teams means a draw; multiple teams with only passive assets
and no completed producer, builder or armed entity also draw. A recorded result stops advancement
and offers return to opening configuration.

## Squad and economy behavior

AI keeps a home reserve and persistent gathering/advancing/attacking/retreating/defending squads.
Squads assemble most members at safe forward holdings or planned extractor expansions, select
reachable targets using value, distance, local resistance and other squads' commitments, maintain
pursuit, retreat from unfavorable fights and regroup. Candidate selection mixes valuable and nearby
targets. Small forces can raid exposed assets; destroying a target triggers selection from the
current position instead of a return home. When no favorable target exists, squads stage toward
the enemy outside its local threat area. Small raids use the reserve; larger raids
recall the nearest capable squad. New units reinforce non-full advancing squads instead of creating
single-unit attack waves. Local estimates include up to 96 nearby squad members, excluding retreating
and defending partners; distant reinforcements are not counted as present. When no favorable attack
exists, weaker groups can join a stronger nearby group before advancing. Severely wounded units use
repair-and-return through shared commands.

Staging points sit away from production buildings and behind the anchor when enemies are close.
New AI production uses an off-building rally point. Construction preserves producer exit rings and
resource footprints. `SPAWN_BLOCKED` and `SITE_OCCUPIED` trigger bounded orders to clear idle,
unassigned combat units; repairing/automated units and ongoing pursuit retain their control.

Combat estimates use health, profile firing cadence, damage and range; close decisions add twelve
aggregate exchanges with approach delays. Fixed turrets wait for mobile enemies to approach;
unfinished turrets contribute no armed threat. Forecasts are heuristic and omit collision, individual focus fire, terrain
routing and reinforcement prediction. Knowledge uses the current fully visible world.

Economy avoids enemy-controlled or unreachable sites, replaces lost builders/factories (using
command centers for builders when necessary), restores income before an unaffordable factory,
expands extraction and protects remote extractors with turrets. Factory capacity scales with income;
additional factories and repair facilities use forward holdings. Capacity and a twenty-second cash
forecast read current per-kind production times and prices. Income shortage prioritizes extraction;
when safe extractor locations are exhausted, supplementary Matter Synthesizer plans commit before
the full price is affordable, so military spending cannot prevent the savings goal from starting.
The desired synthesizer count scales with factory count and army investment. Construction remains
limited to two concurrent jobs. No free resources or discounts are granted.

Income forecasts read each building's effective tier profile. Resource upgrades compete with
new income buildings using completion time plus cost per additional income; expansion estimates
include straight-line builder travel and construction time, not a routed arrival prediction.
The AI considers the next configured income-increasing upgrade, without hard-coded tiers or prices,
on completed, healthy, unthreatened sources not targeted for reclamation. One income upgrade runs
at a time; unaffordable chosen upgrades reserve funds through the existing production budget pauses.
Existing unpaid construction and finite production commitments take precedence, and missing builders
or factories are restored before upgrades. Busy builders do not prevent upgrades. Local military
inferiority suspends new upgrade investment; already paid upgrades continue. Additional synthesizer
plans wait while an income upgrade is running. Non-income upgrade benefits are not evaluated.

Production scores heavy tanks, light artillery support, tank destroyers against enemy heavy tanks
and tank destroyers, and heavy artillery against completed turrets. Light tanks remain the main force.
Emergency defense can spend growth reserves and favors faster light tanks over expensive additions.
Queued unpaid items and newly committed orders reduce the budget available to other factories.
Unpaid construction and needed units reserve funds by pausing continuous production; AI pause
ownership survives intervening finite queues until an actual resume. Manual pauses persist.

Owners decide every ten ticks with staggered slots; economy runs every twenty. A decision updates
at most two combat squads of up to 32 units, checks four attack candidates per squad and attempts
three commands. Economy receives command capacity before offensive squads on economy turns;
production sources rotate to avoid starvation. Each building-kind placement search examines at
most 64 candidates. Shared movement/combat budgets apply.
Decisions derive from ticks and authoritative state; ambient enemy generation is disabled in matches.
Saved squad membership, phases, targets, destinations, command/retry ticks and budget pauses are
validated by the persistence adapter. Current save continuation/replay uses the same decision logic.

## Research basis

[AIIDE 2025's official results](https://davechurchill.ca/starcraft/aiide/results/2025/)
rank BananaBrain first: 3,002 wins in 3,408 games (88.09%). Its
[published tournament source](https://davechurchill.ca/starcraft/aiide/results/2025/bots/BananaBrain.zip)
is the principal implementation reference. `Macro.cpp` compares income and training cost rates,
reserves committed resources and projects affordability; `BuildingPlacement.cpp` and
`UnitPotential.cpp` account for clearance, threats and friendly-unit repulsion. AIRTS adapts those
ideas to its single-resource economy, existing collision system and shared command boundary.

[Stardust's producer](https://github.com/bmnielsen/Stardust/blob/main/src/Producer/Producer.cpp)
provides a second resource-scheduling reference, and its
[attack squad](https://github.com/bmnielsen/Stardust/blob/main/src/General/Squads/AttackBaseSquad.cpp)
considers arriving reinforcements and linking clusters before attacking. Earlier resource-centered
expansion references are [OpenRA](https://github.com/OpenRA/OpenRA/blob/bleed/mods/ra/rules/ai.yaml)
and [Sharpy](https://github.com/DrInfy/sharpy-sc2/blob/master/sharpy/plans/acts/expand.py).
These are algorithm/design references, not ported bot code or runtime dependencies. The policy uses
bounded heuristics and rolling forecasts, without training, neural inference, a terrain influence
map or fog-of-war inference. Tournament standing does not establish AIRTS playing strength.

## Verification owners

[Opponent integration](../../tests/integration/test_opponent_ai.py) checks policy and control;
[skirmish integration](../../tests/integration/test_skirmish.py) checks teams/elimination/results;
[opponent workload](../../tests/performance/test_opponent_performance.py) checks bounded decisions,
with timing acceptance defined in [performance](performance.md#required-workloads).
