# AIRTS

AIRTS is a small research environment for human-in-the-loop, language-driven RTS
automation. Phase 5 adds deterministic resources, paid production, generator-driven
economic automation, simple combat, and emergency retreat-and-repair behavior. It does
not add a language model yet.

The authoritative project scope and architecture are defined in
[`docs/design.md`](docs/design.md).

## Setup

AIRTS is developed in WSL2 Ubuntu with Python 3.13. From the repository root:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

The project uses `pygame-ce`; do not install the separate `pygame` package.

Development plus packaging
```bash
.venv/bin/python -m pip install -e ".[dev,package]"
```

## Run

```bash
.venv/bin/python -m airts
```

The bundled scenario is a validated 64 × 64 map with opposing forces, support and
economic buildings, roads, forest, a river, and a bridge. A custom map can be
supplied with `--map PATH`.
Structured events can be written when the application exits:

```bash
.venv/bin/python -m airts --event-log events.jsonl
```

Complete versioned simulation state can be saved and continued:

```bash
.venv/bin/python -m airts --save-state state.json
.venv/bin/python -m airts --load-state state.json
```

Tick-stamped commands can also be captured and deterministically verified:

```bash
.venv/bin/python -m airts --write-replay replay.json
.venv/bin/python -m airts --replay replay.json
```

### Controls

| Input | Action |
| --- | --- |
| `1` | Selection mode; click entities or regions, or drag friendly units; `Shift` toggles additions |
| `2` | Place a point patrol target |
| `3` | Add line vertices; press `Enter` to finish the route |
| `4` | Drag a rectangular patrol area |
| `5` | Draw a freehand patrol area |
| `A` | Create a patrol from the selected units and current target |
| `D` | Create a defend automation from selected units and current target |
| `P` | Produce three light tanks from exactly one selected factory |
| `R` | Repair selected units and return them to suspended assignments |
| `G` | Develop the economy with selected resource generators until 100 more resources |
| `S` / `H` | Stop selected units or hold their current position |
| `N` | Name or rename exactly one selected region; type the name and press `Enter` |
| `E` | Edit the selected point, route, or region by redrawing it |
| `Delete` | Delete one selected user region and explicitly cancel automations using it |
| `F5` / `F9` | Save or load `airts-quicksave.json` |
| `F2` | Reset the bundled/current starting scenario |
| `U` | Replace the inspected patrol/defend target with the active spatial target |
| `[` / `]` | Decrease or increase the inspected automation priority |
| Right-click | Move, or attack an enemy under the cursor |
| `Space` | Pause or resume simulation time |
| `Esc` | Clear the current spatial target or draft |

Point, line, rectangle, and freehand tools return to selection mode after one completed
operation. Drawing creates stable point, route, and region IDs. Named regions are persistent and
must have unique names; overlapping regions are allowed. Click an automation card to
inspect its provenance, owner, priority, reason, timestamps, and entities. The panel
also provides pause/resume and cancel controls, and the event view includes validation
reasons where available. Production target counts and reinforcement minimums are
editable through the shared Python command interface.

## Architecture

The core simulation modules are authoritative and do not import Pygame. Map, geometry,
entity, occupancy, pathfinding, visibility, command, automation, validation, control,
persistence, replay, and event modules are independently testable. The Pygame app
converts user input into the same tagged commands used by tests and future control
sources. Simulation advances at a fixed 10 ticks per second independently of rendering.

Every automation follows an explicit lifecycle from proposal and validation through
active, waiting, paused, blocked, and terminal states. Control precedence is direct
human input, emergency repair, explicit priority, and then the newer equal-priority
instruction. Units have one current assignment and may retain one suspended assignment
while repairing.

Factories reserve unit costs before building and wait visibly when funds are insufficient.
Resource generators produce deterministic passive income every second; an economy automation
monitors progress toward a target and exposes it through the normal lifecycle. Defend
behavior maintains grounded positions and engages nearby enemies, reinforcement transfers
eligible units to another automation, and repair selects destinations by
repair-hub/factory/command-center order and valid path cost before restoring the original
assignment.

Movement uses deterministic four-direction A* with terrain costs. Terrain and building
footprints are hard obstacles. Group destinations and patrol starts are distributed, and a
unit blocked for one second chooses a deterministic free sidestep before replanning. The UI
displays the calculated path rather than deriving one itself.

Visibility is stored separately for each owner as visible, explored, or unexplored cells.
This phase exposes the authoritative information state but deliberately does not hide map
or entity rendering.

## Test and validate

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/python -m pytest
.venv/bin/python -m pip check
```

For a headless graphical startup/render smoke test:

```bash
SDL_VIDEODRIVER=dummy .venv/bin/python -m airts --max-frames 3
```

## Phase 5 limitations and exclusions

Resources are a single integer balance per owner; there is no gathering unit, construction,
technology tree, projectile simulation, armor, cover modifier, or tactical enemy AI.
Combat uses deterministic range, damage, and cooldown profiles. Visibility does not include
line-of-sight occlusion, last-known enemy observations, or a fog overlay. Save and replay schemas are
versioned and reject older incompatible schemas.

Geometry editing replaces a complete point, route, or region rather than offering
per-vertex handles. Multi-region selections are grounded and inspectable but are not yet
interpreted by language. Full fog of war, LM Studio or other AI
providers, voice, MCP, scouting reports, multiplayer, Unity, and a map editor are not
implemented in this phase.
