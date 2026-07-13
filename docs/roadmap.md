# AIRTS Research Roadmap

This document owns research questions, planned scope, future phases, demonstrations, assumptions,
and open questions. Roadmap statements are not claims about implemented behavior. See the
[design index](design.md) for the current baseline and authoritative document map.

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

