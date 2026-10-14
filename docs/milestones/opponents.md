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
recall the nearest capable squad. Severely wounded units use repair-and-return through shared commands.

Combat estimates use health, profile firing cadence, damage and range; close decisions add twelve
aggregate exchanges with approach delays. Fixed turrets wait for mobile enemies to approach;
unfinished turrets contribute no armed threat. Forecasts are heuristic and omit collision, individual focus fire, terrain
routing and reinforcement prediction. Knowledge uses the current fully visible world.

Economy avoids enemy-controlled or unreachable sites, replaces lost builders/factories (using
command centers for builders when necessary), restores income before an unaffordable factory,
expands extraction and protects remote extractors with turrets. Factory capacity scales with income;
additional factories and repair facilities use forward holdings. When safe extractor locations
are exhausted, supplementary Matter Synthesizers use surplus funds. Production mixes light tanks,
scouts, heavy tanks and artillery, with more artillery against turrets. Unpaid construction and needed expensive units reserve funds
by pausing continuous production; only AI budget pauses resume automatically. Manual pauses persist.

Owners decide every ten ticks with staggered slots; economy runs every twenty. A decision updates
at most two combat squads of up to 32 units, checks four attack candidates per squad and attempts
three commands. Economy receives command capacity before offensive squads on economy turns;
production sources rotate to avoid starvation. Each building-kind placement search examines at
most 64 candidates. Shared movement/combat budgets apply.
Decisions derive from ticks and authoritative state; ambient enemy generation is disabled in matches.
Saved squad membership, phases, targets, destinations, command/retry ticks and budget pauses are
validated by the persistence adapter. Current save continuation/replay uses the same decision logic.

The policy adapts resource-centered expansion, building ratios and mixed-unit production from
[OpenRA's AI rules](https://github.com/OpenRA/OpenRA/blob/bleed/mods/ra/rules/ai.yaml) and safe expansion
with reserved construction funds from [Sharpy's Expand plan](https://github.com/DrInfy/sharpy-sc2/blob/master/sharpy/plans/acts/expand.py).
These are design references, not runtime dependencies or copied engines. There is no learned policy,
terrain influence map or fog-of-war inference.

## Verification owners

[Opponent integration](../../tests/integration/test_opponent_ai.py) checks policy and control;
[skirmish integration](../../tests/integration/test_skirmish.py) checks teams/elimination/results;
[opponent workload](../../tests/performance/test_opponent_performance.py) checks bounded decisions,
with timing acceptance defined in [performance](performance.md#required-workloads).
