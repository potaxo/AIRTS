# AIRTS Design

**Status:** Current implemented architecture and scope

AIRTS is a lightweight real-time strategy research environment for studying human-in-the-loop,
language-driven automation. The implemented runtime provides conventional RTS control and
persistent deterministic automations. Language-model integration remains roadmap work.

This document is the authoritative entry point for current behavior, architecture, scope, and
limitations. Detailed contracts live in the small set of owner documents linked below.

## Design invariants

- `airts.simulation.Simulation` owns authoritative state and advances it on deterministic fixed
  ticks.
- The simulation core runs headlessly and does not depend on Pygame, model providers, voice, or
  MCP.
- Every control source submits shared command objects. UI code, replay, and future language
  adapters do not mutate world state directly.
- The simulation validates geometry, ownership, capabilities, resources, paths, and state
  transitions before applying a command.
- Routing, formations, movement, collision, combat, visibility, and economy are deterministic for
  the same initial state, seed, and command sequence.
- Persistent automations are serializable, inspectable, pausable, resumable, cancelable, and
  traceable through structured events.
- Failures are explicit. Invalid commands and unreachable work do not partially mutate state or
  silently pretend to succeed.
- AIRTS remains a modular monolith with shallow packages and one canonical owner for each concern.

## Current architecture

```text
pygame-ce frontend       persistence / replay       future language adapter
         \                       |                         /
          +---------------- shared commands ----------------+
                                  |
                         Simulation facade
                                  |
               deterministic systems and automation runtime
                                  |
                  navigation, world, and domain contracts
```

Dependencies point toward domain code. Presentation and adapters may use the `Simulation` facade;
systems use navigation and world mechanisms; navigation and world modules do not depend on the
facade, systems, adapters, or presentation.

| Location | Responsibility |
| --- | --- |
| `src/airts/simulation.py` | Public facade, authoritative state, command dispatch, fixed-tick order |
| `src/airts/commands.py` | Serializable commands shared by all control sources |
| `src/airts/automations.py` | Automation schemas and deterministic formation planning |
| `src/airts/systems/` | Command handling and tick-driven domain behavior |
| `src/airts/navigation/` | Cached four-direction routing, collision geometry, and spatial indexing |
| `src/airts/world/` | Maps, entities, occupancy, visibility, and projectiles |
| `src/airts/adapters/` | Versioned persistence and deterministic replay |
| `src/airts/presentation/` | Input, inspection, and the Pygame software frontend |

`airts.Simulation` and `airts.simulation.Simulation` are the supported public entry points. Internal
modules have one canonical package path and are not parallel public facades.

## Implemented baseline

The bundled scenario is a validated 64 x 64 static grid. Terrain consists of grass, road, forest,
water, rock, and bridge cells. Entity kinds are scouts, light tanks, heavy tanks, builders,
factories, repair hubs, command centers, and resource generators.

The runtime supports:

- a deterministic 10 Hz simulation independent of presentation rate;
- one `pygame-ce` software frontend and fully headless domain tests;
- direct move, stop, hold, and explicit attack commands with manual override;
- point, polyline, rectangle, and freehand-polygon grounding;
- typed selection, region naming, whole-object geometry replacement, and route/region deletion;
- patrol, defend, production, construction, reinforcement, repair-and-return, and economy
  automations;
- automation priority, pause, resume, cancellation, target replacement, ownership, and event
  history;
- cached weighted four-direction routing and one deterministic local-steering movement pipeline;
- unique passable formation stations with stable retention while a station remains valid;
- collision, opportunistic projectile combat, current/explored visibility, resource income,
  production, builder construction, and configurable enemy generation;
- versioned complete-state saves, deterministic command replay, JSON Lines event export, and custom
  map loading.

Starting resources are one integer balance per owner. Each resource generator adds 1,000 resources
every ten simulation ticks. Ambient enemy generation defaults to one mobile enemy per second with a
cap of 100 and can be disabled or reconfigured.

## Current limitations

The runtime intentionally remains a research prototype:

- Language providers, LM Studio integration, voice, MCP, and model-generated commands are not
  implemented.
- Scouting reports, last-known enemy observations, line-of-sight occlusion, and a fog overlay are
  not implemented. Visibility tracks current and explored cells.
- Map-defined semantic regions and multi-region automation semantics are not implemented.
- Geometry editing replaces a complete point, route, or region rather than individual vertices.
- Builders do not gather resources, construction has no refund flow, and there is no technology
  tree.
- Combat has no armor, cover, splash damage, missed shots, or tactical enemy AI.
- The movement controller is deterministic local steering, not ORCA or another optimal reciprocal
  collision solver. Dense forces can queue and deform around narrow passages.
- There is no map editor, multiplayer, Unity frontend, air/naval combat, dynamic terrain, or
  unrestricted generated code.

## Document map

| Concern | Authoritative document |
| --- | --- |
| Current vision, invariants, package ownership, scope, and baseline | This document |
| Simulation, commands, automations, world state, persistence, and presentation | [Core architecture](architecture/core.md) |
| Routing, formations, steering, collision, and movement limitations | [Movement architecture](architecture/movement.md) |
| Machine-independent benchmark and profiling guidance | [Performance guidance](performance.md) |
| Research questions and unimplemented phases | [Research roadmap](roadmap.md) |
| Consequential decisions and tradeoffs | [Architecture decisions](decisions/README.md) |
| Setup, runtime, controls, and code entry points | [README](../README.md) |
| Contributor workflow and required validation | [AGENTS.md](../AGENTS.md) |
| Dependencies and tool configuration | [pyproject.toml](../pyproject.toml) |

## Documentation policy

Current architecture documents use present tense and describe implemented behavior. Future work
belongs in the roadmap. Tests own executable edge cases and numerical regression fixtures; design
documents record durable invariants, ownership, limitations, and the evidence category that protects
them. Add an architecture decision record only when a consequential choice has durable alternatives
or tradeoffs.

Keep each fact in one authoritative place and link to it elsewhere. When behavior or architecture
changes, update its owner document in the same change.
