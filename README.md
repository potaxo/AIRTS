# AIRTS

AIRTS is a Windows-native RTS research prototype for direct control and persistent,
human-supervised automation. The current build includes economy, construction, production,
combat, spatial commands, save/load, and deterministic replay. Language-model control is not
implemented yet.

The [design index](docs/design.md) is the entry point for architecture, scope, implemented
milestones, limitations, and focused owner documents. [AGENTS.md](AGENTS.md) contains contributor
rules and required validation.

## Requirements and setup

Use Windows, PowerShell, Python 3.13, and the repository-local virtual environment. From the
repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## Run

```powershell
.\.venv\Scripts\python -m airts
```

OpenGL 3.3 is the default renderer. For diagnostics or systems where OpenGL is not intended:

```powershell
.\.venv\Scripts\python -m airts --renderer software
```

OpenGL startup failures are reported and do not silently fall back. Use `--renderer software`
explicitly when that backend is intended.

The OpenGL frontend is not capped by the application clock and does not request VSync. Open
`Settings` to choose a window-resolution preset and inspect rolling p95 frame, render, present-wait,
and simulation timing. `Submit FPS` counts completed application swaps; physical display cadence
still depends on the monitor, driver, and Windows compositor.

Common workflows:

```powershell
.\.venv\Scripts\python -m airts --map PATH
.\.venv\Scripts\python -m airts --enemy-spawn-seconds 2.5 --enemy-cap 60
.\.venv\Scripts\python -m airts --event-log events.jsonl
.\.venv\Scripts\python -m airts --save-state state.json
.\.venv\Scripts\python -m airts --load-state state.json
.\.venv\Scripts\python -m airts --write-replay replay.json
.\.venv\Scripts\python -m airts --replay replay.json
.\.venv\Scripts\python -m airts --help
```

Enemy generation defaults to one unit per second with a cap of 100. Set
`--enemy-spawn-seconds 0` to disable it.

## Controls

| Input | Action |
| --- | --- |
| `1` | Selection mode; click entities or regions, or drag friendly units; `Shift` toggles additions |
| `2` | Add line vertices; right-click to finish the route |
| `3` | Drag a rectangular patrol area |
| `4` | Draw a freehand patrol area |
| `A` | Create a patrol from the selected units and current target |
| `D` | Create a defend automation from selected units and current target |
| `P` | Attach every selected factory loop to the active line or area defense |
| `R` | Send only selected units below 30% health to repair, then resume work or return to their previous position |
| `G` | Develop the economy with selected resource generators until 100 more resources |
| `S` / `H` | Stop selected units or hold their current position |
| `N` | Name or rename exactly one selected region; type the name and press `Enter` |
| `E` | Edit the selected point, route, or region by redrawing it |
| `Delete` | Delete one selected route or region and explicitly cancel automations using it |
| `F5` / `F9` | Save or load `airts-quicksave.json` |
| `F2` | Reset the bundled/current starting scenario |
| `U` | Replace the inspected patrol/defend target with the active spatial target |
| `Shift` + build click | Keep placement mode active and append the site to the selected builders' FIFO construction queue |
| Right-click while placing | Close building placement without moving builders or changing queued construction |
| `[` / `]` | Decrease or increase the inspected automation priority |
| Right-click | Move, or attack an enemy under the cursor |
| `Space` | Pause or resume simulation time |
| `Esc` | Return to selection mode and clear entity, spatial, placement, and inspection state |
| Middle-drag | Pan the game canvas independently of window resolution |
| Mouse wheel over left panel | Scroll active automations |
| Double-click friendly entity | Select every friendly entity of the same type inside the canvas |

## Development

Keep code changes within the requested milestone and preserve the simulation/UI dependency
boundary. Before declaring a coding task complete, run the validation suite listed in
[AGENTS.md](AGENTS.md). Update the owning document linked from [docs/design.md](docs/design.md) for
every architecture change, feature upgrade, optimization, or behavioral improvement.
