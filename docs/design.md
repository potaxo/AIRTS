# AIRTS design

AIRTS is a Windows-native research prototype for human-supervised RTS automation.
Players supply intent and geometry; deterministic code validates and executes it.
Direct control and persistent automations are implemented. Language-model control is planned.

## Invariants

- `airts.Simulation` is the public facade, authoritative state owner, and tick orchestrator.
- All control sources use the same validated commands and automation interfaces.
- The headless core has no renderer or provider dependency. The same initial state, seed,
  and command sequence reproduce state and events at a fixed 10 Hz.
- Automations retain inspectable ownership, lifecycle, provenance, and failure reasons.
  Humans can override, modify, pause, resume, or cancel them.
- Renderers only read domain state. Presentation interpolation cannot affect simulation or replay.

## Document owners

Read this index first, then the owners relevant to the task. Each contract has one owning document;
cross-references provide context without creating a second specification.

| Concern | Owner |
| --- | --- |
| Scope, invariants, and current baseline | This document |
| Module boundaries and change/test policy | [Engineering](architecture/engineering.md) |
| World, commands, automations, persistence, and interaction | [Runtime](milestones/runtime.md) |
| Rendering, FPS measurement, and crowd acceptance | [Performance](milestones/performance.md) |
| Unimplemented phases and research | [Roadmap](roadmap.md) |
| Setup, CLI, and controls | [README](../README.md) |
| Contributor workflow and validation | [AGENTS](../AGENTS.md) |
| Dependencies and tool settings | [pyproject.toml](../pyproject.toml) |

## Current baseline and limits

The bundled map is a static 64 x 64 weighted grid. Scouts, light/heavy tanks, builders,
ordinary buildings, movement, combat, economy, construction, production, spatial editing,
patrol/defend/reinforcement/repair automations, JSON saves, and deterministic replay exist.
OpenGL 3.3 is the default; software rendering is explicit. Performance targets and their
measurement limits belong to the performance document, not a blanket claim of passing FPS.

Movement currently combines shared routing, local collision/pushing, and a separate large-force
reservation controller. Packed motion can look grid-like; passing selected scenarios does not
establish general crowd correctness. A replacement must explicitly define movement and save/replay
migration contracts.

No language provider, voice, MCP, scouting reports, map editor, multiplayer, Unity, dynamic terrain,
semantic map regions, or multi-region automation is implemented. Visibility has no occlusion,
last-known enemy memory, or fog overlay. There is no technology tree, armor, cover, splash damage,
missed shots, tactical enemy AI, builder gathering, construction refund, or vertex-level editing.
