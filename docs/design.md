# AIRTS Design Document

**Project name:** AIRTS
**Document path:** `docs/design.md`
**Document status:** Current working specification
**Version:** 0.3
**Primary implementation language:** Python
**Current development environment:** Native Windows with Python 3.13
**Project type:** Open-source research prototype

## Document ownership and status

This document owns AIRTS product behavior, architecture, scope, milestones, non-goals, and
implemented technical contracts. Read it as follows:

* Sections 1 through 36 define the research vision, architectural invariants, and roadmap.
  Statements using *should*, *may*, or *eventually* are target behavior, not claims that the
  feature is already implemented.
* Sections 37 through 40 record implemented milestone contracts and their acceptance tests.
* Section 41 is the concise current implementation baseline and takes precedence when roadmap
  wording could otherwise be mistaken for current behavior.

All architecture modifications, feature upgrades, optimizations, and behavioral improvements must
be recorded here with their authoritative owner, important tradeoffs, and relevant acceptance
evidence. [`README.md`](../README.md) owns the concise user/developer quick start and controls;
[`AGENTS.md`](../AGENTS.md) owns repository working rules; `pyproject.toml` owns dependencies and
tool configuration. Keep those files consistent without copying this specification into them.

---

# 1. Project Overview

AIRTS is a lightweight real-time strategy research environment for studying **human-in-the-loop,
language-driven automation**.

AIRTS is designed to combine conventional RTS interaction with natural-language control. Players
retain direct control through the mouse and keyboard, including selecting units, selecting
buildings, drawing regions, defining patrol routes, and issuing ordinary commands. Natural
language will provide a higher-level mechanism for creating, modifying, and managing persistent
strategic behaviors. The current build implements direct control and deterministic automations;
language-model integration remains a later phase described in Section 41.

Example instructions include:

* “Keep producing tanks and send them to defend these regions.”
* “Patrol along this route.”
* “Scout this area and report any military buildings.”
* “Retreat damaged units to the nearest repair facility.”
* “Focus on economic development until I cancel this instruction.”
* “Stop producing workers and prepare tanks for an attack.”
* “Use this factory to reinforce the northern bridge.”

The language model does not directly manipulate game objects or control units on every simulation tick. Instead, it translates the player’s grounded intent into structured commands or persistent automation specifications.

The deterministic game core validates and executes those specifications.

AIRTS is not initially intended to be a complete commercial RTS game. Its first purpose is to provide a controlled environment in which language-grounded RTS interaction can be implemented, observed, tested, and evaluated.

A future version may use Unity or another game engine as a frontend, but the initial prototype will be implemented as a Python research environment.

---

# 2. Core Research Vision

AIRTS investigates whether natural language can serve as a practical high-level automation interface for RTS games when combined with direct spatial interaction.

The central interaction model is:

```text
human strategic intent through language
+
precise spatial grounding through direct manipulation
+
persistent deterministic automation
+
continuous human supervision
```

The project does not primarily ask:

> Can an LLM independently play an RTS game?

Instead, it asks:

> Can a human use language, cursor grounding, and direct manipulation to create and manage reliable RTS automations?

This distinction is fundamental.

A fully autonomous LLM-controlled RTS agent would require the model to perform continuous tactical reasoning, maintain long-term memory, react at high frequency, understand the entire map, and control many entities simultaneously. That approach would be computationally expensive, difficult to validate, and unstable.

AIRTS keeps the human in control while using the language model as an interpreter, planner, configuration assistant, and reporting system.

---

# 3. Main Research Questions

## 3.1 Primary research question

Can cursor-grounded natural-language automation help players control complex RTS tasks more effectively than conventional direct control or ungrounded language commands?

## 3.2 Secondary research questions

1. Can local language models reliably translate grounded human instructions into valid automation specifications?

2. Do cursor selections, highlighted entities, named regions, points, routes, rectangles, and freehand areas reduce spatial ambiguity and hallucination?

3. Are parameterized automation templates more reliable than unrestricted LLM-generated plans or scripts?

4. Does exposing active automations to the player improve trust, predictability, understanding, and error correction?

5. When should the system execute an instruction immediately, and when should it request clarification or confirmation?

6. How should long-running automations react to changing world conditions without requiring continuous LLM inference?

7. How much game-world complexity can be introduced before automation reliability decreases significantly?

8. Can structured scouting observations be converted into accurate, useful, and uncertainty-aware natural-language reports?

9. Can language reduce the mechanical burden of RTS control without removing meaningful strategic decision-making?

10. How should responsibility be divided between the human player, the language model, deterministic automation, and low-level game logic?

---

# 4. Project Positioning

AIRTS should not be described only as a “voice-controlled RTS game.”

Natural-language RTS control, language-guided game agents, behavior trees, structured game-state interfaces, and LLM-based strategic planning have already been explored in related projects.

AIRTS should instead be positioned as:

> A human-in-the-loop RTS research environment in which players combine conventional controls, direct spatial grounding, and natural language to create, inspect, modify, pause, and cancel persistent strategic automations.

The distinctive combination includes:

* traditional RTS controls remain available;
* language augments rather than replaces direct control;
* humans provide exact spatial grounding;
* the model interprets strategic intent;
* persistent automations are visible and editable;
* the game core remains authoritative;
* low-level behavior is deterministic;
* model outputs are constrained and validated;
* all important actions can be logged and replayed;
* the system is designed for research evaluation.

---

# 5. Core Design Principles

## 5.1 Language communicates intent; direct manipulation communicates geometry

The language model should not normally invent precise map coordinates.

The player communicates geometry through:

* entity selection;
* point placement;
* line or polyline drawing;
* rectangular selection;
* freehand region drawing;
* multiple selections using `Shift`;
* map beacons;
* paths;
* rally points;
* named regions;
* selected buildings;
* selected unit groups.

The language model interprets:

* the intended task;
* the relationship between selected objects;
* behavior style;
* priorities;
* completion conditions;
* risk tolerance;
* production goals;
* retreat conditions;
* reinforcement conditions.

For example:

```text
Player actions:
1. Select Factory A.
2. Select two regions around a bridge.
3. Say:
   “Keep producing tanks and patrol both of these areas.
   Retreat damaged tanks to the factory.”
```

The human supplies the exact factory and patrol geometry.

The language model determines that the instruction requests:

* continuous tank production;
* assignment of produced tanks to two patrol regions;
* a retreat-and-repair behavior;
* restoration of the original patrol assignment after repair.

The game core determines:

* valid production;
* resource requirements;
* exact spawn locations;
* movement paths;
* patrol coverage;
* engagement behavior;
* repair destinations;
* unit states;
* simulation timing.

## 5.2 The simulation is authoritative

The language model is not the authority on:

* whether an entity exists;
* whether a path exists;
* whether a target is reachable;
* whether terrain is traversable;
* whether resources are sufficient;
* whether a building can produce a unit;
* whether an instruction violates game rules;
* whether a target is visible;
* whether an automation conflicts with another controller;
* whether a unit can perform an action.

The simulation owns the authoritative world state.

Every model-generated proposal must pass deterministic validation before it can affect the world.

## 5.3 The language model should not control every simulation tick

The language model operates at the level of:

* interpreting player intent;
* creating automation proposals;
* modifying existing automations;
* choosing supported behavior profiles;
* selecting valid parameters;
* requesting clarification;
* explaining validation failures;
* summarizing structured observations;
* assisting with high-level strategy.

Ordinary code handles:

* movement;
* pathfinding;
* collision;
* targeting;
* combat;
* damage;
* visibility;
* production;
* resource gathering;
* local reactions;
* patrol execution;
* retreat execution;
* repair;
* state transitions;
* simulation timing.

## 5.4 Automations must be inspectable

Every persistent automation must be:

* visible;
* serializable;
* explainable;
* editable;
* pausable;
* resumable;
* cancelable;
* traceable to its original instruction;
* associated with grounded entities and regions;
* associated with status and event history.

## 5.5 Errors must never pass silently

AIRTS must fail clearly when:

* model output is malformed;
* an entity reference is invalid;
* a path cannot be found;
* an automation becomes impossible;
* a target no longer exists;
* a factory is no longer available;
* a parameter is unsupported;
* an internal invariant is violated.

The system must not pretend that an invalid instruction succeeded.

## 5.6 Initial complexity should be deliberately restricted

The first version should reduce uncontrolled variables.

Initial restrictions include:

* static terrain;
* indestructible bridges;
* no terrain deformation;
* no weather system;
* no air or naval units;
* a small unit-type set;
* deterministic movement;
* deterministic pathfinding;
* simple visibility rules;
* simple combat behavior;
* no multiplayer;
* no dynamic map generation;
* no unrestricted AI-generated executable code;
* no continuous autonomous LLM control.

These are experimental assumptions, not necessarily permanent game rules.

---

# 6. Initial Scope

## 6.1 Included in the initial project

The initial AIRTS prototype should eventually include:

* a 2D grid-based map;
* static terrain;
* map-defined semantic regions;
* player-defined temporary and persistent regions;
* simple ground units;
* factories and support buildings;
* basic resources;
* movement;
* pathfinding;
* basic combat;
* visibility and fog of war;
* deterministic simulation ticks;
* mouse-based unit and building selection;
* point, line, rectangle, and freehand spatial input;
* multiple selections using `Shift`;
* manual RTS commands;
* persistent automations;
* an automation-management panel;
* structured event logs;
* saveable scenarios;
* replayable experiment information;
* LM Studio integration through a local API;
* evaluation scenarios.

## 6.2 Explicit non-goals for the first prototype

The initial prototype will not include:

* Unity;
* Unreal Engine;
* Godot;
* VR;
* multiplayer;
* cloud deployment;
* commercial-quality graphics;
* complex animation;
* physics-based simulation;
* very large maps;
* unbounded or continent-scale armies beyond the verified 1,000-unit interaction target;
* many factions;
* advanced diplomacy;
* procedural campaign generation;
* unrestricted model-generated code;
* continuous model-controlled micro-management;
* autonomous multi-agent warfare;
* reinforcement-learning training;
* destructible bridges;
* dynamic terrain;
* advanced weather;
* naval or air combat.

---

# 7. High-Level Architecture

AIRTS is divided into six conceptual layers.

```text
┌─────────────────────────────────────────┐
│ Human Interaction Layer                 │
│ mouse, keyboard, text, later voice      │
└────────────────────┬────────────────────┘
                     ↓
┌─────────────────────────────────────────┐
│ Spatial Grounding Layer                 │
│ entities, points, lines, regions        │
└────────────────────┬────────────────────┘
                     ↓
┌─────────────────────────────────────────┐
│ Language Interpretation Layer           │
│ grounded intent → structured proposal   │
└────────────────────┬────────────────────┘
                     ↓
┌─────────────────────────────────────────┐
│ Validation and Automation Layer         │
│ schemas, lifecycle, policy, conflicts   │
└────────────────────┬────────────────────┘
                     ↓
┌─────────────────────────────────────────┐
│ Deterministic Simulation Layer          │
│ movement, combat, vision, production    │
└────────────────────┬────────────────────┘
                     ↓
┌─────────────────────────────────────────┐
│ Observation and Evaluation Layer        │
│ events, logs, reports, replay, metrics  │
└─────────────────────────────────────────┘
```

Dependencies should flow downward.

The simulation core must not depend on:

* the graphical UI;
* LM Studio;
* a specific language model;
* voice recognition;
* MCP.

The language-model layer must interact with the simulation only through stable interfaces and validated data structures.

---

# 8. Spatial Input and Grounding

## 8.1 Player-facing spatial tools

AIRTS should support four primary spatial input tools:

* point placement;
* line or polyline drawing;
* rectangular selection;
* freehand area selection.

These tools should be sufficient for the first prototype.

## 8.2 Canonical internal geometry

Internally, the four UI methods are normalized into three geometry types:

```text
PointTarget
PolylineTarget
PolygonRegion
```

A rectangle is represented as a polygon after input processing.

A freehand area is simplified into a valid polygon.

## 8.3 Geometry interpretation

Different automations may interpret the same geometry differently.

| Geometry          | Possible interpretation                                           |
| ----------------- | ----------------------------------------------------------------- |
| Point             | Move to, guard, build near, observe, or patrol within a radius    |
| Polyline          | Follow, patrol, scout, or defend along a route                    |
| Polygon           | Patrol, search, defend, build, gather, or operate within an area  |
| Multiple polygons | Distribute forces, alternate patrol areas, or cover several zones |

For patrol:

* a point means patrol or guard around the point;
* a line means repeatedly follow the route;
* a polygon means patrol or search within the area;
* multiple regions mean patrol or distribute forces across those regions.

## 8.4 Spatial reference types

AIRTS distinguishes between regions, entities, points, and paths.

Initial reference types include:

```text
MapRegionRef
UserRegionRef
EntityRef
PointRef
PolylineRef
```

Examples:

```text
MapRegionRef("dark_mountain")
UserRegionRef("eastern_defense_zone")
EntityRef("factory_01")
PointRef("point_014")
PolylineRef("patrol_route_003")
```

An entity is not treated as a region.

The game resolves an entity reference to:

* its current position;
* its occupied cells;
* its interaction location;
* its valid rally or repair point.

---

# 9. Region Model

## 9.1 Map-defined regions

Map-defined regions are created with the map.

Each map-defined region receives:

* a stable ID;
* a display name;
* polygonal geometry;
* optional semantic labels;
* optional strategic metadata.

Examples:

* Big Grassland;
* Dark Mountain;
* North Bridge;
* Southern Forest;
* Western Resource Field;
* Main Base;
* Northern Approach.

Every important map region should have a default name.

## 9.2 User-defined regions

The player may draw a region during gameplay.

A user-created region is temporary by default.

The player may explicitly assign it a name. A named user region persists and can be referenced in later instructions.

Examples:

```text
Eastern Defense Zone
Tank Assembly Area
Unsafe Forest
Bridge Patrol Area
```

An unnamed region may be discarded when:

* its associated instruction completes;
* its automation is canceled;
* the player clears temporary selections;
* the match ends.

## 9.3 Overlapping regions

Regions may overlap.

A tile may simultaneously belong to:

* Big Grassland;
* Northwest Sector;
* Bridge Approach;
* Player Defense Zone.

Overlapping regions are useful because different labels can represent different semantic meanings.

## 9.4 Spatial relationships

Relationships should be calculated by deterministic code.

Supported relationships may include:

* north of;
* south of;
* east of;
* west of;
* adjacent to;
* inside;
* intersecting;
* connected to;
* reachable from;
* nearest to;
* across a bridge from;
* within a specified distance;
* separated by an obstacle.

The language model may reason using these relationships, but it should not calculate precise geometry itself.

---

# 10. Map Representation

## 10.1 Grid model

The simulation uses a 2D grid.

Each cell has a coordinate:

```text
(x, y)
```

## 10.2 Static terrain layer

Static terrain information may include:

* terrain type;
* movement cost;
* ground passability;
* vehicle passability;
* cover value;
* elevation;
* water depth;
* bridge membership;
* road membership.

Initial terrain types may include:

* grass;
* road;
* forest;
* shallow water;
* deep water;
* bridge;
* rocky or impassable terrain.

## 10.3 Semantic layer

The semantic layer contains:

* region membership;
* region IDs;
* region names;
* landmark IDs;
* bridge IDs;
* choke-point labels;
* resource-zone labels;
* base-zone labels;
* user-defined labels.

## 10.4 Dynamic entity layer

Dynamic state includes:

* units;
* buildings;
* resources;
* construction sites;
* temporary obstacles;
* occupancy.

Dynamic state should not be permanently copied into static terrain definitions.

## 10.5 Visibility and memory layer

Visibility information must be player-specific.

Possible visibility states:

* currently visible;
* previously explored;
* unexplored;
* last-known observation;
* last observation time.

## 10.6 Strategic-analysis layer

Calculated information may include:

* friendly influence;
* enemy influence;
* threat value;
* resource value;
* reinforcement distance;
* path accessibility;
* front-line estimate;
* region control.

These values should be calculated rather than manually stored whenever possible.

---

# 11. Initial Map and Entity Scale

The engine should eventually support maps up to approximately:

```text
256 × 256 cells
```

However, the first vertical slice should use:

```text
64 × 64 cells
```

Recommended progression:

```text
First vertical slice: 64 × 64
Later scenarios:     128 × 128
Scalability target:  256 × 256
```

A small map is easier to:

* render;
* inspect;
* debug;
* replay;
* test;
* reason about;
* use in early model prompts.

## 11.1 Initial entity set

The first meaningful entity set may include:

* scout vehicle;
* light tank;
* heavy tank;
* artillery or long-range tank;
* factory;
* repair hub;
* command center;
* resource generator.

Unit types may differ in:

* health;
* attack damage;
* attack range;
* movement speed;
* vision range;
* production cost;
* repair time;
* terrain capability.

The first vertical slice may use fewer entities than this complete set.

---

# 12. Human Interaction Model

## 12.1 Conventional RTS interaction

AIRTS should retain basic RTS controls:

* click to select one entity;
* drag a rectangle to select multiple entities;
* use `Shift` to add entities or regions;
* right-click or an equivalent command to move;
* select factories and support buildings;
* place points;
* draw routes;
* draw areas;
* inspect entities;
* inspect automations;
* manually override automation-controlled units.

## 12.2 Grounded language context

A language request may include:

```json
{
  "selected_entities": ["factory_01"],
  "selected_regions": [
    "user_region_014",
    "user_region_015"
  ],
  "selected_points": [],
  "selected_polylines": [],
  "cursor_position": [48, 26],
  "instruction": "Keep producing tanks and patrol both of these areas."
}
```

## 12.3 Grounding priority

The system should interpret references in this order:

1. selected entities;
2. selected regions;
3. selected points or paths;
4. explicit references in the instruction;
5. active automation context;
6. recent conversational context;
7. clarification.

## 12.4 Language–grounding conflict policy

AIRTS follows these rules:

1. Vague language with explicit grounding uses the grounding.
2. Explicit language consistent with grounding proceeds normally.
3. Explicit language contradicting grounding triggers clarification.
4. Missing grounding and materially ambiguous language triggers clarification.
5. AIRTS must not guess when several materially different interpretations remain.

Example:

```text
Selected region: North Bridge
Instruction: “Patrol the South Base.”
```

The system should ask which location the player intends.

## 12.5 Confirmation policy

Simple, reversible, well-grounded instructions should not require confirmation.

Examples:

* move selected units to a selected point;
* pause a selected automation;
* cancel a factory queue;
* patrol explicitly selected areas.

Confirmation should be requested when:

* an instruction is materially ambiguous;
* no valid target has been grounded;
* multiple important interpretations remain;
* the action is destructive or difficult to reverse;
* the action affects a large proportion of the player’s forces;
* the action cancels an important automation;
* the action conflicts with a high-priority instruction.

---

# 13. Command Model

## 13.1 Direct commands

Direct commands are short-lived actions.

Initial direct command types may include:

* move;
* stop;
* attack target;
* attack-move;
* hold position;
* set rally point;
* cancel production;
* pause automation;
* resume automation;
* cancel automation.

Direct manual control has the highest authority.

## 13.2 Persistent automations

Persistent automations continue across simulation ticks until they are:

* completed;
* canceled;
* paused;
* invalidated;
* failed;
* replaced;
* expired.

Initial automation templates may include:

* patrol;
* defend;
* scout;
* harass;
* produce;
* produce and rally;
* produce and reinforce;
* gather resources;
* build structure;
* repair and return;
* maintain force threshold;
* develop economy.

## 13.3 Template parameters

Automation parameters may include:

* entity references;
* spatial references;
* unit type;
* unit count;
* production target;
* resource threshold;
* risk tolerance;
* retreat-health threshold;
* patrol order;
* engagement range;
* pursuit range;
* defensive radius;
* reinforcement threshold;
* completion condition;
* priority;
* return destination;
* acceptable loss level.

The initial language model selects a supported template and supplies validated parameters.

It does not generate arbitrary executable code.

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

Weapon firing and locomotion are independent controller concerns. Entering firing range must not
clear a valid move, patrol, defend, pursue, or return path; all armed units should opportunistically
fire at enemies in range regardless of their current movement automation. Line patrol groups should traverse route
vertices in the same direction with deterministic formation spacing rather than assigning members
to opposing endpoint flows. Unit occupancy must defer unit-unit exclusion to physical colliders;
stationary units remain pushable and may yield laterally when forward pressure is obstructed.

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
Military units are finite-cost dynamic path obstacles rather than impassable terrain. Movement
controllers periodically recalculate delayed routes through or around changing formations, with
per-tick path budgets preserving responsiveness during mass movement and focus-fire commands.
Dense movement must retain throughput rather than pausing whole formations. Collision broadphase
pairs are generated directly from spatial buckets, reused across solver passes where safe, and
each unit's deterministic steering neighborhood is converted once into compact collider records
reused by the collision-clamp, local-clearance, and stationary-blocker checks for that movement
attempt. Static building cells are materialized once per movement tick, and local comparisons use
squared distances. Contested final-approach rerouting is reserved for actual stationary blockers;
moving head-on traffic continues through physical steering rather than triggering a path search
per unit. Visibility retains exact circular sight geometry while unioning occupied cells into
per-row integer bit masks before materializing visible cells.
Blocked-unit recovery is budgeted across ticks. These optimizations reduce repeated computation
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

# 24. Fog of War and Information Authority

AIRTS must distinguish between:

* complete internal simulation state;
* information visible to the player;
* previously observed information;
* uncertain or inferred information.

The model receives only information available to the player unless a specific debugging mode is active.

Last-known information should include:

* observation time;
* previous position;
* confidence or certainty;
* whether the entity is still visible.

The model must not receive hidden enemy locations during normal evaluation.

---

# 25. Simulation-Time Model

AIRTS uses a deterministic fixed-step simulation.

The simulation update rate must be independent of the rendering frame rate.

Provisional values:

```text
Simulation: 10 ticks per second
Rendering:  up to 100 frames per second
```

The game should support:

* pause;
* normal speed;
* accelerated testing speed;
* deterministic execution from the same state and seed.

Model latency must not stop the simulation unless the player explicitly pauses the game.

Every model request should record the simulation tick of its input snapshot.

When a response returns, it must be validated against the current world state rather than blindly applied to the older snapshot.

---

# 26. User Interface

The initial UI is a debugging and interaction interface, not a polished commercial interface.

An operating-system window-close request must stop event handling before another simulation or
render pass. The frontend initializes only the Pygame subsystems it uses and releases retained
graphics objects, pending events, fonts, the display, and global Pygame state in a deterministic
order on both normal and exceptional exits.

The default renderer uses a Pygame-created, double-buffered OpenGL 3.3 core window and ModernGL.
It renders directly to the physical framebuffer size: a 3840 x 2160 window therefore receives
native 4K terrain, analytic antialiased unit circles, buildings, health feedback, selection state,
and bounded route lines instead of an enlarged low-resolution image. Terrain and grid instances
are uploaded only when the map transform changes. Entity instances and route vertices are rebuilt
at most once per simulation tick or relevant UI change and remain resident for intervening render
frames. The normal scene is submitted as one instanced terrain draw, one instanced entity draw, and
one bounded line draw.

Pygame font output and infrequent interaction feedback are rasterized into a cached transparent
native-resolution texture and composited by OpenGL. This does not make the application "GPU only":
the simulation, commands, input, frame-data preparation, and font rasterization remain CPU work,
while the GPU owns scene rasterization, antialiasing, and final composition. The legacy bounded
logical `SCALED | RESIZABLE` software framebuffer remains an explicit `--renderer software` mode
for headless CI and compatibility. OpenGL startup failures are diagnostic and never silently fall
back, because doing so would make performance evidence and renderer identity untrustworthy.

On Windows, SDL creates the interactive OpenGL context through the native Windows driver stack,
and ModernGL's standalone verifier uses its native WGL backend. Context creation requires OpenGL
3.3 or newer. Physical refresh rate, VSync, desktop composition, driver behavior, and monitor
timing remain outside the deterministic simulation contract.

WSLg remains a compatibility path and prefers Wayland when no SDL video driver is selected.

Entity hit testing uses the visible occupied footprint for buildings rather than only their center
point. Large focus-fire groups share deterministic reverse navigation fields to the target's valid
interaction perimeter so selecting a building does not trigger one full search per attacker.
When more than 128 units are selected, the renderer preserves selection feedback with a brighter
unit color and one group outline, omits redundant full-health bars, and draws at most 32 evenly
sampled authoritative routes. Damaged-unit and inspected-unit health bars remain visible. An
inspected route is retained in the representative set. This is visual level of detail only: every
selected unit remains selected, simulated, collision-enabled, visible, and owned by its command.
For large selections, unit and building screen transforms, health-bar geometry, group bounds, and
representative route transforms are rebuilt at most once per simulation tick or relevant UI-state
change. Normal buildings and damaged or inspected health feedback remain part of every complete
frame. The explicit software backend retains its scaled-terrain, cached-sprite, and batched-blit
optimizations as an independent regression and compatibility path.

It should eventually display:

* the grid map;
* terrain;
* units;
* buildings;
* resources;
* selected entities;
* selected points;
* drawn paths;
* selected regions;
* named regions;
* active automations;
* automation status;
* unit states;
* recent failures;
* path previews;
* visibility;
* event logs.

## 26.1 Automation panel

The automation panel should allow the player to:

* inspect an automation;
* view its title;
* view its status;
* view assigned entities;
* view target geometry;
* view relevant parameters;
* pause it;
* resume it;
* cancel it;
* modify selected parameters.

Editable parameters may initially include:

* priority;
* retreat threshold;
* patrol order;
* reinforcement threshold;
* risk tolerance;
* completion condition.

## 26.2 Visual style

The initial visual style should remain simple.

Possible representations include:

* colored shapes;
* basic tank icons;
* simple building icons;
* symbolic unit markers;
* clear region overlays.

A lightweight style inspired by simple 2D RTS games is sufficient.

Correctness and observability are more important than visual quality.

---

# 27. Map Editor

The map editor should not be the first subsystem implemented.

The correct dependency order is:

```text
map data model
→ hand-written example map
→ simulation
→ renderer
→ map editor
```

A future map editor may include:

* terrain brush;
* region-drawing tool;
* region naming;
* unit placement;
* building placement;
* resource placement;
* bridge placement;
* validation;
* loading and saving.

The editor must produce the same stable map format used by the simulation.

---

# 28. Observability and Logging

## 28.1 Structured event log

AIRTS should generate structure for:

* player commands;
* language instructions;
* selections;
* region creation;
* automation creation;
* automation modification;
* automation state transitions;
* validation failures;
* unit-state transitions;
* movement failure;
* pathfinding failure;
* combat;
* production;
* resource changes;
* repair;
* visibility changes;
* scouting observations;
* model requests;
* raw model outputs;
* repaired model outputs.

## 28.2 Failure reports

Failure reports should identify:

* which automation failed;
* which operation failed;
* the direct reason;
* relevant entities;
* relevant regions;
* current state;
* whether execution stopped;
* whether partial progress remains;
* whether automatic recovery is possible;
* possible repair actions.

## 28.3 Replay information

A reproducible experiment should record:

* AIRTS version;
* scenario version;
* map version;
* random seed;
* initial state;
* player inputs;
* spatial selections;
* original language instructions;
* model inputs;
* raw model outputs;
* validated commands;
* automation transitions;
* world events;
* final state;
* evaluation metrics.

---

# 29. Evaluation Plan

## 29.1 Possible experimental conditions

AIRTS may compare:

1. mouse and keyboard only;
2. language only;
3. cursor grounding plus text;
4. cursor grounding plus voice;
5. direct commands only;
6. persistent automations;
7. unrestricted model output;
8. constrained automation templates;
9. human-defined geometry versus model-proposed geometry.

## 29.2 Quantitative metrics

Possible metrics include:

* task success rate;
* task completion time;
* number of player actions;
* number of language interactions;
* invalid-output rate;
* clarification rate;
* repair success rate;
* automation-failure rate;
* number of manual corrections;
* resource efficiency;
* unit losses;
* scouting coverage;
* report factual accuracy;
* inference latency;
* token usage;
* automation interruption rate;
* time spent managing automations.

## 29.3 Human-centered metrics

Possible measures include:

* perceived workload;
* perceived control;
* trust;
* predictability;
* comprehensibility;
* ease of correction;
* satisfaction;
* preference;
* perceived strategic freedom;
* perceived responsiveness.

---

# 30. Development Philosophy

AIRTS should use a combination of deliberate design and fast iteration.

The project should avoid both extremes:

```text
Too little design:
“Build an AI RTS game.”
→ inconsistent architecture
→ fragile prototype

Too much design:
specify every class and method before coding
→ unnecessary complexity
→ slow progress
```

The preferred loop is:

```text
define architectural invariants
→ implement one bounded vertical slice
→ test real behavior
→ identify problems
→ revise the design
→ implement the next milestone
```

The design document is a living source of truth.

It should be updated when implementation reveals that an assumption is incorrect.

---

# 31. Engineering Change Policy

`AGENTS.md` owns contributor workflow and validation instructions. This document owns durable
technical decisions and must change whenever implementation work changes architecture, behavior,
scope, dependencies, performance strategy, or a user-visible feature.

Every architecture modification, feature upgrade, optimization, or improvement must record the
following here, in the relevant section or as a new bounded milestone:

* the behavior or constraint that changed;
* the component that remains authoritative;
* effects on dependency direction, determinism, validation, persistence, or replay;
* explicit exclusions and remaining limitations;
* the test or other acceptance evidence that defines the contract.

Milestones should remain coherent and reviewable. Do not add later-phase capability merely because
it is mentioned in the roadmap, and do not turn this document into a class-by-class implementation
manual.

---

# 32. Development Phases

These phases describe capability order, not release labels. Phases 0 through 5 and the implemented
milestones in Sections 37 through 40 form the current baseline. Phases 6 onward remain roadmap
work unless a task explicitly changes that status.

## Phase 0 — Design and repository foundation

Deliverables:

* `docs/design.md`;
* `AGENTS.md`;
* `README.md`;
* Python project configuration;
* Git repository;
* repository-local virtual environment;
* initial milestone definition.

## Phase 1 — First playable vertical slice

Deliverables:

* Python package structure;
* 64 × 64 example map;
* map loading;
* map validation;
* deterministic simulation ticks;
* several simple entities;
* basic 2D rendering;
* entity selection;
* point input;
* line input;
* rectangle input;
* freehand-area input;
* manual movement;
* one patrol automation;
* point, line, and area patrol behavior;
* manual override;
* basic automation panel;
* structured event logging;
* tests for non-visual domain logic.

Explicit exclusions:

* combat;
* economy;
* fog of war;
* LM Studio;
* voice;
* MCP;
* map editor;
* scouting reports;
* multiple automation templates;
* multiplayer;
* Unity.

## Phase 2 — Simulation foundation

Deliverables:

* richer entity model;
* occupancy;
* pathfinding;
* buildings;
* visibility foundation;
* save/load foundation;
* structured events;
* deterministic replay foundation.

## Phase 3 — Command and automation runtime

Deliverables:

* command schemas;
* automation schemas;
* validators;
* automation lifecycle;
* ownership rules;
* manual override;
* waiting and failure states;
* patrol;
* defend;
* production;
* reinforcement;
* repair and return;
* conflict resolution.

## Phase 4 — Traditional RTS interaction

Deliverables:

* improved selection;
* `Shift` multi-selection;
* region naming;
* persistent user regions;
* route editing;
* point editing;
* automation inspection;
* parameter editing;
* richer event display.

## Phase 5 — Basic economy and combat

Deliverables:

* resources;
* production cost;
* factories;
* resource generators;
* simple combat;
* health;
* attack range;
* attack power;
* retreat;
* repair hubs;
* command center;
* economic automation.

## Phase 6 — LM Studio integration

Deliverables:

* provider interface;
* mock provider;
* LM Studio provider;
* prompt builder;
* structured output;
* response parser;
* validation;
* one repair attempt;
* clarification flow;
* tests independent of the live model.

## Phase 7 — Grounded language automation

Deliverables:

* selected-entity context;
* selected-region context;
* selected-point context;
* selected-line context;
* multi-region context;
* grounded reference resolution;
* automation creation through language;
* automation modification;
* language–grounding conflict handling.

## Phase 8 — Scouting and reporting

Deliverables:

* scout selection;
* search planning;
* region coverage;
* visibility;
* observation records;
* risk policy;
* safe return;
* structured report evidence;
* natural-language report generation;
* factuality evaluation.

## Phase 9 — Research evaluation

Deliverables:

* benchmark scenarios;
* deterministic replay;
* metrics;
* baseline control modes;
* experimental scripts;
* result export;
* model comparison;
* user-study preparation.

## Phase 10 — Future extensions

Possible later additions:

* voice recognition;
* map editor;
* larger maps;
* more unit types;
* dynamic obstacles;
* destructible bridges;
* more flexible plans;
* strategic reconsideration;
* MCP exposure;
* external agents;
* cloud models;
* Unity frontend;
* VR interaction;
* multiplayer.

---

# 33. First End-to-End Demonstration

The first major demonstration should eventually support this scenario:

1. Load a small map containing:

   * a player base;
   * one factory;
   * one bridge;
   * roads;
   * repair facilities;
   * several named regions.

2. Select a factory.

3. Select two regions around the bridge.

4. Enter:

   ```text
   Keep producing tanks and patrol both of these areas.
   Retreat badly damaged tanks to the factory.
   ```

5. Convert the request into a validated automation.

6. Display the automation in the management panel.

7. Execute:

   * production;
   * rallying;
   * patrol;
   * engagement;
   * retreat;
   * repair;
   * return to patrol.

8. Allow the player to modify the automation:

   ```text
   Stop production after six tanks and defend more cautiously.
   ```

9. Update the existing automation rather than creating an unrelated one.

10. Record:

    * selections;
    * language input;
    * model output;
    * validated automation;
    * state transitions;
    * failures;
    * final result.

This demonstration validates the core AIRTS idea:

* direct spatial grounding;
* natural-language interpretation;
* persistent automation;
* human supervision;
* automation modification;
* deterministic tactical behavior;
* simulation independence from continuous LLM control.

---

# 34. Initial Experimental Assumptions

The initial prototype assumes:

* selected geometry accurately expresses the player’s intended location;
* players understand basic direct-manipulation controls;
* bridges remain available;
* terrain does not change;
* the simulation is deterministic under a fixed seed;
* the model receives valid entity and region candidates;
* automation templates cover the initial supported tasks;
* local tactical reactions can be implemented without continuous model reasoning;
* the game core owns all authoritative state;
* constrained outputs are more reliable than unrestricted plans;
* small maps are sufficient for early evaluation.

These assumptions should be revisited after implementation evidence becomes available.

---

# 35. Open Questions for Later Iteration

The following questions should not block the first implementation:

1. How should overlapping automations share non-unit resources?

2. Should temporary user regions be automatically named for display?

3. How should large groups be divided across multiple selected regions?

4. Should the player directly edit every automation parameter?

5. When should a `BLOCKED` automation automatically retry?

6. How long should last-known enemy information remain relevant?

7. How should safe locations be scored beyond path distance?

8. Which behaviors should remain fixed templates, and which may later support model-generated composition?

9. How much conversational context should be included for references such as “continue that plan”?

10. When should the model reconsider a long-running strategic automation?

11. Should AIRTS later support an asynchronous strategic model that periodically reviews economy or battlefield state?

12. When would MCP provide enough value to justify its additional complexity?

13. Which local model sizes are sufficient for valid structured command generation?

14. How should voice-input errors be separated from language-understanding errors?

15. How should user workload be measured in a future study?

These questions should be answered through prototypes and experiments rather than speculation alone.

---

# 36. Definition of Initial Success

The initial AIRTS prototype is successful when:

* the map and simulation run without an LLM;
* manual commands and automations use stable shared interfaces;
* a player can ground instructions using entities, points, lines, and regions;
* a patrol automation can operate across time;
* manual control can override an automation clearly;
* automations can be inspected, paused, resumed, modified, and canceled;
* failures are explicit and traceable;
* important events can be replayed;
* an LM Studio model can later create valid automation proposals;
* invalid model output cannot mutate the world;
* persistent automations execute without continuous model calls;
* the architecture is understandable to external researchers and developers;
* the project remains small enough to develop and evaluate as a research prototype.

---

This document should guide development without prescribing every internal class or method.

Codex should retain meaningful engineering freedom while preserving the architectural principles and research goals defined here.

---

# 37. Builder, Factory, and Responsive UI Milestone

## Construction

The simulation adds a factory-produced `builder` unit. A selected builder group may create a factory,
repair hub, or resource generator by choosing a building in the right context panel and clicking a
grid location. UI code may preview or submit construction, but only the simulation may validate
placement, reserve resources, advance progress, or create the building.

Placement validation covers map bounds, terrain, footprint overlap, ownership, and builder
capability. Factories, repair hubs, and resource generators cost 400, 250, and 200 resources. A
shared construction job reserves that cost once and requires 100 construction value. Every builder
still assigned to the job contributes its profile's build speed, currently 5 value per tick, only
while within its 2.5-map-unit build range of the building footprint. Out-of-range builders route to
a valid perimeter point and contribute no work until they arrive. One in-range builder therefore
completes a job in 20 ticks, while additional in-range builders reduce elapsed time without
duplicating the cost or result. Command centers are scenario-defined and not constructible.

Shift-clicking while a building placement tool is active keeps that tool selected and appends a
reserved construction site to the selected builders' FIFO queue. Waiting jobs do not own builders
or reserve resources; they reserve only their non-overlapping footprints. When the current job
completes or is canceled, the next job claims all still-available builders and charges its cost.
A non-Shift construction command replaces pending construction jobs for those builders. Queue
intent, lifecycle, progress, assignments, and destinations are persisted and replayed.

Right-click while placement is active closes only the placement tool. It must not issue a move or
attack command, cancel accepted construction, or change builder assignments. Assigned builders
inside a reserved footprint route to a valid perimeter point and do not contribute work while
inside. If any entity still occupies the footprint at 100 percent progress, the job remains
inspectable with reason `SITE_OCCUPIED` and retries safely; occupancy is committed before the
finished building is published to entity state.

Construction commands, automation state, resource accounting, timing, persistence, and replay all
use the same authoritative simulation path.

## Factory Production

Factories produce every current mobile kind: scout, light tank, heavy tank, and builder. All
manual, finite ordered, and continuous requests share the same five-tick build, resource
reservation, spawn search, event, lifecycle, persistence, and replay path. An ordered request is a
sequence of `(unit kind, exact positive quantity)` stages. It completes only after the last stage
and then leaves the live automation panel. A continuous request repeats one kind until explicitly
canceled; a newer continuous request supersedes an older unfinished continuous request for that
factory. Finite player queues have execution priority over the current continuous request, which
waits and resumes after finite work completes. Continuous production does not create a defense by
itself. `Produce + Defend` and automation retargeting attach the selected polygon or polyline to
the factory's existing loop, preserve that loop's unit kind, and route its existing and future
produced units into one linked defense automation. A polygon uses the expanding gathering-point
formation. A polyline creates an ordinary line defense and deterministically redistributes the
produced force at evenly spaced stations across the full line whenever a unit joins.

Same-kind building controls apply to the complete compatible friendly selection. Selecting several
factories and choosing Loop submits one independent continuous production command per factory;
starting an ordered queue copies the exact staged sequence to every selected factory; and
`Produce + Defend` retargets every selected factory loop to the same line or polygon. The UI only
broadcasts ordinary commands. It does not merge factory state, reserve resources, advance work, or
create units itself. Consequently every factory retains its own authoritative costs, timing, queue
priority, spawn behavior, persistence, and replay history. Other contextual building actions follow
the same selection-wide rule when their underlying command supports a group, including economy
development across all selected friendly resource generators.

## Interface

The application UI has a left status and scrollable automation rail, a central pannable canvas,
and a right selection/action rail. Mixed selections first show every selected entity kind. The
player chooses one kind to deselect other kinds before its statistics and valid controls appear;
a single-kind selection opens those details immediately. Double-clicking a friendly entity selects
all friendly entities of that kind currently inside the canvas. Escape returns to selection mode
and clears entity, geometry, placement, and inspection state. Save, load, and new game
are grouped under a settings button, while the full control reference is hidden from the normal
status surface. Middle-drag pans the canvas through shared map/screen transforms, and the resizable
window recalculates rail, canvas, command-bar, font, and map scaling from its current dimensions.
The left rail shows current FPS. Building placement previews its snapped footprint in green when
valid and red when blocked; accepted construction jobs remain visible with completion progress.
Single-kind detail titles and applicable action labels show the selected count so the scope of a
group command is visible before it is submitted.

## Exclusions

This milestone does not add builder resource gathering, construction refunds,
command-center construction, technology prerequisites, or multiple factories contributing to one
production automation.

---

# 38. Thousand-Unit 100 FPS Interaction Milestone

The frontend targets 100 FPS while the authoritative simulation remains fixed at 10 ticks per
second. The required interaction workload contains 1,000 selected player light tanks on an
80 x 60 map with grass, road, and forest terrain. Move, patrol, and defend commands must each be
accepted for the complete selection; the UI must not deselect, hide, suspend, or omit simulation
work for any unit to meet the budget.

`tests/test_thousand_unit_100fps.py` is the executable expected-behavior contract. For each command
it measures one interval containing command submission, 100 complete Pygame software-surface draw
passes, and ten authoritative simulation advances. The interval must complete within one second.
Afterward, all 1,000 units must still belong to the order, the simulation must have advanced ten
ticks, and at least 100 units must have changed position. Large-selection route feedback must remain
visible through one to 32 deterministic representative paths. Timing setup and the initial warm-up
draw occur outside the measured interval; command planning, movement, automation work, visibility,
collision, panel drawing, and entity drawing occur inside it.

The milestone uses algorithmic and data-layout changes within the existing Python process:

* dense reverse-navigation fields with uniform and weighted builders;
* exact per-row bit-mask unions for visibility;
* one reused spatial-neighbor result per unit movement attempt;
* cached per-frame map transforms and bounded large-selection visual detail.

The simulation remains single-threaded. Rust or worker threads may be reconsidered only after a
measured workload exceeds this architecture's budget; neither is required for this target, and
nondeterministic worker scheduling must never alter authoritative results.

This acceptance contract measures portable core GUI work on a Pygame software surface. Physical
display presentation also depends on CPU speed, refresh rate, VSync, desktop composition, and
GPU/driver behavior, so it is not a cross-machine guarantee of 100 displayed refreshes per second.
The milestone covers move, patrol, and defend responsiveness, not worst-case 1,000-unit combat or
choke throughput; dense choke behavior has its own 500-unit regression.

---

# 39. 4K Thousand-Scout Movement and Collision Milestone

The 4K acceptance workload adds simultaneous rendering and dense movement pressure that the
general interaction milestone does not exercise. It contains two opposing 500-scout formations,
four ordinary friendly buildings outside the traffic lane, and mixed grass, road, and forest on
an 80 x 60 map. Both formations receive head-on move commands and must physically interact during
the measured second. No unit may be hidden, deselected, removed from collision, or omitted from
authoritative simulation to satisfy the budget.

`tests/test_4k_thousand_scout_100fps.py` is the executable expected-behavior contract. It first
isolates 100 complete draws on a real 3840 x 2160 Pygame software Surface and then isolates two
command submissions plus ten authoritative collision ticks. The end-to-end test measures those
commands, ten ticks interleaved at 10 Hz, and 100 complete 4K draws in one interval that must finish
within one second. All 1,000 scouts must retain active orders, collision-pair checks must occur,
the simulation must advance exactly ten ticks, and at least 750 scouts must change position. Its
runtime-configuration assertion additionally requires a bounded logical window opened with both
`SCALED` and `RESIZABLE`.

This milestone remains within the declared Python and `pygame-ce` dependencies. The renderer
caches static terrain scaling, per-tick large-scene transforms, sprite Surfaces, and representative
routes, then batches unit blits. The simulation uses compact reused collider snapshots, cached
static building occupancy, exact bit-mask visibility unions, and larger scout-only staging
clusters. These are data-layout and redundant-work reductions; entity movement, collision,
visibility, selection, buildings, UI panels, and command ownership remain authoritative.

The 3840 x 2160 Surface test verifies CPU-side full-frame construction and remains suitable for
headless regression testing. It cannot prove that a physical 4K monitor presents 100 distinct
refreshes per second. The explicit software runtime still renders a smaller logical Surface and
asks SDL to scale it; backend acceleration is environment-dependent, and `pygame.SCALED` may report
that no fast renderer is available. Section 40 adds the separate native OpenGL contract. Worst-case
1,000-unit combat and dense-choke throughput remain separate workloads.

---

# 40. Native-4K OpenGL Rendering Milestone

The interactive runtime defaults to an explicit OpenGL 3.3 renderer implemented with ModernGL.
The renderer must use a native physical framebuffer with `OPENGL | DOUBLEBUF | RESIZABLE`; it must
not use `SCALED` or silently substitute the software backend. OpenGL or dependency failure is an
actionable startup error. `--renderer software` is the only supported way to request the existing
software path.

The GPU scene consists of native-pixel instanced rectangles and analytic antialiased circles.
Every terrain cell and grid line remains present. Every unit and building remains visible, and
selection tint, group outline, damaged/inspected health bars, and one to 32 representative routes
remain consistent with the software renderer. Static terrain data is uploaded once per transform;
dynamic instance and line buffers update at simulation-tick cadence rather than render cadence.
The GPU performs rasterization and composition. The CPU still performs authoritative simulation,
collision, commands, input, buffer preparation, and font rasterization.

The existing Pygame interaction UI is preserved as a cached transparent native-resolution texture.
Spatial editing feedback, construction previews, gathering glow, projectiles, panels, settings, and
help are rebuilt only when their authoritative or interaction state changes and are then composed
by OpenGL. This hybrid keeps the complete interface available without uploading or redrawing the
1,000-unit base scene in software.

`tests/test_opengl_thousand_scout_100fps.py` is the executable contract. It verifies:

* native 3840 x 2160 framebuffer coordinates and a 1.0 pixel scale;
* OpenGL, double buffering, resizing, and absence of `SCALED` on the default backend;
* native platform context selection: WGL on Windows and Wayland preference on WSLg;
* all 4,800 terrain cells, 1,000 scouts, four buildings, selection, and bounded routes;
* one cached terrain draw, one entity draw, one bounded line draw, and one UI composition draw;
* diagnostic context failure with no hidden software fallback;
* deterministic buffer reuse and explicit GPU-resource release;
* two head-on 500-scout commands, ten collision ticks, 100 native-4K GPU frames, a non-background
  rendered-pixel check, and a final GPU completion wait within one second;
* rejection of llvmpipe, softpipe, SwiftShader, or another software rasterizer as hardware proof.

The dependency is `moderngl>=5.12,<6`, which provides OpenGL 3.3 core access and instanced buffer
submission on Python 3.13. A passing offscreen hardware benchmark proves GPU rasterization and the
100 FPS work budget on the tested adapter. The verifier uses ModernGL's native platform backend,
including WGL on Windows, instead of hard-coding EGL; known Mesa software rasterizers, SwiftShader,
GDI Generic, and the Microsoft Basic Render Driver are rejected as hardware evidence. It still
cannot prove that a compositor and physical monitor display 100 distinct refreshes, and it does
not change the simulation's fixed 10 Hz rate.

---

# 41. Current Implemented Baseline

This section is the current-state index. The milestone details in Sections 37 through 40 remain
normative. The README contains setup, runtime, and controls; `AGENTS.md` owns validation commands.

## 41.1 Architectural ownership

| Concern | Authoritative implementation |
| --- | --- |
| Tagged control inputs and serialization | `src/airts/commands.py` |
| World state, validation, commands, automation execution, movement, combat, resources, and ticks | `src/airts/simulation.py` |
| Automation schemas, lifecycle, and deterministic geometry planning | `src/airts/automations.py` |
| Map, entity profiles, geometry, occupancy, routing, visibility, and spatial references | Focused UI-independent modules under `src/airts/` |
| Versioned save/load and deterministic command replay | `src/airts/persistence.py` and `src/airts/replay.py` |
| Input, inspection, panels, and the explicit software renderer | `src/airts/app.py` |
| Native OpenGL frame construction and submission | `src/airts/opengl_renderer.py` |

The dependency direction remains simulation core outward to adapters. The simulation imports
neither Pygame nor a model provider. UI actions submit the same command objects used by replay and
future language adapters; renderer code reads authoritative state but does not advance or mutate
domain behavior.

## 41.2 Supported behavior

The bundled scenario is a validated 64 x 64 static grid using grass, road, forest, water, rock, and
bridge terrain. Current entities are scouts, light tanks, heavy tanks, builders, factories, repair
hubs, command centers, and resource generators.

The runtime currently supports:

* fixed 10 Hz deterministic simulation with a separately paced 100 FPS frontend target;
* direct move, stop, hold, and explicit attack commands with manual override;
* point, polyline, rectangle, and freehand grounding; typed selection; region naming; whole-object
  geometry replacement; and route/region deletion;
* patrol, defend, production, construction, reinforcement, repair-and-return, and economy
  automations with inspectable lifecycle, priority, pause, resume, cancellation, and event history;
* weighted four-direction routing, distinct group destinations, local physical collision and push,
  opportunistic projectile combat, visibility/exploration state, resource income, production, and
  builder construction;
* versioned complete-state saves, deterministic replay verification, JSON Lines event export,
  configurable deterministic enemy generation, and custom map loading;
* a native OpenGL 3.3 default renderer and an explicitly selected bounded software renderer.

Starting resources are one integer balance per owner. Each resource generator adds 1,000 resources
every ten simulation ticks. Ambient enemy generation defaults to one mobile enemy per second with a
cap of 100 and can be disabled or reconfigured. Save and replay documents preserve those settings
and reject incompatible schema versions.

Sections 38 through 40 define the three 1,000-unit performance contracts and the limits of the
evidence each one provides.

## 41.3 Current implementation limitations

Builders do not gather resources, construction cannot be canceled for a refund, and there is no
technology tree, armor, cover, splash damage, missed shots, or tactical enemy AI. Visibility tracks
authoritative visible, explored, and unexplored cells but does not yet provide line-of-sight
occlusion, last-known enemy observations, or a fog overlay.

Geometry editing replaces an entire point, route, or region rather than individual vertices.
Map-defined semantic regions and multi-region automation semantics are not implemented. LM Studio
and other language providers, voice, MCP, scouting reports, multiplayer, Unity, and a map editor
remain outside the implemented phase.
