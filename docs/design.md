# AIRTS Design Index

**Document status:** Current working specification
**Version:** 0.4
**Environment:** Native Windows with Python 3.13
**Project type:** Open-source research prototype

This file is the authoritative entry point for AIRTS product behavior, architecture, scope,
non-goals, and implemented baseline. It intentionally stays concise enough to review as a whole;
detailed contracts live in the linked owner documents below.

## Document map

| Concern | Authoritative document |
| --- | --- |
| Current vision, invariants, high-level architecture, and implementation baseline | This file |
| Research questions, future scope, phases, and open questions | [Research roadmap](roadmap.md) |
| Spatial grounding, maps, entities, and world representation | [Spatial and world architecture](architecture/spatial-world.md) |
| Commands, automations, ownership, and behavior templates | [Command and automation architecture](architecture/automation-model.md) |
| Visibility, persistence, replay, providers, and evaluation | [Information and integration architecture](architecture/information-integrations.md) |
| Repository structure and engineering-change policy | [Engineering architecture](architecture/engineering.md) |
| Builder, factory, and responsive-UI implemented contract | [Runtime milestone](milestones/runtime.md) |
| 1,000-unit software, native-4K, and OpenGL contracts | [Performance milestones](milestones/performance.md) |
| Consequential decisions and their tradeoffs | [Architecture decisions](decisions/README.md) |
| User/developer setup, runtime, controls, and common workflows | [README](../README.md) |
| Contributor workflow and required validation | [AGENTS.md](../AGENTS.md) |
| Dependencies and tool configuration | [pyproject.toml](../pyproject.toml) |

## Status and change policy

Roadmap language such as *should*, *may*, and *eventually* is not a claim of implemented behavior.
The current baseline in Section 41 and the implemented milestone documents take precedence when a
future-facing statement could be ambiguous.

Every architecture modification, feature upgrade, optimization, and behavioral improvement must
update this design set. Change the owning detailed document, update this index when ownership or
the current baseline changes, and add an architecture decision record when the choice has durable
alternatives or tradeoffs. Record the authoritative component, effects on dependency direction,
determinism, validation, persistence or replay, remaining limitations, and acceptance evidence.
Do not duplicate the same specification across README, AGENTS, design documents, and configuration.

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

# 41. Current Implemented Baseline

This section is the current-state index. The [runtime milestone](milestones/runtime.md) and
[performance milestones](milestones/performance.md) remain normative. The README contains setup,
runtime, and controls; `AGENTS.md` owns validation commands.

## 41.1 Architectural ownership

| Concern | Authoritative implementation |
| --- | --- |
| Tagged control inputs and serialization | `src/airts/commands.py` |
| Public simulation facade, authoritative state, and tick order | `src/airts/simulation.py` |
| Direct, automation, and spatial command validation | `src/airts/systems/command_handlers.py` and `src/airts/systems/spatial_commands.py` |
| Automation schemas and deterministic geometry planning | `src/airts/automations.py` |
| Automation lifecycle, ownership, and behavior execution | `src/airts/systems/automation_lifecycle.py` and `src/airts/systems/automation_runtime.py` |
| Movement, collision, and blocked-unit recovery | `src/airts/systems/movement.py` |
| Combat targeting and projectile resolution | `src/airts/systems/combat.py` |
| Construction, production, economy, and enemy generation | Focused modules under `src/airts/systems/` |
| Entity profiles, maps, occupancy, visibility, and projectiles | Focused authoritative modules under `src/airts/world/` |
| Routing, steering support, and spatial indexing | Focused deterministic modules under `src/airts/navigation/` |
| Geometry and named spatial references | `src/airts/geometry.py` and `src/airts/spatial.py` |
| Versioned save/load and deterministic command replay | `src/airts/adapters/persistence.py` and `src/airts/adapters/replay.py` |
| Input, inspection, panels, and the explicit software renderer | `src/airts/presentation/app.py` |
| Native OpenGL frame construction and submission | `src/airts/presentation/opengl_renderer.py` |

The dependency direction remains simulation core outward to adapters. `Simulation` imports and
orchestrates internal systems; those systems have no runtime dependency back to the facade and do
not import Pygame or a model provider. UI actions submit the same command objects used by replay and
future language adapters. Renderer code reads authoritative state but does not advance or mutate
domain behavior. The public `airts.simulation.Simulation` import and facade remain stable.
The former top-level implementation paths remain compatibility re-exports for downstream callers;
AIRTS's own source imports the canonical package paths. Tests are grouped by intent under
`tests/unit/`, `tests/integration/`, `tests/movement/`, `tests/performance/`, and
`tests/architecture/`. Optional blocking human-inspection workloads live under
`tests/gui_scenarios/` in files that deliberately do not match pytest's normal discovery pattern.

## 41.2 Supported behavior

The bundled scenario is a validated 64 x 64 static grid using grass, road, forest, water, rock, and
bridge terrain. Current entities are scouts, light tanks, heavy tanks, builders, factories, repair
hubs, command centers, and resource generators.

The runtime currently supports:

* fixed 10 Hz deterministic simulation with a 1,000 FPS-ceiling, GPU-interpolated frontend and an
  invariant inverse-p99-frame-time acceptance contract whose required 1,000-unit software and
  hardware workloads pass the 100 Real FPS target on the reference Windows system;
* direct move, stop, hold, and explicit attack commands with manual override;
* point, polyline, rectangle, and freehand grounding; typed selection; region naming; whole-object
  geometry replacement; and route/region deletion;
* patrol, defend, production, construction, reinforcement, repair-and-return, and economy
  automations with inspectable lifecycle, priority, pause, resume, cancellation, and event history;
* weighted four-direction routing, collision-safe overflow and patrol formations, a deterministic
  large-force traffic lattice with fixed-unit anchors and topology-preserving bridge flow, local
  physical collision and push, opportunistic projectile combat, dense bit-mask visibility,
  resource income, production, and builder construction;
* versioned complete-state saves, deterministic replay verification, JSON Lines event export,
  configurable deterministic enemy generation, and custom map loading;
* a native OpenGL 3.3 default renderer with GPU-batched projectile and assembly feedback,
  fixed-tick position interpolation, persistent dynamic buffers, partial UI-texture uploads,
  selectable window resolution, and a 1,000 FPS submission ceiling, plus an explicitly selected
  software renderer with complete-frame caching.

Starting resources are one integer balance per owner. Each resource generator adds 1,000 resources
every ten simulation ticks. Ambient enemy generation defaults to one mobile enemy per second with a
cap of 100 and can be disabled or reconfigured. Save and replay documents preserve those settings
and reject incompatible schema versions.

The [performance milestone document](milestones/performance.md) separates 1,000-unit stress
contracts from capacity-valid crowd correctness scenarios. It includes a sustained genuine
500-vs-500 mixed-unit battle, a 999-unit focus workload, and 400-unit tiny-area and bridge scenarios
whose formations fit their maps while still exposing congestion and overlap.

## 41.3 Current implementation limitations

Builders do not gather resources, construction cannot be canceled for a refund, and there is no
technology tree, armor, cover, splash damage, missed shots, or tactical enemy AI. Visibility tracks
authoritative visible, explored, and unexplored cells but does not yet provide line-of-sight
occlusion, last-known enemy observations, or a fog overlay.

Geometry editing replaces an entire point, route, or region rather than individual vertices.
Map-defined semantic regions and multi-region automation semantics are not implemented. LM Studio
and other language providers, voice, MCP, scouting reports, multiplayer, Unity, and a map editor
remain outside the implemented phase.

The large-force controller deliberately uses deterministic coherent translation and orthogonal
slot reservations rather than continuous reciprocal-velocity optimization. Packed lane changes can
therefore look grid-like. A reservation changes logical ownership only after the body reaches its
current slot, and every physical step remains bounded by `speed * TICK_SECONDS`; this prevents
identity exchanges and stop-and-go jumps. Hostile idle units and held units are exact anchors,
while route bands and vacancy propagation let commanded traffic pass them. Defend automations that
share an owner and target coordinate one global collision-safe station set, including defenders
produced by separate factories. Large formations may still adopt a collision-safe reached position
inside their declared overflow envelope instead of churning forever toward one exact packed slot.
ORCA, native crowd code, and GPU simulation remain future options unless a checked-in sustained
workload demonstrates that the deterministic CPU kernel is insufficient.
