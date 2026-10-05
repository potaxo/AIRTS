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

The first simulation startup compiles the serial CPU kernels and can take several seconds.
NumPy holds numeric buffers; Numba accelerates movement, route lookahead, and visibility.

## Run

OpenGL 3.3 is the default and needs a compatible graphics driver. Startup failures are reported;
explicitly choose software if needed. The following are separate run examples; create `outputs/`
before exporting files because export commands do not create parent directories:

```powershell
New-Item -ItemType Directory -Force outputs | Out-Null
.\.venv\Scripts\python -m airts --renderer software
.\.venv\Scripts\python -m airts --map PATH
.\.venv\Scripts\python -m airts --enemy-spawn-seconds 2.5 --enemy-cap 60
.\.venv\Scripts\python -m airts --event-log outputs/events.jsonl
.\.venv\Scripts\python -m airts --save-state outputs/state.json
.\.venv\Scripts\python -m airts --load-state outputs/state.json
.\.venv\Scripts\python -m airts --write-replay outputs/replay.json
.\.venv\Scripts\python -m airts --replay outputs/replay.json
.\.venv\Scripts\python -m airts --help
```

`--map`, `--load-state`, and `--replay` are mutually exclusive. Load/replay examples require a
previously exported file. Replay verifies the recorded final snapshot and event stream, then opens
that resulting simulation; it does not play back an animated timeline.

For a new CLI game, enemy generation defaults to one unit/second, cap 100;
`--enemy-spawn-seconds 0` disables it. Loaded saves/replays retain their recorded spawn settings;
these CLI spawn options do not override them. `--max-frames N` closes after N rendered frames.

CLI exports run after normal window exit and currently use the simulation created at startup.
After an in-window load (`F9`) or reset (`F2`), use `F5` to save the displayed game; CLI exports
still refer to the earlier simulation. Quicksave writes `airts-quicksave.json` in the working
directory and overwrites that file; keep it out of Git.

Settings offers resolution presets and p95 frame/render/present-wait/simulation timings.
`Real FPS` measures rolling inverse-p99 completed-swap time; average `Submit FPS` is diagnostic.
These measurements do not count physical monitor scan-out;
[performance contracts](docs/milestones/performance.md) own frame limits and measurement details.

## Controls

| Input | Action |
| --- | --- |
| `1` | Selection mode; click entities, points, routes, or regions, or drag friendly units; `Shift` toggles additions |
| `2` | Add line vertices; right-click to finish the route |
| `3` | Drag a rectangular patrol area |
| `4` | Draw a freehand patrol area |
| `A` | Create a patrol from the selected units and current target |
| `D` | Create a defend automation from selected units and current target |
| `P` | Attach every selected factory loop to the active line or area defense; start a Loop on each factory first |
| `R` | Send only selected units below 30% health to repair, then resume work or return to their previous position |
| `G` | Develop the economy with selected resource generators until 100 more resources |
| `S` / `H` | Stop selected units (they can yield without returning), or establish a Hold anchor at their current position; held bodies can yield and safely return when pressure clears ([movement contract](docs/milestones/runtime.md#contact-yielding-and-hold-anchors)) |
| `N` | Name or rename exactly one selected region; type the name and press `Enter` |
| `E` | Edit the selected point, route, or region by redrawing it |
| `Delete` | Delete one selected route or region and explicitly cancel automations using it |
| `F5` / `F9` | Save or load `airts-quicksave.json` |
| `F2` | Reset the bundled/current starting scenario |
| `U` | Replace the inspected patrol/defend target or production loop's defense target with the active spatial target |
| `Shift` + build click | Keep placement mode active and append the site to the selected builders' FIFO construction queue |
| Right-click while placing | Close building placement without moving builders or changing queued construction |
| `[` / `]` | Decrease or increase the inspected automation priority |
| Right-click | Move, or attack an enemy under the cursor |
| `Space` | Pause or resume simulation time |
| `Esc` | Return to selection mode and clear entity, spatial, placement, and inspection state |
| Middle-drag | Pan the game canvas independently of window resolution |
| Mouse wheel over left panel | Scroll active automations |
| Double-click friendly entity | Select every friendly entity of the same type inside the canvas |

## Windows package

The manually triggered [Windows build workflow](.github/workflows/build-windows.yml) validates
the project and builds a console-enabled PyInstaller directory. Its `AIRTS-windows-debug`
artifact contains `AIRTS.exe` and supporting files; keep the whole extracted directory together.
The workflow runs functional validation; designated-machine hardware/timing acceptance runs
separately using the [contributor validation commands](AGENTS.md#validation).
