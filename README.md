# AIRTS

Windows-native RTS game with direct control, persistent automations, economy,
construction, combat, save/load, and diagnostic command replay. Language-model control is planned.
[Design](docs/design.md) owns scope/contracts; [AGENTS](AGENTS.md) owns contributor workflow.

## Requirements and setup

Windows, PowerShell, Python 3.13; dependencies are defined in [pyproject.toml](pyproject.toml).
From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m airts
```

The first simulation startup after kernel changes compiles the serial CPU kernels and can take
several seconds. Later launches reuse Numba's on-disk compiled cache; compiler failures still
propagate. Source-checkout caches live in ignored `__pycache__` directories.
NumPy holds numeric buffers; Numba accelerates movement, route lookahead, and visibility.

## Run

OpenGL 3.3 is the default and needs a compatible graphics driver. Startup failures are reported;
explicitly choose software if needed. The following are separate run examples; create `outputs/`
before exporting files because export commands do not create parent directories:

```powershell
New-Item -ItemType Directory -Force outputs | Out-Null
.\.venv\Scripts\python -m airts --renderer software
.\.venv\Scripts\python -m airts --legacy-map
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
previously exported file. The optional replay diagnostic currently verifies the recorded final
snapshot and event stream, then opens that resulting simulation; it does not play back an animated
timeline. [Persistence](docs/milestones/persistence.md#save-reliability-and-replay-policy) owns its support policy.

Default startup shows a cover and opening setup for the fixed 320 x 320 symmetric river map.
Click **Start game**, choose the four slot controllers and teams, then **Begin match**.
Exactly one human and at least two opposing teams are required. Configuration is locked after
starting; `F2` returns to the opening cover. Resource sites are visible red rings; build Resource
Extractors on those sites. Placement snaps within three cells of a site's center. The central
crossing has wide clear lanes beside its resource sites. Team colors identify allies and opponents.
All units/buildings are visible; no fog overlay is enabled. The roster displays active, eliminated
and out states; [Opponents](docs/milestones/opponents.md#skirmish-configuration-and-results) owns their rules.

`--legacy-map` opens the original 64 x 64 sandbox directly. For legacy/custom sandbox games,
enemy generation defaults to one unit/second, cap 100;
`--enemy-spawn-seconds 0` disables it. Loaded saves/replays retain their recorded spawn settings;
these CLI spawn options do not override them. `--max-frames N` closes after N rendered frames.

CLI exports run after normal window exit and use the currently displayed simulation, including
in-window starts and loads. Quicksave writes `airts-quicksave.json` in the working
directory and overwrites that file; keep it out of Git.

Settings offers resolution presets and p95 frame/render/present-wait/simulation timings.
`Real FPS` measures rolling inverse-p99 completed-swap time; average `Submit FPS` is diagnostic.
These measurements do not count physical monitor scan-out;
[performance contracts](docs/milestones/performance.md) own frame limits and measurement details.

## Controls

| Input | Action |
| --- | --- |
| `1` | Selection mode; click entities, points, routes, or regions, or drag friendly units; a drag started on the map can finish over the command bar but selects only units inside the canvas; `Shift` toggles additions |
| `2` | Add line vertices; right-click to finish the route |
| `3` | Drag a rectangular patrol area |
| `4` | Draw a freehand patrol area |
| `W` / `A` / `S` / `D` (hold) | Move the camera up / left / down / right; stop at map boundaries |
| `T` | Create a patrol from the selected units and current target |
| `Y` | Create a defend automation from selected units and current target |
| `P` | Attach every selected factory loop to the active line or area defense; start a Loop on each factory first |
| `R` | Send only selected units below 50% health to repair, then resume work or return to their previous position |
| `X` / `H` | Stop selected units (they can yield without returning), or establish a Hold anchor at their current position; held bodies can yield and safely return when pressure clears ([movement contract](docs/milestones/movement.md#contact-yielding-and-hold-anchors)) |
| `N` | Name or rename exactly one selected region; type the name and press `Enter` |
| `E` | Edit the selected point, route, or region by redrawing it |
| `Delete` | Delete one selected route or region and explicitly cancel automations using it |
| `F5` / `F9` | Save or load `airts-quicksave.json` |
| `F2` | Return to opening configuration in a team match; reset the startup map/seed/spawn settings in a legacy sandbox |
| `U` | Replace the inspected patrol/defend target or production loop's defense target with the active spatial target |
| `Shift` + build click | Keep placement mode active and append the site to the selected builders' FIFO construction queue |
| Right-click while placing | Close building placement without moving builders or changing queued construction |
| `[` / `]` | Decrease or increase the inspected automation priority |
| Right-click | Move preserving formation; armed members pursue a clicked enemy until destroyed; selected Builders repair a clicked damaged friendly building |
| Double right-click at the same destination | Gather selected units around ground or friendly entities, regardless of army size; enemy clicks still attack |
| `Space` or top-left Pause/Resume | Pause or resume simulation time |
| `Esc` | Return to selection mode and clear entity, spatial, placement, and inspection state |
| Middle-drag | Pan the game canvas independently of window resolution |
| Mouse wheel over left panel | Scroll active automations |
| Mouse wheel over map | Zoom around the cursor, bounded between the full-map view and 32 pixels per cell |
| Double-click friendly entity | Select every friendly entity of the same type inside the canvas |

The right sidebar contains Selection information and controls. Factories offer scouts, tanks,
and builders; command centers offer builders. Each production row has a unit name/remaining count,
`+`, `-`, and `Loop`: `+` orders immediately on each selected source, `-` removes one unfinished
finite order per source, and `Loop` preserves continuous production. Click the unit name to inspect
its attributes and cost. Canceling a paid unit does not refund its cost.

Selecting a building shows its construction price. Builders offer Matter Synthesizers alongside
factories, repair hubs and extractors; Synthesizers require no resource site. Selected attackers
show faint red target lines that update each simulation tick, sampled like movement paths for large
selections. Selecting a repair hub shows its repair radius; it automatically heals one friendly
mobile unit at a time inside that circle.
An active repair hub shows a green line to the unit it is currently healing, even without selection.

The left sidebar separates unit, production, and construction automations into tabs. Each row has
`||` to pause / `>` to resume, `X` to cancel, and `...` to open a small details window immediately
to the right of the list. Its `X` closes the window; clicking `...` again also closes it.

## Windows package

The manually triggered [Windows build workflow](.github/workflows/build-windows.yml) validates
the project and builds a console-enabled PyInstaller directory. Its `AIRTS-windows-debug`
artifact contains `AIRTS.exe` and supporting files; keep the whole extracted directory together.
The workflow runs functional validation; designated-machine hardware/timing acceptance runs
separately using the [contributor validation commands](AGENTS.md#validation).
