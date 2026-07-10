# AIRTS Design Document

**Project name:** AIRTS
**Document path:** `docs/design.md`
**Document status:** Current working specification
**Version:** 0.2
**Primary implementation language:** Python
**Initial development environment:** WSL2 Ubuntu 24.04
**Project type:** Open-source research prototype

---

# 1. Project Overview

AIRTS is a lightweight real-time strategy research environment for studying **human-in-the-loop, language-driven automation**.

The project combines conventional RTS interaction with natural-language control. Players retain direct control through the mouse and keyboard, including selecting units, selecting buildings, drawing regions, defining patrol routes, and issuing ordinary commands. Natural language provides a higher-level mechanism for creating, modifying, and managing persistent strategic behaviors.

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
* hundreds of units;
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

Units may temporarily interrupt their assignment for repair.

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
3. if invalid, the unit remains idle;
4. the automation records a waiting, blocked, or failure event.

The unit still has only one ac operational state at a time.

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

# 24. Fog of War and Imation Authority

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
Rendering:  up to 60 frames per second
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
âve complexity
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

# 31. Codex Development Strategy

## 31.1 Codex autonomy

Codex should receive broad implementation freedom within bounded milthin a milestone, Codex may:

* inspect the repository;
* propose module boundaries;
* choose classes and helper functions;
* choose appropriate internal data structures;
* implement the milestone;
* write tests;
* run validation;
* diagnose failures;
* update documentation.

Codex should not be micromanaged method by method.

## 31.2 Architectural invariants

Codex must preserve these rules:

* the simulation core must not depend on the UI;
* the simulation core must not depend on LM Studio;
* the language model must not mutate world state directly;
* all control sources must use shared command interfaces;
* exact geometry comes from the player or deterministic game logic;
* automations are serializable and inspectable;
* failures do not pass silently;
* external dependencies must be justified;
* tests cover important domain behavior;
* documentation changes together with architecture.

## 31.3 Milestone size

Each Codex task should represent one coherent, reviewable outcome.

A milestone should contain:

* one clear goal;
* explicit exclusions;
* defined acceptance criteria;
* required tests;
* documentation requirements.

Codex should not be asked to implement the entire AIRTS project in one task.

## 31.4 Planning workflow

For complex milestones:

1. Codex reads `AGENTS.md` and relevant documentation.
2. Codex inspects the current repository.
3. Codex proposes an implementation plan.
4. Unclear assumptions are identified.
5. Codex implements the bounded milestone.
6. Codex adds or updates tests.
7. Codex runs formatting, linting, type checking, and tests.
8. Codex reviews the final diff.
9. Codex updates relevant documentation.
10. Codex reports remaining uncertainty.

---

# 32. Development Phases

## Phase 0 — Design and repository foundation

Deliverables:

* `docs/design.md`;
* `AGENTS.md`;
* `README.md`;
* Python project configuration;
* Git repository;
* WSL virtual environment;
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
 map editor;
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
* waiting and failutates;
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
*ack range;
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
* selecline context;
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

* benchmenarios;
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

# 33. First End-tond Demonstration

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
* bridges rein available;
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

12. When wouCP provide enough value to justify its additional complexity?

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

