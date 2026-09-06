# AIRTS Engineering Architecture

This document owns repository structure, dependency direction, validation strategy, and durable engineering-change policy.

[Design index](../design.md) · [Roadmap](../roadmap.md)

---

# 30. Development Philosophy

Define invariants, implement one bounded vertical slice, test its behavior, and revise the design
from evidence. Prefer clear types and focused functions over speculative factories, registries,
or inheritance hierarchies. The design records useful contracts rather than every class or method.

---

# 31. Engineering Change Policy

`AGENTS.md` owns contributor workflow and validation commands. Update the owning design document
when architecture, dependencies, scope, behavior, performance strategy, or limitations change.
Record the changed contract, important tradeoffs, and relevant acceptance tests; discuss effects
on authority, determinism, or persistence when they apply. A behavior-preserving cleanup needs a
concise ownership/tradeoff note, not a new milestone or a repeated checklist of unaffected systems.

Update `docs/design.md` when document ownership or the implemented baseline changes. Add an ADR
when a consequential choice has durable alternatives. README owns setup and controls;
`pyproject.toml` owns dependencies. Link to these owners rather than restating their rules.

## 31.1 Module boundary and refactoring policy

AIRTS is a modular monolith. `airts.simulation.Simulation` is the stable public facade,
authoritative state owner, and tick orchestrator. Authoritative world state types live under
`airts.world`; routing and spatial-query mechanisms live under `airts.navigation`; cohesive
simulation behaviors live under `airts.systems`; persistence and replay live under
`airts.adapters`; and Pygame/OpenGL code lives under `airts.presentation`. Runtime dependencies
point from presentation and adapters toward the facade, from the facade toward systems, and from
systems toward navigation, world, and stable top-level contracts. World and navigation code never
depend on systems, adapters, presentation, or the facade.

Split modules by responsibility, invariant, and reason to change rather than by a fixed line
count. A large cohesive module may be acceptable; a smaller module with unrelated responsibilities
is not. Avoid generic dumping grounds such as `utils`, `helpers`, or `manager`, and introduce a
subpackage only when it represents a durable boundary.

Refactor one responsibility at a time behind stable interfaces. Establish the test baseline first,
preserve the documented `airts.Simulation` facade, commands, persistence and replay schemas, tick
and event order, and seeded determinism, then run focused tests before the full suite. Internal
implementation paths are not parallel public facades: moved code has one canonical package owner.
A flag-day rewrite requires an explicit milestone and migration contract.

## 31.2 Repository ownership

| Location | Responsibility |
| --- | --- |
| `src/airts/world/` | Authoritative entities, maps, occupancy, visibility, and projectiles |
| `src/airts/navigation/` | Deterministic routing, steering support, and spatial indexing |
| `src/airts/systems/` | Command execution and tick-driven domain behavior |
| `src/airts/adapters/` | Persistence and replay at the simulation boundary |
| `src/airts/presentation/` | Input, inspection, software rendering, and OpenGL rendering |
| `tests/unit/` | Isolated domain-component contracts |
| `tests/integration/` | Facade, adapter, milestone, and cross-system behavior |
| `tests/movement/` | Collision and swarm behavior with specialized fixtures |
| `tests/performance/` | Explicit workload and frame-rate contracts |
| `tests/architecture/` | Dependency and documentation structure policies |

Moved code has one canonical package path under [ADR 0005](../decisions/0005-canonical-module-ownership.md).
The stable public facade does not require mirroring every system function as a private method.
Command dispatch and tick orchestration call their owners directly; system-local calls stay local.
Shared cross-system hooks remain where removing them would expand coupling. Unused forwarding
methods and obsolete algorithms are removed under [ADR 0007](../decisions/0007-simplify-internal-delegation.md).
Public commands, state schemas, event order, and tick behavior are preserved.

Architecture tests enforce dependency direction (including nested and relative imports), keep
Pygame and OpenGL outside the domain, reject removed compatibility imports, and preserve the public
facade. Documentation checks verify local links and that owner documents are reachable from the
index. Filenames and a fixed global section count are not architectural invariants.

The unit, integration, movement, and performance suites cover the delegation cleanup, including
save/load continuation and replay equality. Hardware performance thresholds remain unchanged.
Optional blocking inspection workloads live under `tests/gui_scenarios/` outside pytest discovery.

## 31.3 Test ownership and fixture scope

Test a contract at the lowest useful layer. Automation lifecycle and command serialization belong
in unit tests; facade-driven patrol behavior belongs in simulation integration tests. The command
codec matrix covers every command kind, so UI tests need not repeat isolated codec assertions.
Parameterized validation cases keep separate descriptive IDs and expected failures.

Renderer correctness and mocked startup/error paths live in
`tests/integration/test_opengl_presentation.py`. Startup and overlay-key checks use small worlds;
full-scene and timing contracts retain their required scale. Software and OpenGL stress tests use
the same fresh 1,000-scout map from `tests/scout_scenario.py`, with no shared mutable simulation.
Timing workloads remain under `tests/performance/`; their thresholds and frame counts are unchanged.

Persistence scenarios remain distinct for patrol, repair suspension, production queues, ambient
spawns, and linked defenses. They share a full exported-state continuation assertion that includes
events, command history, counters, and transient movement state rather than repeating partial
snapshot checks. Merge repeated setup and assertions without collapsing independent failures into
one long scenario or replacing behavior checks with source-text checks.

The FPS acceptance helper is exercised with explicit completed-frame timestamps: a stuttering
sample must fail 100 Real FPS even when average throughput exceeds it. This replaces a source-text
blacklist that could be satisfied by a comment or broken by harmless renaming; benchmark calls
continue to use the same measured acceptance helper and invariant p99 rule.
