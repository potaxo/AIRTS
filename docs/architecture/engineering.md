# Engineering

[Design](../design.md) owns scope and invariants; [AGENTS](../../AGENTS.md) owns workflow.

## Ownership

AIRTS is a modular monolith. Split code by responsibility and reason to change, not line count.
Keep one canonical implementation per responsibility; the public facade does not require private
forwarders for every system function. Local work calls its owner; retain shared hooks where useful.

| Location under `src/airts/` | Responsibility |
| --- | --- |
| `simulation.py` | Public facade, state, command boundary, tick order |
| `commands.py`, `control.py`, `validation.py` | Shared input and validation contracts |
| `automations.py`, `geometry.py`, `spatial.py`, `events.py` | Schemas, geometry, references, events |
| `_numeric.py` | Typed serial CPU compilation shared by domain owners |
| `world/` | Entity/map profiles, occupancy, projectiles, visibility |
| `navigation/` | Routing, collision support, spatial indexing |
| `systems/` | Command handling and tick-driven movement, automation, combat, production, construction, economy |
| `adapters/` | Save/load and replay |
| `presentation/` | Input, UI/software renderer in `app.py`; GPU renderer in `opengl_renderer.py` |

Runtime dependencies point inward: presentation/adapters -> facade -> systems -> navigation/world.
World does not import navigation; neither imports systems, adapters, presentation, or the facade.
Systems may reference `Simulation` under `TYPE_CHECKING` only. Pygame/ModernGL stay outside domain
code. Keep the public `airts.Simulation` identity; do not recreate transitional compatibility APIs.

## Change policy

Update only the document that owns a changed contract. Keep implementation details in code,
parameter values in their definitions, and executable acceptance scenarios in tests. Historical
rationale should explain a surviving tradeoff once, not repeat every patch or validation run.
A consequential new choice needs a concise decision/tradeoff note in its owner, not another file
unless it introduces a separate durable concern. Roadmap proposals are not current requirements.

For cleanup, preserve commands, schemas, tick/event order, determinism, and hardware thresholds.
A movement rewrite needs an explicit replacement scope and save/replay migration decision.
Do not introduce generic managers, registries, parallel facades, or unrelated restructuring.

## Test ownership

| Suite | Contract |
| --- | --- |
| `tests/unit/` | Isolated domain components and codecs |
| `tests/integration/` | Facade, adapters, controls, and renderer behavior |
| `tests/performance/` | Measured workload and timing contracts |
| `tests/architecture/` | Dependency direction, public facade, document links/discovery |
| `tests/gui_scenarios/` | Explicit, blocking visual inspection; excluded from default discovery |

Test at the lowest useful layer. Remove duplicate smoke checks when a retained scenario covers
their assertions; merge unique assertions into that scenario. Preserve independent failures and
use descriptive parameter IDs. Do not freeze obsolete filenames or private candidate ordering.
Architecture checks inspect real imports, including nested/relative imports and type-only guards.

Keep fixtures local until independent tests need identical setup; never share mutable simulations.
Integration modules are organized by behavior rather than historical phase or milestone.
[Shared scenario constructors](../../tests/integration/scenarios.py) return fresh maps/simulations;
[persistence tests](../../tests/integration/test_persistence.py) own save/load continuation cases.
Renderer startup checks use small worlds; full-scene/timing tests retain their required scale.
Backend stress tests share `tests/scout_scenario.py`. Persistence scenarios retain distinct patrol,
repair, queues, spawns, and defense cases and compare complete exported state after continuation.
FPS tests use the common completed-frame probe; no average-throughput substitution or lower gates.
