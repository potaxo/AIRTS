# AIRTS

AIRTS is a small research environment for human-in-the-loop, language-driven RTS
automation. Phase 2 provides a playable deterministic simulation foundation with
conventional spatial controls and one persistent patrol automation, but no language
model yet.

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

The bundled scenario is a validated 64 × 64 map with six units, four inert buildings,
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
| Right-click | Manually move selected units and detach them from automation |
| `Space` | Pause or resume simulation time |
| `Esc` | Clear the current spatial target or draft |

The automation panel shows status and assigned-unit counts and provides pause/resume and
cancel controls. Recent structured events are shown beneath it.

## Architecture

The core simulation modules are authoritative and do not import Pygame. Map, geometry,
entity, occupancy, pathfinding, visibility, command, patrol, persistence, replay, and
event modules are independently testable. The Pygame app converts user input into the
same commands used by tests and future control sources. Simulation advances at a fixed
10 ticks per second independently of rendering.

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

## Phase 2 limitations and exclusions

Buildings are validated, occupy space, provide vision, and can be inspected, but do not
yet produce units, generate resources, repair, or fight. Visibility does not yet include
line-of-sight occlusion, last-known enemy observations, or a fog overlay. Save and replay
schemas are versioned foundations and do not promise compatibility with future schema
versions.

Combat, economy, full fog of war, additional automation templates, the Phase 3 lifecycle
and conflict runtime, LM Studio or other AI providers, voice, MCP, scouting reports,
multiplayer, Unity, and a map editor are not implemented in this phase.
