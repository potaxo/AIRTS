# AIRTS

Windows-native RTS research prototype with direct control, persistent automations, economy,
construction, combat, save/load, and deterministic replay. Language-model control is planned.
[Design](docs/design.md) owns scope/contracts; [AGENTS](AGENTS.md) owns contributor workflow.

## Requirements and setup

Windows, PowerShell, Python 3.13; dependencies are defined in [pyproject.toml](pyproject.toml).
From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m airts
```

## Run

OpenGL 3.3 is the default. Startup failures are reported; explicitly choose software if needed:

```powershell
.\.venv\Scripts\python -m airts --renderer software
.\.venv\Scripts\python -m airts --map PATH
.\.venv\Scripts\python -m airts --enemy-spawn-seconds 2.5 --enemy-cap 60
.\.venv\Scripts\python -m airts --event-log events.jsonl
.\.venv\Scripts\python -m airts --save-state state.json
.\.venv\Scripts\python -m airts --load-state state.json
.\.venv\Scripts\python -m airts --write-replay replay.json
.\.venv\Scripts\python -m airts --replay replay.json
.\.venv\Scripts\python -m airts --help
```

Enemy generation defaults to one unit/second, cap 100; `--enemy-spawn-seconds 0` disables it.
Settings offers resolution presets and p95 frame/render/present-wait/simulation timings.
`Real FPS` measures rolling inverse-p99 completed-swap time; average `Submit FPS` is diagnostic.
The frontend has a 1,000 FPS ceiling and requests VSync off. These measurements do not count
physical monitor scan-out; [performance contracts](docs/milestones/performance.md) explain limits.

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
