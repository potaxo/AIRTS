# AIRTS

AIRTS is a small research environment for human-in-the-loop, language-driven RTS
automation. Phase 3 adds a deterministic command and automation runtime with validated
ownership, lifecycle, priorities, conflicts, production, reinforcement, and
repair-and-return behavior, but no language model yet.

The authoritative project scope and architecture are defined in
[`docs/design.md`](docs/design.md).

## Setup

AIRTS is developed in WSL2 Ubuntu with Python 3.13. From the repository root:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

The project uses `pygame-ce`; do not install the separate `pygame` package.

## Run

```bash
.venv/bin/python -m airts
```

The bundled scenario is a validated 64 × 64 map with six units, four buildings,
roads, forest, a river, and a bridge. A custom map using the same JSON format can be
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
| `1` | Entity selection mode; click or drag to select units |
| `2` | Place a point patrol target |
| `3` | Add line vertices; press `Enter` to finish the route |
| `4` | Drag a rectangular patrol area |
| `5` | Draw a freehand patrol area |
| `A` | Create a patrol from the selected units and current target |
| `D` | Create a defend automation from selected units and current target |
| `P` | Produce three light tanks from exactly one selected factory |
| `R` | Repair selected units and return them to suspended assignments |
| Right-click | Manually move selected units and detach them from automation |
| `Space` | Pause or resume simulation time |
| `Esc` | Clear the current spatial target or draft |

The automation panel shows the tagged template, lifecycle status, and assigned-entity
count and provides pause/resume and cancel controls. Recent structured events are shown
beneath it. Reinforcement and fully parameterized schemas are available through the
shared Python command interface; richer editing controls belong to Phase 4.

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

Factories produce units after fixed build times without resource costs. Defend behavior
maintains grounded positions without combat, reinforcement transfers eligible units to
another automation, and repair selects destinations by repair-hub/factory/command-center
order and valid path cost before restoring the original assignment.

Movement uses deterministic four-direction A* with terrain costs. Terrain and building
footprints are hard obstacles, while unit-cell conflicts are resolved deterministically
during movement. The UI displays the calculated path rather than deriving one itself.

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

## Phase 3 limitations and exclusions

Production is intentionally cost-free and repair uses fixed-rate healing. Resources,
production costs, combat damage, attacks, targeting, and economic behavior belong to
Phase 5. Defend controls positioning only. Visibility does not yet include line-of-sight
occlusion, last-known enemy observations, or a fog overlay. Save and replay schemas are
versioned and reject older incompatible schemas.

Combat, economy, full fog of war, Phase 4 editing features, LM Studio or other AI
providers, voice, MCP, scouting reports, multiplayer, Unity, and a map editor are not
implemented in this phase.
