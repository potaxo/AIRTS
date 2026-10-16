# AIRTS design

AIRTS is a Windows-native RTS game with human-supervised automation.
Players supply intent and geometry; validated commands execute their orders.
Direct control and persistent automations are implemented. Language-model control is planned.

## Product priorities

The current release priority is a playable single-player game: responsive controls, smooth
presentation, reliable saves, safe world state, and understandable automations that players can
correct. Research evaluation is a secondary opportunity after useful gameplay exists, rather
than a prerequisite or required development sequence. Performance improvements need workload
evidence; relaxing replay requirements alone does not establish a speedup.

## Invariants

- `airts.Simulation` is the public facade, authoritative state owner, and tick orchestrator.
- All control sources use the same validated commands and automation interfaces.
- The headless core has no renderer or provider dependency and advances at a fixed 10 Hz.
- World updates preserve valid state, collision safety, command authority, and resource accounting.
  Supported saves restore coherent authoritative state and player intent.
- Automations retain inspectable ownership, lifecycle, provenance, and failure reasons.
  Humans can override, modify, pause, resume, or cancel them.
- Renderers only read domain state. Presentation interpolation cannot affect authoritative state.

Exact trajectory and event reproduction across worker counts, hardware, or versions is not a
release gate. Seeded repeatability remains useful for debugging where inexpensive; concurrency
and numerical optimizations may change individual outcomes while preserving gameplay contracts.
[Persistence](milestones/persistence.md#save-reliability-and-replay-policy) owns save reliability and the
optional command-replay diagnostic policy.

## Document owners

Read this index first, then the owners relevant to the task. Each contract has one owning document;
cross-references provide context without creating a second specification.

| Concern | Owner |
| --- | --- |
| Scope, invariants, and current baseline | This document |
| Module boundaries and change/test policy | [Engineering](architecture/engineering.md) |
| Battle sprites, terrain, interface style and visual facing | [Appearance](architecture/appearance.md) |
| Commands, economy, construction and interaction | [Runtime](milestones/runtime.md) |
| Movement, routing, collision, combat and repair | [Movement](milestones/movement.md) |
| Save/load, replay diagnostics and compatibility | [Persistence](milestones/persistence.md) |
| AI, opening teams, elimination and results | [Opponents](milestones/opponents.md) |
| Rendering, FPS measurement, and crowd acceptance | [Performance](milestones/performance.md) |
| Deferred features and optional research | [Roadmap](roadmap.md) |
| Setup, CLI, and controls | [README](../README.md) |
| Contributor workflow and validation | [AGENTS](../AGENTS.md) |
| Dependencies and tool settings | [pyproject.toml](../pyproject.toml) |

For input, selection or menus, read Runtime's [Interaction](milestones/runtime.md#interaction)
and README controls; add Performance only when changing drawing. Save/load changes need Persistence,
not collision internals. Movement changes need Movement and the relevant Performance gate. Read
Engineering for boundary/test changes; Roadmap only for explicitly requested future features.

## Current baseline and limits

The default game uses a static, exactly mirrored 320 x 320 river map with four configurable
starting slots, resource sites, rule-based opponents with persistent squads, economy planning,
combat estimates and team results. The original
64 x 64 example map remains available for existing tests and the explicit legacy sandbox.
Scouts, light/heavy tanks, artillery, builders, defense turrets,
ordinary buildings, movement, combat, economy, construction, production, spatial editing,
patrol/defend/reinforcement/repair automations, JSON saves, and command-replay verification exist.
OpenGL 3.3 is the default; software rendering is explicit. Performance targets and their
measurement limits belong to the performance document, not a blanket claim of passing FPS.

No language provider, voice, MCP, scouting reports, map editor, multiplayer, Unity, dynamic terrain,
semantic map regions, or multi-region automation is implemented. Visibility has no occlusion,
last-known enemy memory, or fog overlay. There is no technology tree, armor, cover, splash damage,
missed shots, learned enemy policies, individual kiting, builder gathering, vertex-level editing.
