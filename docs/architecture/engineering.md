# Engineering

[Design](../design.md) owns scope and invariants; [AGENTS](../../AGENTS.md) owns workflow.

## Ownership

AIRTS is a modular monolith. Split code by responsibility and reason to change, not line count.
Keep one canonical implementation per responsibility; the public facade does not require private
forwarders for every system function. Local work calls its owner; retain shared hooks where useful.

Directories identify responsibilities: shared contracts, world state, navigation algorithms,
authoritative systems, persistence adapters, presentation, and bundled data. Package initializers
only declare packages; the root initializer explicitly exports the public entry points.

```text
src/airts/
|-- __init__.py                 Public Simulation and load_example_map exports
|-- __main__.py                 CLI argument parsing, startup and exit exports
|-- simulation.py               Public facade, authoritative state, command/tick orchestration
|-- contracts/                  Shared serializable data and control contracts
|   |-- __init__.py
|   |-- commands.py             Command schemas/codecs, authority precedence and validation
|   |-- automations.py          Parameters, legal transitions and formation planning
|   |-- spatial.py              Geometry targets, spatial references and selections
|   `-- events.py               Ordered event records, queries and JSON Lines export
|-- world/                      World models and lowest-level numeric execution support
|   |-- __init__.py
|   |-- map.py                  Cells, terrain/entity profiles, loading and occupancy
|   |-- skirmish.py             Fixed symmetric map and opening slot configuration
|   |-- opponents.py            Serializable opponent squads and economy memory
|   |-- entities.py             Runtime entities, projectiles and trajectory traces
|   |-- visibility.py           Per-owner visibility and exploration
|   `-- _numeric.py             Typed serial CPU compilation for domain kernels
|-- navigation/                 Routing and local collision algorithms
|   |-- __init__.py
|   |-- pathfinding.py          Weighted routes, shared fields and route budgets
|   |-- collision.py            Continuous clearance and local steering
|   `-- spatial_index.py        Neighbor and nearest-target queries
|-- systems/                    Validated commands and tick-driven authoritative behavior
|   |-- __init__.py
|   |-- command_handlers.py     Unit/automation command validation and application
|   |-- spatial_commands.py     Spatial reference and selection command application
|   |-- automation_control.py   Lifecycle, assignments, ownership and control claims
|   |-- automation_execution.py Patrol/defend/reinforcement/repair and formation coordination
|   |-- movement.py             Shared group-path planning and movement execution
|   |-- combat.py               Pursuit, firing, projectile impacts and damage
|   |-- production.py           Factory queues, spawning and linked automation work
|   |-- construction.py         Placement, builder queues and construction progress
|   |-- opponent_ai.py          Bounded economy planning, combat forecasts and squad decisions
|   |-- skirmish.py             Elimination, team results and match-state validation
|   `-- economy.py              Income, economy targets and ambient enemy spawning
|-- adapters/                   Versioned persistence and deterministic replay
|   |-- __init__.py
|   |-- persistence.py          Complete save/load validation and restoration
|   `-- replay.py               Command recording and replay verification
|-- presentation/               Window/input state, interface and scene presentation
|   |-- __init__.py
|   |-- app.py                  Event loop, input, UI drawing and frame composition
|   |-- lobby.py                Opening cover and pre-match player/team configuration
|   |-- rendering.py            Shared SceneView, backend choice and software scene caches
|   |-- metrics.py              Frame-pacing samples and Real FPS calculation
|   |-- opengl_renderer.py      GPU batches, interpolation, uploads and resource lifetime
|   |-- gpu_feedback.py         Cached world feedback, camera transforms and text atlas
|   `-- palette.py              Shared terrain, entity and projectile colors
`-- data/
    `-- phase1_map.json          Bundled starting map
```

Command schemas, conflict precedence and failure types belong together at the control boundary.
Geometry and spatial references share one representation and codec. Map footprints and occupancy
share cell semantics; runtime entities and projectiles share authoritative object serialization.
Visibility and navigation retain independent computational boundaries. Automation data, control
and execution remain separate because schema changes, ownership changes and tick behavior differ.
World entities store recovery request ages and phases that affect later trajectories. Navigation
derives contact graphs and release geometry; movement owns return intent, phase lifecycle and all
position/occupancy submission. Automations retain business station identities and control claims;
adapters serialize authoritative recovery state and validate its restored owner. Derived graphs,
leaders and numeric scratch state remain reconstructible navigation data.

SoftwareRenderer owns terrain, sprite, entity and path caches and reads SceneView instead of
application internals. The app retains input drafts, UI overlays and whole-window composition;
the GPU backend owns its buffers and resources. Metrics has no Pygame or application dependency.
Shared palette and independent render backends retain useful boundaries.

Runtime dependencies point inward: presentation/adapters -> facade -> systems -> navigation/world.
`contracts/` holds shared data and control contracts; commands and automations may read world profiles.
The package root contains only exports, the CLI entry and the public facade. Internal imports use
the owning modules directly; removed module paths do not have forwarding shims.
Public `airts.Simulation` and `airts.load_example_map` are direct re-exports with unchanged identity.
JSON command/save/replay formats contain tags and data, not Python module paths; this organization
does not require a format migration. Bundled map lookup stays package-relative and package-data
configuration remains owned by [pyproject.toml](../../pyproject.toml).
World does not import navigation; neither imports systems, adapters, presentation, or the facade.
Systems may reference `Simulation` under `TYPE_CHECKING` only. Pygame/ModernGL stay outside domain
code. Keep the public `airts.Simulation` identity; do not recreate transitional compatibility APIs.
Runtime module imports are acyclic. The facade schedules automation work; command handlers and
automation execution share group-path planning in `systems/movement.py`.

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
| `tests/performance/` | Workload behavior plus opt-in `performance` hardware/wall-clock contracts |
| `tests/architecture/` | Dependency direction, public facade, document links/discovery |
| `tests/gui_scenarios/` | Explicit, blocking visual inspection; excluded from default discovery |

Test at the lowest useful layer. Remove duplicate smoke checks when a retained scenario covers
their assertions; merge unique assertions into that scenario. Preserve independent failures and
use descriptive parameter IDs. Do not freeze obsolete filenames or private candidate ordering.
Architecture checks classify every source module and inspect every domain directory, including
`contracts/`, with nested/relative imports and type-only guards. Unclassified packages fail validation.

Keep fixtures local until independent tests need identical setup; never share mutable simulations.
Share reusable checks through helper modules, not imports from discovered `test_*.py` files;
[recovery checks](../../tests/integration/recovery_checks.py) own the independent swept-disc oracle.
Integration modules are organized by behavior rather than historical phase or milestone.
[Shared scenario constructors](../../tests/integration/scenarios.py) return fresh maps/simulations;
[persistence tests](../../tests/integration/test_persistence.py) own save/load continuation cases.
Renderer startup checks use small worlds; full-scene/timing tests retain their required scale.
Backend stress tests share `tests/scout_scenario.py`. Persistence scenarios retain distinct patrol,
repair, queues, spawns, and defense cases and compare complete exported state after continuation.
FPS tests use the common completed-frame probe; no average-throughput substitution. Gates belong to
[performance](../milestones/performance.md); change them only for an explicit acceptance decision,
not to make a failing cleanup pass. Mixed workload tests retain behavior assertions in default
validation and add timing budgets in their marked variant. Hardware FPS acceptance is opt-in;
the default Windows packaging validation is functional validation, not hardware performance evidence.
