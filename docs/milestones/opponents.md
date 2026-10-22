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
at most 32 members for membership bookkeeping. Small armies use the first active cohort as their
assembly reference. The main wave launches with 20 locally assembled combat units. After 90 seconds of assembly,
at least 12 can launch; there is no timeout that releases isolated units. Nearby waiting cohorts
merge. Fresh units rally near their producer and wait for a six-unit reinforcement batch before
following the army, rather than being appended to a distant marching cohort.
Ordinary new arrivals join nearby gathering/retreating cohorts, so geographically separate factories
do not share one scattered assembly queue. With no active wave, the largest cohort supplies the
next leader rather than allowing a small distant group to hold all larger forces behind it.
Small marching armies share the target and regroup when stretched out. Ordinary orders combine up to four
nearby cohorts per command; this dispatch chunk is not a total army-size limit. Mission opportunities
and least-recently-ordered cohorts prevent changing front orders from permanently starving rear
reinforcements. A depleted offensive force of fewer than twelve
regroups; forces unable to rebuild, or clearing enemies unable to develop, can finish with survivors.

With at least 500 available field members, a mass assault enrolls the whole field force in one
shared attack command rather than squeezing it into a small leader's arrival slots. Infrastructure
is preferred while choosing a reachable target with a favorable forecast; absent enemy buildings, remnants remain
targets. A live objective supported by at least half the army persists, including below 500 down
to 250 members, so casualty counts do not repeatedly reverse the army into assembly. Existing
matching pursuit is not reissued. Actual route admission still uses the shared combat budget;
issuing a large command does not synchronously compute every member's path. Local opportunistic
fire remains available en route without changing the strategic target. Base rescue still removes
the nearest necessary defenders before field command. Close partial advancing cohorts with a
common objective merge and can receive nearby new members, avoiding singleton command chains.
No extra authoritative mass-assault state or total army limit is introduced.

For small armies, contacts take precedence over the distant objective. Nearby cohorts jointly assess a fight,
engage together or regroup near a friendly production anchor. Estimates include physically
nearby field cohorts without a fixed 96-unit prefix, not distant queued reinforcements. In a favorable
fight, ready rear cohorts share the contact order in least-recently-commanded order; front contact
does not end the decision before offering rear reinforcement an opportunity. Small home garrisons handle
weak raids independently. Otherwise threats to owned infrastructure recall the nearest required
cohorts; a negative individual combat forecast never vetoes base rescue. Recent attacks on
production and income buildings take priority over proximity alone.
Garrisons count as sufficient cover only when they are also near the raid. The nearest required
defenders respond while unneeded field cohorts continue their offensive objective; a persistent
minor border raid does not suspend the entire army. An overwhelming attack can still require all cohorts.
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

Nearest-enemy queries used for frontier and staging positions share one packed enemy-position
snapshot per decision. The numeric scan retains the complete enemy set and stable entity-ID ties;
it does not cap candidates or approximate distance. AI commands in that phase cannot move hostile
bodies, and the snapshot is discarded at the end of the decision. It is not saved AI memory.

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

There is no fixed cumulative building, army, builder or resource ceiling in the AI policy. Available
space, funds and losses constrain real growth. Under ordinary budgets, builder demand scales with
both extractor/synthesizer holdings and factories. Each available builder can own funded work;
there is no separate parallel-site ceiling. Close,
connected resource sites rank ahead of distant speculative holdings, while enemy-controlled and
unreachable sites are rejected. Production buildings first support remote income sites without
a nearby factory, then extend the frontier in bounded steps toward unclaimed resources or enemies.
Factory expansion targets account for income, production time, territory and army size. Ordinary
low-budget planning need not run every factory; surplus cash instead activates affordable idle
factories through standing production orders. After establishing a second factory, exposed production/income
holdings receive turrets before further factory expansion. Repair hubs support damaged armies.
Visible enemy army buildup that substantially outmatches the current force prioritizes a home
turret before further expansion, so early economic growth does not leave the core undefended.
Placement preserves resource footprints and producer exit rings. AI infill avoids road/bridge
terrain and reserves two-cell-wide streets every twelve cells, allowing denser blocks without
sealing their through routes. Fixed extractor sites are exempt from the lattice and terrain
preference: the default river map deliberately places strategic resources on wide bridges.
Existing opening buildings are not relocated to fit that lattice. Ordinary
and surplus construction use the same layout rules. Obstructed production and construction
trigger bounded clearance orders.

After protecting finite production and unpaid construction, cash of at least eight factory prices
enables surplus expansion. This is a spending policy, not an income bonus. The worker target then
scales with cash and the size of developed holdings, but existing idle builders satisfy demand
before further workers are queued. Rich worker replenishment also requires spare builders to fall
below two or a quarter of current construction jobs. The retired six/24-site limits no longer
exclude otherwise affordable work. Command centers preferentially
produce workers in batches of up to six; without a command center, worker demand is limited relative
to the existing/queued army so factories also keep supplying soldiers. Combat at a remote holding
does not stop worker production at safe rear sources or locally safe construction/upgrades.

Surplus growth ranks factory, income, turret and repair-hub deficits together instead of treating
one of each building in a large sector as a completed base. Factory demand combines effective income
with excess available cash spread over a sixty-second spending horizon, using configured production
costs/times. Existing finite/continuous queues estimate spending capacity from their remaining mix;
idle factories use the light-tank rate. Pending factories/income count before completion, without
double-counting scaffolds. Income demand responds to both the production-rate shortfall and the
developing factory count, with continuing reinvestment even when a current ratio is satisfied;
cheaper extraction gets a bounded cost preference. Ordinary budgets likewise retain synthesis as
an investment without the retired ten-factory/ten-synthesizer cutoffs. Turret and repair
demand grow with holdings rather than stopping at one structure. These are planning preferences,
not a guarantee that cash reaches zero in sixty seconds.

Coarse sectors remain eligible while physical build space exists. Footprint density adds a soft
distance penalty rather than excluding developed regions. Nearby builders, existing holdings and the home base
rank regions. Each kind checks the nearest three regions plus a rotating fallback, so local
construction remains stable while blocked shores and contested approaches do not hide all other
land. Every third rich growth period favors vacant regions adjacent to current holdings for new
income/production, rather than waiting until the home area is physically full; fixed extraction
sites retain their own value-based ranking. Remote income holdings without nearby production or defense generate their own factory/turret
demand even when home-base ratios are already satisfied. Support placement prioritizes those gaps
and penalizes already-covered areas. Wide-bridge resources generate defense needs on reachable land
approaches. Free resource sites remain desirable;
site-constrained maps use synthesizers on ordinary land, while unrestricted maps use extractors.
Production/defense coverage uses local spatial queries rather than all building pairs. Strategic
gaps use a bounded nearby/rotating window before regional scoring, so that growing holdings do not
create an unbounded cross-product of candidate sites and supporting buildings.
Regional nearest-holding, builder and coverage distances are computed in warmed numeric batches
over the complete point sets, preserving the scalar distance scores without Python all-pairs loops.
Static impassable footprints are filtered before detailed placement admission; an exhausted kind
does not repeat regional planning in the same decision. Base-threat queries likewise batch the
complete enemy-to-owned-asset neighborhood rather than repeatedly walking overlapping circles.
Producer clearance and staging geometry are shared within the decision and invalidated when a
construction command changes pending footprints. Unreachable candidate approaches are rejected
before repeating shared placement validation; accepted sites still use the shared validator.
Placement rejects enemy fire coverage, nearby enemy producers, occupied/resource footprints,
blocked exits and unreachable builder approaches, including other planned footprints. Surplus mode
has no ten-factory or ten-synthesizer stop condition; safe space, actual funds, workers, demand and
bounded planning work constrain it. New factories enter the ordinary production rotation as soon
as they complete.

Income upgrades compete with expansion using completion time plus cost per additional income;
expansion adds straight-line builder travel and construction time, not a routed arrival forecast.
Only healthy, completed, unthreatened, unreclaimed sources qualify. Next configured income-increasing
upgrades supply their own prices, durations and target profiles; separate buildings can upgrade
concurrently, without a second upgrade on the same entity. Rich reinvestment alternates preference
for a better-payback upgrade with construction, keeping either activity from monopolizing growth.
Idle construction capacity can still upgrade. Non-income
benefits are not evaluated. Existing paid work continues during threats. Synthesizers remain
supplementary income when safe extraction and cheaper upgrades are unavailable.

Ordinary military production uses fully funded short batches. Eight light/heavy frontliners establish the opening
backbone; subsequent production balances resource investment across light tanks, heavy tanks,
light artillery, tank destroyers and heavy artillery, plus a small scout quota. Desired shares shift
from cheap units toward heavy armor and artillery as army investment, actual military cash or
effective income grows. Enemy heavy units increase destroyer demand; fixed defenses increase siege
demand. These are composition preferences, not nonexistent armor-damage bonuses. Finite queued
units and the prepaid current item of continuous production count toward composition, preventing
separate factories from all filling the same gap.

With surplus cash, idle factories receive continuous military orders instead of waiting for a
finite batch to empty and another two-second scheduling opportunity. These have no target-count
stop condition: each unit pays through ordinary production, not an infinite prepaid commitment.
Idle sources rank before retuning already-running factories. Rich composition includes twenty
seconds of standing output, so a group of newly activated factories does not all choose the same
temporarily missing role. Paid AI loops change kind only at a unit boundary while rich; ordinary
cash can preempt them with a funded finite specialist order, preserving paid loop progress and
keeping mixed production possible. Manual pauses remain untouched. Every ordinary economy turn allows at most one immediate reserve/army command
before offering production and growth opportunities; ordinary budgets keep growth before production,
while rich budgets first offer a production command. Remaining opportunities handle clearance,
repairs and production, then further growth if production is already supplied. Other decisions
retain normal tactical response budgets. Thus repeated rescue,
clearance and repair orders cannot consume every funded macro opportunity. Unused opportunities
remain available to other work.

A missing expensive role can save up to its next unit's cost within military funds instead of
repeatedly buying another light tank; existing continuous loops respect that reserve. This can idle
production while income accumulates, but neither queues unaffordable units nor spends the separate
growth envelope. Threatened bases or owners without income use an affordable light-tank fallback
rather than waiting for a specialist. An income-less owner also releases the economic earmark for
production after protecting actual outstanding construction and finite production commitments.
Threats prioritize military output over additional nonessential builders and economic earmarks,
without forcing every funded purchase back to light tanks. No prices, production times or income
multipliers are secretly changed, and no free resources or discounts are granted.

Owners make tactical decisions every ten ticks with staggered slots; ordinary low-budget macro
work remains every twenty. Rich owners additionally rotate through background macro service on
other ticks, without waiting for another tactical decision. A service offers one production command
and one growth command; a successful growth search using at most sixteen detailed candidates can
use a second spare growth opportunity. All owners together attempt at most three commands per
simulation tick, so this is pacing rather than a total queue/building/army limit. Tactical decisions
take priority and normally issue at most two main-army dispatches,
with one on economy turns. Ordinary dispatches process chunks of cohorts; a mass attack shares one
intent across its entire field force and leaves actual routing to the existing per-tick allowance.
Target selection checks at most four candidates and placement
at most 64 candidates per building kind across the complete decision, including surplus searches.
The global sector grid is at most twelve cells per axis; each building kind's window checks at most
four regions with at most sixteen detailed placement candidates each, after filtering known blocked
footprints and the street lattice. Production queues rotate factories and retain ordinary
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
[OpenRA's base builder](https://github.com/OpenRA/OpenRA/blob/bleed/OpenRA.Mods.Common/Traits/BotModules/BaseBuilderBotModule.cs)
also separates excess-cash production expansion, building fractions and defensive placement.
The surplus planner adapts resource-time scheduling and weighted deficits rather than copying
a game-specific build order, tournament configuration or trained model.
These are algorithm/design references, not ported bot code or runtime dependencies. The policy uses
bounded heuristics and rolling forecasts, without training, neural inference, a terrain influence
map or fog-of-war inference. Tournament standing does not establish AIRTS playing strength.

## Verification owners

[Opponent integration](../../tests/integration/test_opponent_ai.py) checks policy and control;
[macro integration](../../tests/integration/test_opponent_macro.py) checks local infill, real combat
spending, new-factory output, configured production time and retained routes;
[scaling integration](../../tests/integration/test_opponent_scaling.py) checks continued ordinary/5x
reinvestment, uncapped worker demand/batches, developed-region reuse, mass attacks, reinforcement
fairness, remote support and concurrent upgrades;
[skirmish integration](../../tests/integration/test_skirmish.py) checks teams/elimination/results;
[opponent workload](../../tests/performance/test_opponent_performance.py) checks bounded decisions,
with timing acceptance defined in [performance](performance.md#required-workloads).
