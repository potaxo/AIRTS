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

A single army objective replaces independent opportunistic squad raids. Persistent cohorts retain
at most 32 members for bounded command/routing work; the first active cohort leads the army.
The main wave launches with 20 locally assembled combat units. After 90 seconds of assembly,
at least 12 can launch; there is no timeout that releases isolated units. Nearby waiting cohorts
merge. Fresh units rally near their producer and wait for a six-unit reinforcement batch before
following the army, rather than being appended to a distant marching cohort. Marching cohorts
share the target and regroup when stretched out. A depleted offensive force of fewer than twelve
regroups; forces unable to rebuild, or clearing enemies unable to develop, can finish with survivors.

Army contacts take precedence over the distant objective. Nearby cohorts jointly assess a fight,
engage together or regroup near a friendly production anchor. Estimates include only physically
nearby support, capped at 96 units, not distant queued reinforcements. Small home garrisons handle
weak raids independently. Otherwise threats to owned infrastructure recall the nearest required
cohorts; a negative individual combat forecast never vetoes base rescue. Recent attacks on
production and income buildings take priority over proximity alone.
Defenders first assemble around the threatened asset and nearby turret cover; they engage when
the enemy reaches that coverage, threatens firing range, or owned units/assets are already hit.
They do not chase a merely approaching force away from their own supporting defenses. Turret
placement faces the nearest visible enemy rather than selecting the rear of a producer.
Outside base defense, severely
unfavorable contact triggers regrouping. Severely wounded units repair-and-return when infrastructure
does not require immediate rescue. Targets mix nearby and valuable reachable assets, and surviving
armies continue clearing remnants rather than stopping after destroying production.

Combat estimates use health, profile firing cadence, damage and range; close decisions add twelve
aggregate exchanges with approach delays. Fixed turrets wait for mobile enemies to approach;
unfinished turrets contribute no armed threat. Forecasts omit collision, focus fire, splash effects,
terrain routing and future reinforcement timing. Knowledge uses the fully visible world. Tactical
control is a bounded rule-based state machine, not a learned policy or a full combat simulator.

Economy and production use separate spending envelopes. Saved growth credit earmarks roughly 40%
of available opening/reclaimed cash and ongoing building income; the remainder funds military
production and additional builders. Credit is spending intent, not extra money: each budget update
clamps it against real cash after finite production and unpaid construction commitments. Unexpected
or emergency spending can temporarily exceed the earmark, which is reconciled on the next update.
Fully funded construction deducts its earmark on acceptance, and its ordinary unpaid job protects
the cash until builders arrive. Expensive growth goals save only within that envelope; they do not
freeze all army production or enqueue unaffordable buildings. Recovering lost production/income
and emergency defense can use the shared balance. Manual production pauses remain untouched;
AI budget pauses on existing continuous loops remain tracked until actual resume.

Builders scale from three toward eight with resource holdings and factories; command centers
supply them preferentially. Up to six separate builders can construct concurrently. Close,
connected resource sites rank ahead of distant speculative holdings, while enemy-controlled and
unreachable sites are rejected. Production buildings first support remote income sites without
a nearby factory, then extend the frontier in bounded steps toward unclaimed resources or enemies.
Factory targets account for income, production time, territory and army size rather than requiring
every factory to run continuously. After establishing a second factory, exposed production/income
holdings receive turrets before further factory expansion. Repair hubs support damaged armies.
Visible enemy army buildup that substantially outmatches the current force prioritizes a home
turret before further expansion, so early economic growth does not leave the core undefended.
Placement preserves resource footprints and producer exit rings. Obstructed production and
construction trigger bounded clearance orders.

Income upgrades compete with expansion using completion time plus cost per additional income;
expansion adds straight-line builder travel and construction time, not a routed arrival forecast.
Only healthy, completed, unthreatened, unreclaimed sources qualify. Next configured income-increasing
upgrades supply their own prices, durations and target profiles; one runs at a time. Non-income
benefits are not evaluated. Existing paid work continues during threats. Synthesizers remain
supplementary income when safe extraction and cheaper upgrades are unavailable.

Factories produce fully funded short batches. Eight light/heavy frontliners establish the opening
backbone; subsequent production balances resource investment across light tanks, heavy tanks,
light artillery, tank destroyers and heavy artillery, plus a small scout quota. Desired shares shift
from cheap units toward heavy armor and artillery as army investment, actual military cash or
effective income grows. Enemy heavy units increase destroyer demand; fixed defenses increase siege
demand. These are composition preferences, not nonexistent armor-damage bonuses. Finite queued
units and the prepaid current item of continuous production count toward composition, preventing
separate factories from all filling the same gap.

A missing expensive role can save up to its next unit's cost within military funds instead of
repeatedly buying another light tank; existing continuous loops respect that reserve. This can idle
production while income accumulates, but neither queues unaffordable units nor spends the separate
growth envelope. Threatened bases or owners without income use an affordable light-tank fallback
rather than waiting for a specialist. An income-less owner also releases the economic earmark for
production after protecting actual outstanding construction and finite production commitments.
Threats prioritize military output over additional nonessential builders and economic earmarks,
without forcing every funded purchase back to light tanks. No prices, production times or income
multipliers are secretly changed, and no free resources or discounts are granted.

Owners decide every ten ticks with staggered slots; budgets, construction and production run every
twenty. A decision attempts at most three commands and updates at most two combat cohorts, with one
normally reserved on economy turns. Target selection checks at most four candidates and placement
at most 64 candidates per building kind. Production queues rotate factories and retain ordinary
shared validation, routing and collision budgets. Saved cohort phases, objectives, command/retry
ticks, budget pauses, growth credit and its update tick resume through current-format persistence;
AI commands are regenerated internally during replay.

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
