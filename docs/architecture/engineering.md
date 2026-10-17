# Engineering

[Design](../design.md) owns priorities; [AGENTS](../../AGENTS.md) owns contributor workflow.

## Ownership

AIRTS is a modular monolith with one canonical implementation per responsibility. Split by reason
to change, not line count; avoid facade forwarders, generic managers and transitional import shims.

| Owner | Responsibility |
| --- | --- |
| Package root | Explicit public exports, CLI and `Simulation` orchestration |
| `contracts/` | Serializable commands, automations, spatial input and events |
| `world/` | Maps, occupancy, runtime entities, visibility, numeric kernels and opponent memory |
| `world/units/` | Immutable profiles, stable tags and explicit production relationships |
| `navigation/` | Routing fields/budgets, spatial queries and local collision geometry |
| `navigation/collision/` | Shared disc certificates (`geometry`), bounded queries (`neighbors`), joint release (`release`), anchor recovery (`returning`), tick solver (`solver`) and dependency-aware compilation (`_compile`) |
| `systems/` | Command application and focused runtime services; each mutation has one owning module |
| `adapters/` | Versioned persistence and replay diagnostics |
| `presentation/` | Window/input, lobby, UI, software/GPU rendering and frame metrics |
| `presentation/visuals/` | Explicit appearance bindings, packaged images/atlas, bounded software transforms, terrain patterns, visual facing and GPU sprite records; [appearance contract](appearance.md) |
| `presentation/ui/` | Shared visual roles and frames (`theme`), font/text primitives (`drawing`), selection/production controls (`selection`), automation inspection/scroll (`automations`), settings/help (`menus`) and save browser (`saves`) |
| `data/` | Bundled maps; package-data configuration belongs to `pyproject.toml` |

Runtime dependencies point inward: **presentation/adapters -> facade -> systems -> navigation/world**.
Contracts may read world profiles. World does not import navigation; neither imports systems,
adapters, presentation or the facade. Systems reference `Simulation` only under `TYPE_CHECKING`.
Imports are acyclic; Pygame/ModernGL remain outside domain code. Package initializers declare
packages; the root directly re-exports `Simulation` and `load_example_map` with unchanged identity.
Internal calls import their owner directly. System modules do not import another system's private
symbols, and retired facade forwarders must not reappear. JSON tags carry data, not Python module paths.

### Definitions and execution

Each mobile kind has one keyword-based immutable profile; buildings share the same schema.
The explicit catalog owns tag/profile and producer bindings, never dynamic plugin discovery.
Profile construction rejects invalid health, footprint, motion, collision, combat, economy,
production, construction, repair and reclamation values. Armed profiles require positive range and
projectile speed; mobile kinds require positive cost and production time. Mobile build, repair and
reclaim eligibility derives from profile capabilities rather than kind-name checks. Each mobile
profile declares its mass;
buildings remain hard obstacles regardless of mass. The catalog's `BUILDABLE_KINDS` is shared by
construction validation and the Builder controls. Artillery and turrets have independent definitions.
Add a kind through its definition, catalog/producer binding and presentation palette; opponent
composition remains an explicit AI policy. Simulation behavior stays in shared systems, not
per-kind executors. Images/colors belong to presentation.

Command validation and precedence belong at the control boundary; spatial targets share codecs.
Navigation owns contact geometry; movement owns intent/recovery lifecycle and all position/occupancy
submission. Adapters restore authoritative recovery state; scratch, graphs and leaders are derived.
`automation_lifecycle.py` owns creation, transitions and claims; `automation_assignments.py` owns
membership and cleanup; `automation_runtime.py` owns entity initialization and shared formations.
`automation_control.py` composes these for player pause/resume/cancel; `automation_execution.py`
advances tasks. `production.py` owns queues, payment and progress; `production_orders.py` assigns
new units. `unit_orders.py` owns direct FIFO admission and dispatch across movement, attack and repair;
dispatch reuses the command boundary for target work. Shared leaves own validation (`validation.py`), IDs (`entity_registry.py`), spawn clearance
(`spawning.py`) and firing approach geometry (`combat_geometry.py`).
`repair_positions.py` owns service geometry/capacity without importing repair controllers;
`reclamation.py` owns builder approach and the single removal/refund transaction. Building-line
draft geometry belongs to `presentation/build_placement.py` and emits ordinary construction commands.

SoftwareRenderer owns scene caches and reads SceneView; the app owns input drafts, overlays and
composition. Camera owns zoom/offset and bounded transforms independently of input. UI panels use
scoped view protocols rather than importing AirtsApp or a render backend, emit clickable action
records, and leave command dispatch to the app. AutomationPanelState owns local inspection, tabs,
scroll and hit geometry; authoritative automation data remains in Simulation. UiPainter owns fonts
and text/button drawing, including the font handle read by the GPU text atlas.
GPU resources/buffers belong to its backend; metrics has no Pygame/app dependency.
Shared palette and independent backends retain separate owners.
Battle appearance and facing are presentation concerns; GPU record packing has a shared leaf owner
in `gpu_shapes.py`. The core never loads PNGs, stores camera state or advances visual headings.
Art sources and the offline authoring tool are owned by the appearance contract.

Collision geometry and queries are shared leaves. Release and return algorithms import those
leaves; the solver orchestrates them. Movement packs each profile's mass into its existing reusable
numeric workspace and remains the only position/occupancy submitter. Save and replay consequences
of mass-weighted response belong to the persistence policy.

Contact kernels fingerprint their source package once at import and extend each Numba cache index
stamp through the numeric adapter. A helper edit invalidates inlined callers even when the caller
file is unchanged; unchanged sources retain disk-cache reuse and Numba's existing source/user/frozen
cache placement. The adapter targets the declared Numba minor version and has a cross-process
regression that holds source size/mtime constant while changing helper bytes. This prevents stale
compiled contact behavior after splitting kernels across files.

## Change policy

Update each fact's owner, linking instead of repeating contracts. Keep implementation details in
code and exact acceptance fixtures in tests. Retain historical rationale only for a surviving
tradeoff; proposals are not requirements. New durable concerns may get a focused document linked
directly from design. Use its task index to read only relevant documents or sections.

Cleanup preserves commands, schemas, observable behavior and hardware thresholds. A movement
rewrite defines replacement scope, current save format/rejection and replay impact under
[persistence policy](../milestones/persistence.md#save-reliability-and-replay-policy).
Do not add unrelated restructuring or a second public facade.

Execution changes may alter arithmetic order, scheduling, trajectories or event order while
preserving gameplay contracts. Keep one authoritative writer; workers have explicit input ownership
and return results through controlled submission. Rendering reads coherent state. Races, lost
commands, duplicate resource transactions, invalid references and partial saves remain failures.
Numerical approximations need bounded error and relevant finite-value, clearance, speed and gameplay
checks. Measure responsiveness and complete workloads instead of assuming optimizations are faster.

## Test ownership

| Suite | Contract |
| --- | --- |
| `tests/unit/` | Isolated domain components and codecs |
| `tests/integration/` | Facade, adapters, controls and rendering behavior |
| `tests/performance/` | Workload behavior plus opt-in hardware/wall-clock acceptance |
| `tests/architecture/` | Dependency direction, public facade and documentation discovery/links |
| `tests/gui_scenarios/` | Explicit blocking inspection, excluded from default discovery |

Test at the lowest useful layer. Reuse meaningful cases, merge unique assertions and remove
duplicate smoke checks. Do not freeze obsolete filenames or private ordering. Architecture checks
cover every source package, nested/relative imports and type-only guards; unclassified modules fail.
Keep fixtures local until independent tests need the same setup, always returning fresh simulations.
Use helpers rather than importing discovered tests: [scenarios](../../tests/integration/scenarios.py),
[recovery oracle](../../tests/integration/recovery_checks.py), and `tests/scout_scenario.py` own shared inputs.

Persistence tests retain distinct patrol, repair, queues, spawn and defense continuation cases.
Save/replay tests cover current formats and meaningful corruption/failure paths. Keep a generic
unsupported-format check rather than enumerating historical versions or freezing retired-field
compatibility and old-version diagnostic wording. Remove superseded tests when their behavior is
retired, and keep shared contracts in the lowest useful suite instead of repeating smoke assertions.
Current exact continuation/replay comparisons may become meaningful restoration, gameplay and
concurrency checks when an intentional execution change needs that; preserve unique failure coverage
and document diagnostic support. Do not silently skip failures. Renderer startup uses small worlds;
stress/timing scenarios keep full scale. FPS uses the shared completed-frame probe.
[Performance](../milestones/performance.md) owns gates and hardware evidence; [AGENTS](../../AGENTS.md#validation)
owns required commands. Full workload behavior remains in default validation even when timing is opt-in.
