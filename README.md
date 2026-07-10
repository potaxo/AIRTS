# AIRTS

AIRTS is a small research environment for human-in-the-loop, language-driven RTS
automation. Phase 1 is a playable, deterministic vertical slice: it has conventional
spatial controls and one persistent patrol automation, but no language model yet.

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

The bundled scenario is a validated 64 × 64 map with six units, roads, a river, and a
bridge. A custom map using the same JSON format can be supplied with `--map PATH`.
Structured events can be written when the application exits:

```bash
.venv/bin/python -m airts --event-log events.jsonl
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
entity, command, patrol, and event modules are independently testable. The
Pygame app converts user input into the same commands used by tests and future control
sources. Simulation advances at a fixed 10 ticks per second independently of rendering.

Phase 1 movement is straight-line and deterministic. A move stops with an explicit event
if it reaches impassable terrain; routing around obstacles belongs to Phase 2.

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

## Phase 1 exclusions

Combat, economy, fog of war, LM Studio or other AI providers, voice, MCP, scouting
reports, multiple automation templates, multiplayer, Unity, and a map editor are not
implemented in this phase.
