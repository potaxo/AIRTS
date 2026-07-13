# AIRTS Command and Automation Architecture

This document owns command semantics, automation lifecycle and ownership, persistent behavior templates, and group behavior.

[Design index](../design.md) · [Roadmap](../roadmap.md)

---

# 14. Automation Lifecycle

An automation may enter the following states:

```text
PROPOSED
VALIDATING
AWAITING_CONFIRMATION
ACTIVE
WAITING
PAUSED
BLOCKED
COMPLETED
FAILED
CANCELED
```

## 14.1 `WAITING`

Used when progress can resume automatically.

Examples:

* insufficient resources;
* no units currently available;
* production queue occupied;
* repair hub at capacity;
* prerequisite construction incomplete;
* waiting for produced units.

## 14.2 `BLOCKED`

Used when the automation cannot currently perform a valid action.

Examples:

* no reachable path;
* target temporarily inaccessible;
* conflicting higher-priority controller;
* required target cannot currently be observed.

## 14.3 `FAILED`

Used when the goal can no longer be achieved without reinterpretation or human intervention.

Examples:

* source factory destroyed;
* target permanently removed;
* required capability no longer exists;
* unsupported automation state;
* unrecoverable internal error.

## 14.4 `COMPLETED`

Used when the explicit goal has been achieved.

## 14.5 `CANCELED`

Used when the player or another authoritative system intentionally terminates the automation.

All state transitions must be logged with structured reason codes.

---

# 15. Automation Ownership and Control Priority

## 15.1 Unit control model

Each unit has:

* one active operational state;
* at most one active controlling assignment;
* an optional suspended assignment that may be resumed after a temporary task.

A unit should not be controlled simultaneously by several conflicting automations.

## 15.2 Control precedence

Control conflicts are resolved in this order:

```text
1. Direct human control
2. Emergency safety behavior
3. Explicit automation priority
4. Newer instruction among equal-priority instructions
```

A recent low-priority patrol instruction must not override an emergency retreat.
Creating a new patrol or defend automation for an explicitly selected unit is a direct
reassignment, not background arbitration. It replaces that unit's older normal automation
assignment even when the new automation has lower priority. If the older automation loses its
last unit, it is canceled and removed from the live automation list. Emergency repair remains
the exception because it intentionally suspends and later restores the operational assignment.

## 15.3 Manual override

When the player directly controls a unit that belongs to an automation:

1. the unit is detached from the automation;
2. the automation records a manual-override event;
3. the automation continues with remaining units when possible.

Example:

```text
Automation units:
tank_01, tank_02, tank_03

Player manually controls tank_01.

Updated automation units:
tank_02, tank_03
```

The change must not happen silently.

## 15.4 Automation with no remaining entities

When an automation loses all assigned units:

* it enters `WAITING` if it has a valid source of future units;
* otherwise, it is canceled with reason `NO_ASSIGNED_ENTITIES`.

For example, a production-and-patrol automation may remain active while waiting for a factory to produce replacement units.

## 15.5 Factory override

When a player manually changes a factory controlled by an automation:

* the factory is detached;
* the automation records the override;
* the automation enters `PAUSED`, `WAITING`, or `CANCELED`;
* the change appears in the automation panel.

---

# 16. Automation Display and Provenance

Every automation stores:

* automation ID;
* short title;
* original player instruction;
* selected automation template;
* grounded entity references;
* grounded spatial references;
* parameter values;
* priority;
* creation time;
* modification time;
* model-provider information;
* validation history;
* execution history;
* current status;
* current reason code.

The title should normally contain three to five words.

Examples:

```text
Defend North Bridge
Scout Eastern Forest
Expand Tank Production
Patrol River Route
Reinforce Western Base
```

The automation panel displays the short title by default.

An expanded view may show:

* original instruction;
* source entities;
* target regions;
* behavior parameters;
* current units;
* creation source;
* recent events;
* failure information.

---

# 17. Unit and Group Behavior Model

## 17.1 Local behavioral states

Initial unit or group states may include:

* idle;
* moving;
* gathering;
* patrolling;
* defending;
* engaging;
* pursuing;
* retreating;
* regrouping;
* waiting for reinforcement;
* returning to base;
* repairing;
* scouting.

## 17.2 Behavior parameters

Automations may configure:

* engagement range;
* pursuit distance;
* retreat-health threshold;
* minimum group strength;
* reinforcement threshold;
* patrol duration;
* patrol order;
* risk tolerance;
* acceptable loss level;
* return destination;
* defensive radius;
* target priority.

## 17.3 Local behavior controllers

AIRTS may use deterministic behavior controllers that appear intelligent without requiring continuous LLM inference.

Local movement, physical collision, and nearby combat queries should use deterministic spatial
broadphase indexing so per-tick work depends primarily on nearby entities rather than every
possible entity pair. Automatically generated forces must have configurable rates and active
population caps to prevent unbounded scenario growth.

Defend retaliation follows the same rule: each attacked assigned unit queries a deterministic
spatial index for responders inside the response radius. It must not scan every assigned defender
for every victim, because a mass engagement would otherwise become quadratic while producing the
same local response set.

Weapon firing and locomotion are independent controller concerns. Opportunistic fire must not clear
a valid move, patrol, defend, or return path. An explicit pursue order is different: once the ordered
target is inside weapon range, its adjacency path is complete and must be cleared so a mass focus
command forms a firing envelope instead of converging on four neighboring cells. All armed units
still opportunistically fire at enemies in range regardless of their current movement automation.
Large patrol groups traverse route vertices in the same direction with deterministic collision-safe
formation spacing rather than assigning members to opposing flows. Unit occupancy must defer
unit-unit exclusion to physical colliders; stationary units remain pushable and may yield laterally
when forward pressure is obstructed.

Projectile simulation is independent of presentation scale. Every launched projectile stores its
current position and last known target destination. It tracks a live hostile target, but target
removal does not remove the projectile: the shot completes its flight to the stored destination,
creates its normal visible trace, and applies no damage there. The UI renders all bullets with one
small fixed-pixel core and high-contrast halo so resizing cannot change weapon readability or imply
different projectile strength.

A production automation with an explicitly attached defense area sends every produced unit into
one linked military gathering defense without resetting existing defenders. Its unique stations
are allocated from the selected area's center outward over all reachable map space; there is no
fixed automation unit cap. The visible gathering glow grows with the occupied radius, and new
deployment routes are budgeted per tick. Stations use collision-aware dense packing, preserve
center-first filling, and are recalculated when assigned force size changes so the visible radius
contracts as well as expands. Defend-line stations are distributed by distance across the full
polyline rather than concentrated at its vertices. Section 37 defines factory reservation, queue
priority, and explicit defense-attachment lifecycle.

Static patrol, defend, repair, production-rally, and combat routes pass through one deterministic
routing service. It reuses bounded navigation fields for shared destinations and admits automation
route work through fair per-controller and global per-tick budgets, so a large task cannot monopolize
a simulation tick or permanently starve another task. Route validation proves group and route
connectivity with shared fields instead of repeating a full-map search for every formation slot.
Navigation fields store costs, next links, and goal ownership in dense integer-indexed arrays.
Uniform-cost passable terrain uses deterministic layer expansion without a heap; mixed terrain
uses deterministic weighted Dijkstra with the same goal and next-cell tie rules. These are
representation and construction optimizations, not approximations of terrain cost or reachability.
Large direct-move formations may cluster paths through nearby staging anchors before branching to
unique final slots. Scout formations use 8 x 8 staging clusters at the verified 1,000-unit scale;
other unit kinds retain 5 x 5 clusters so heavier formations do not regress dense-choke behavior.
Replans whose costs depend on current unit positions remain uncached because their obstacle
penalties change with the formation.
Settled military units are finite-cost dynamic path obstacles rather than impassable terrain.
Movement controllers periodically recalculate delayed routes around settled blockers, with stable
per-entity phases and per-tick path budgets preserving responsiveness. Moving queue members are not
fed back into per-agent A*: the shared static field supplies global direction while deterministic
local steering, collision response, and bounded yielding carry the flow through unavoidable chokes.
Blocked recovery follows the same rule and does not search for an alternate route merely because a
moving bridge queue occupies the next cells.
Dense movement must retain throughput rather than pausing whole formations. Collision broadphase
pairs are generated directly from spatial buckets, reused across solver passes where safe, and
each unit's deterministic steering neighborhood is converted once into compact collider records
reused by the collision-clamp, local-clearance, and stationary-blocker checks for that movement
attempt. Overlap correction uses at most three deterministic relaxation passes; the third pass
prevents pressure propagated through a dense moving front from leaving a deeply collapsed pair
while preserving the 6,000-pair-check budget in the 500-unit choke regression.
Homogeneous scout crowds use one drive-pressure pass and two overlap passes because a scout can
separate its full radius in one correction; mixed or heavier forces retain two and three passes,
respectively, under the physical-collision and heavy-choke regressions. Static building
cells are materialized once per movement tick, and local comparisons use squared distances.
Contested final-approach rerouting is reserved for actual stationary blockers and shares the same
stalled-route budget;
moving head-on traffic continues through physical steering rather than triggering a path search
per unit. Visibility retains exact circular sight geometry while unioning occupied cells into
per-row integer bit masks before materializing visible cells.
When an ordinary defend target cannot physically contain its assigned force, it automatically uses
the same reachable hex-packed deployment slots as a gathering defense instead of repeating a few
station coordinates. Tiny point and area patrols move one spaced formation through the patrol cycle
in phase. Blocked-unit recovery is budgeted across ticks. These optimizations reduce repeated computation
without adding map-specific bridge or road rules or stopping opposing formations as a group.
The initial implementation remains single-threaded so identical state and commands cannot diverge
because of worker scheduling.

Examples:

* patrol controller;
* defense controller;
* scout controller;
* retreat controller;
* reinforcement controller;
* production controller;
* economy controller.

The language model configures these controllers.

It does not execute their individual simulation steps.

---

# 18. Repair and Resume Behavior

Units may temporarily interrupt their assignment for repair. The conventional `R` interaction
claims only selected units whose health is strictly below 30%; healthier selected units keep their
current assignment and must not incur repair-path computation.

The initial repair-destination order is:

```text
1. nearest reachable operational repair hub
2. associated or nearest reachable friendly factory
3. nearest reachable friendly command center
```

“Nearest” should be calculated using valid path cost rather than straight-line distance.

A unit sent for repair stores its original automation assignment:

```json
{
  "active_state": "REPAIRING",
  "resume_automation_id": "automation_014"
}
```

After repair:

1. AIRTS revalidates the original automation;
2. if valid, the unit returns to it;
3. if no original automation exists, the unit returns to its recorded pre-repair position;
4. if the prior work is invalid and no return can be completed, the unit remains blocked or idle;
5. the automation records a waiting, blocked, or failure event.

The unit still has only one active operational state at a time.

---

# 19. Scouting System

Scouting is an important early end-to-end research scenario.

## 19.1 Player interaction

The player:

1. selects a target region;
2. optionally selects scout units;
3. gives a language instruction.

Example:

```text
Scout this area and tell me whether the enemy has military buildings.
Return to a safe location afterward.
```

## 19.2 Language-model output

The model produces a structured proposal:

```json
{
  "type": "scout_region",
  "target_regions": ["user_region_021"],
  "requested_information": [
    "enemy_military_buildings",
    "enemy_units"
  ],
  "risk_tolerance": "low",
  "return_policy": "return_to_safe_location"
}
```

## 19.3 Deterministic planning

Game code determines:

* available scout units;
* route;
* search waypoints;
* region-coverage pattern;
* danger response;
* completion condition;
* return location.

## 19.4 Observation recording

Structured observations may include:

* entity type;
* entity ID when known;
* coordinate;
* observation tick;
* confidence;
* current visibility;
* estimated quantity;
* region coverage;
* unobserved subregions;
* scout damage or losses.

Example:

```json
{
  "task_id": "scout_004",
  "coverage_ratio": 0.72,
  "observations": [
    {
      "type": "enemy_building",
      "building_type": "vehicle_factory",
      "position": [84, 31],
      "observed_at_tick": 9012,
      "confidence": 1.0
    }
  ],
  "unobserved_subregions": [
    "northeast_corner"
  ]
}
```

## 19.5 Report generation

The language model may summarize the structured evidence after completion or failure.

The report must distinguish:

* confirmed observations;
* estimated counts;
* last-known information;
* unobserved areas;
* uncertainty;
* incomplete objectives.

The model must not invent observations not present in the evidence.

---

# 20. Language-Model Integration

## 20.1 Initial provider

The initial implementation will use LM Studio through its local API.

MCP is not required for the first version.

## 20.2 Provider abstraction

AIRTS should not depend directly on one model or provider.

A provider interface should allow implementations such as:

```text
MockProvider
LMStudioProvider
FutureCloudProvider
```

The rest of AIRTS should not need to know which model produced a response.

## 20.3 Initial model responsibilities

The language model may:

* interpret grounded instructions;
* select valid command types;
* select valid automation templates;
* map references to provided IDs;
* choose supported behavior profiles;
* propose parameter values;
* request clarification;
* repair malformed proposals;
* explain failures;
* summarize scouting evidence.

## 20.4 Prohibited model responsibilities

The model must not:

* directly mutate world state;
* invent hidden entities;
* create unknown unit IDs;
* invent unsupported automation templates;
* calculate authoritative paths;
* calculate combat results;
* bypass validation;
* generate executable Python code for runtime behavior;
* receive hidden enemy information during normal evaluation.

---

# 21. Stateless Model Interaction

The authoritative memory belongs to AIRTS.

The language model is not responsible for remembering:

* world state;
* named regions;
* entity IDs;
* active automations;
* long-term goals;
* resource levels;
* previous failures;
* previous unit assignments.

For every request, AIRTS constructs a compact context containing relevant current information.

Example:

```json
{
  "instruction": "Continue defending the bridge, but protect the tanks.",
  "selected_entities": ["tank_group_01"],
  "selected_regions": ["north_bridge"],
  "active_automations": [
    {
      "id": "automation_014",
      "type": "defend_region",
      "target": "north_bridge"
    }
  ],
  "relevant_world_state": {},
  "recent_events": [
    "tank_02 health dropped below 40 percent"
  ],
  "available_actions": [],
  "schema_version": "0.2"
}
```

Persistent instructions such as:

```text
Develop the economy.
```

must become structured automations stored by AIRTS.

Conversation history may be included as supporting context, but it must never be the sole authoritative source.

---

# 22. Validation Pipeline

Every model-generated proposal passes through:

```text
schema validation
→ reference validation
→ capability validation
→ spatial validation
→ path validation when applicable
→ resource validation
→ automation-conflict validation
→ confirmation policy
→ execution
```

Possible validation failures include:

* malformed schema;
* unknown entity;
* unknown region;
* unsupported automation;
* unsupported parameter;
* invalid parameter range;
* incompatible unit type;
* unreachable target;
* insufficient resources;
* ownership conflict;
* target no longer valid;
* information unavailable under fog of war.

No invalid or partially validated proposal may modify world state.

---

# 23. Model Repair Policy

When model output fails validation:

1. AIRTS creates a structured error report.
2. The error identifies the failed validation phase.
3. AIRTS includes invalid fields and authoritative evidence.
4. AIRTS performs at most one automatic repair request.
5. The repaired response passes through the complete validation pipeline.
6. If it fails again, AIRTS stops automatic repair.
7. The failure is reported to the player.

This means:

```text
Initial generation
→ one repair attempt
→ human-facing failure
```

There should be no unlimited retry loop.

The model may generate a readable explanation, but AIRTS supplies the authoritative error code and evidence.

---
