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

## Vision and invariants

AIRTS studies human-supervised RTS automation: players supply strategic intent through language
and exact geometry through direct interaction; deterministic code validates and executes commands
and persistent behaviors. Conventional controls remain available. The current build has direct
control and automations; language-model integration remains planned.

- `Simulation` owns world state and tick order, including geometry, paths, resources, visibility,
  combat, production, and state transitions.
- Every control source uses shared command and automation interfaces. Model output is a proposal
  and must pass deterministic validation before affecting the world.
- The core runs without a graphical window or provider connection and preserves results for the
  same initial state, seed, and command sequence.
- Persistent automations are serializable and inspectable, with lifecycle, ownership, provenance,
  and explicit failure evidence. Humans can modify, pause, resume, or cancel them.
- Initial complexity stays bounded: static terrain, a small ground-unit set, simple combat and
  visibility, no multiplayer, and no unrestricted generated code or continuous LLM tick control.

The conceptual flow is human interaction → spatial grounding → optional language interpretation
→ command validation and automation → deterministic simulation → observations and evaluation.
Runtime dependencies point inward from presentation and adapters to the simulation facade, then
to systems, navigation, world types, and shared contracts. The simulation does not import its UI
or a language provider. Package ownership and refactoring policy live in the
[engineering architecture](architecture/engineering.md).

## Reading and maintaining the specification

The current baseline below and implemented milestone documents take precedence over future-facing
language in the roadmap and architecture documents. Roadmap terms such as *should*, *may*, and
*eventually* do not claim implemented capability. Research proposals under `docs/human/` are
non-normative historical material.

[AGENTS.md](../AGENTS.md) owns contributor workflow and validation;
[engineering change policy](architecture/engineering.md#31-engineering-change-policy) owns when
and how to update this design set. Each fact should have one authoritative owner.

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
| Movement and blocked-unit recovery | `src/airts/systems/movement.py` |
| Combat targeting and projectile resolution | `src/airts/systems/combat.py` |
| Construction, production, economy, and enemy generation | Focused modules under `src/airts/systems/` |
| Entity profiles, maps, occupancy, visibility, and projectiles | Focused authoritative modules under `src/airts/world/` |
| Routing, collision and steering support, and spatial indexing | Focused deterministic modules under `src/airts/navigation/`, including `navigation/collision.py` |
| Geometry and named spatial references | `src/airts/geometry.py` and `src/airts/spatial.py` |
| Versioned save/load and deterministic command replay | `src/airts/adapters/persistence.py` and `src/airts/adapters/replay.py` |
| Input, inspection, panels, and the explicit software renderer | `src/airts/presentation/app.py` |
| Native OpenGL frame construction and submission | `src/airts/presentation/opengl_renderer.py` |

`Simulation` remains the state owner and public facade (`airts.Simulation` is the same class).
Systems use type-only references to it. UI actions submit the same commands used by replay and
future language adapters; renderers only read domain state. Private forwarding methods remain
only where shared collaborators need them; local work calls the owning function directly.
See [ADR 0007](decisions/0007-simplify-internal-delegation.md) for the audit cleanup and
[engineering architecture](architecture/engineering.md) for package and test ownership.

## 41.2 Supported behavior

The bundled scenario is a validated 64 x 64 static grid using grass, road, forest, water, rock, and
bridge terrain. Current entities are scouts, light tanks, heavy tanks, builders, factories, repair
hubs, command centers, and resource generators.

The runtime currently supports:

* fixed 10 Hz deterministic simulation with a 1,000 FPS-ceiling, GPU-interpolated frontend and an
  inverse-p99-frame-time acceptance contract targeting 100 Real FPS on the required 1,000-unit
  software and hardware workloads; results depend on the reference system and measured workload;
* direct move, stop, hold, and explicit attack commands with manual override;
* point, polyline, rectangle, and freehand grounding; typed selection; region naming; whole-object
  geometry replacement; and route/region deletion;
* patrol, defend, production, construction, reinforcement, repair-and-return, and economy
  automations with inspectable lifecycle, priority, pause, resume, cancellation, and event history;
* weighted four-direction routing, collision-safe overflow and patrol formations, stable shared
  defense stations, an incremental deterministic large-force traffic lattice with fixed-unit
  anchors and topology-preserving bridge flow, local physical collision and push, opportunistic
  projectile combat, dense bit-mask visibility, resource income, production, and builder
  construction;
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

Large-force movement uses coherent translation and orthogonal slot reservations, so packed lane
changes can look grid-like. Stable incremental formation and traffic identities, speed bounds,
fixed-unit anchors, and collision-safe arrival envelopes are specified in
[ADR 0006](decisions/0006-stable-incremental-crowd-state.md) and the
[performance milestones](milestones/performance.md). ORCA, native crowd code, and GPU simulation
remain future options requiring measured evidence.
