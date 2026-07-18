# AIRTS

AIRTS is a Windows-native RTS research prototype for direct control and persistent,
human-supervised automation. The current build includes movement, formations, economy,
construction, production, combat, spatial commands, save/load, and deterministic replay.
Language-model control is not implemented yet.

The simulation is authoritative and runs without a graphical window. The interactive application
is one `pygame-ce` software frontend that submits the same commands used by tests and replay.

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

Useful command-line workflows:

```powershell
.\.venv\Scripts\python -m airts --map PATH
.\.venv\Scripts\python -m airts --enemy-spawn-seconds 2.5 --enemy-cap 60
.\.venv\Scripts\python -m airts --enemy-spawn-seconds 0
.\.venv\Scripts\python -m airts --event-log events.jsonl
.\.venv\Scripts\python -m airts --save-state state.json
.\.venv\Scripts\python -m airts --load-state state.json
.\.venv\Scripts\python -m airts --write-replay replay.json
.\.venv\Scripts\python -m airts --replay replay.json
.\.venv\Scripts\python -m airts --help
```

Enemy generation defaults to one mobile enemy per second with a cap of 100. Set
`--enemy-spawn-seconds 0` to disable it.

## Controls

| Input | Action |
| --- | --- |
| `1` | Select entities or regions; drag friendly units; hold `Shift` to toggle additions |
| `2` | Add route vertices; right-click to finish |
| `3` | Draw a rectangular area |
| `4` | Draw a freehand area |
| Right-click | Move selected units, or attack an enemy under the cursor |
| `A` / `D` | Create a patrol or defense from the selected units and active target |
| `P` | Attach selected factory production loops to the active line or area defense |
| `R` | Send selected units below 30% health to repair and return |
| `G` | Develop the economy with selected resource generators |
| `S` / `H` | Stop selected units or hold their positions |
| `N` | Name or rename one selected region; enter the name and press `Enter` |
| `E` | Replace the selected point, route, or region by redrawing it |
| `Delete` | Delete one selected route or region and cancel automations that use it |
| `U` | Replace the inspected patrol or defense target with the active spatial target |
| `[` / `]` | Decrease or increase the inspected automation priority |
| `F2` | Reset the current starting scenario |
| `F5` / `F9` | Save or load `airts-quicksave.json` |
| `Space` | Pause or resume simulation time |
| `Esc` | Return to selection mode and clear selection, placement, and inspection state |
| Middle-drag | Pan the game canvas |
| Mouse wheel over left panel | Scroll active automations |
| Double-click friendly entity | Select visible friendly entities of the same kind |
| `Shift` + build click | Append a construction site to the selected builders' FIFO queue |
| Right-click while placing | Close placement without changing accepted construction work |

## Where to start reading

| Concern | Entry point |
| --- | --- |
| Authoritative state and fixed-tick orchestration | `src/airts/simulation.py` |
| Commands and automation schemas | `src/airts/commands.py`, `src/airts/automations.py` |
| Routing and local movement | `src/airts/navigation/`, `src/airts/systems/movement.py` |
| World state | `src/airts/world/` |
| Interactive frontend | `src/airts/presentation/app.py` |
| Persistence and replay | `src/airts/adapters/` |

The [design overview](docs/design.md) describes the implemented architecture and limitations.
[AGENTS.md](AGENTS.md) contains contributor rules and required validation commands.
