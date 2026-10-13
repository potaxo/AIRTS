# AIRTS

AIRTS is a Windows-native single-player RTS with base building, team skirmishes against
rule-based AI, and persistent automations you can inspect and interrupt. Give units direct
orders or draw routes and areas for patrol, defense, and factory reinforcements.

The current game includes scouts, light and heavy tanks, artillery, builders, defense turrets,
resource extraction, construction queues, repairs, and JSON save/load. Language-model control
is planned; the playable game currently uses mouse/keyboard controls and rule-based opponents.
[Design](docs/design.md) is the index for scope and gameplay contracts.

## Requirements and setup

Use Windows, PowerShell, and Python 3.13. The default renderer requires an OpenGL 3.3-compatible
graphics driver. From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m airts
```

Dependencies are declared in [pyproject.toml](pyproject.toml), including `pygame-ce`, ModernGL,
NumPy, and Numba. The first simulation startup, or the first startup after kernel changes,
can take several seconds while Numba compiles CPU kernels. Later launches reuse its disk cache.

OpenGL startup errors are reported. To explicitly use software rendering:

```powershell
.\.venv\Scripts\python -m airts --renderer software
```

## Run

Normal startup opens the cover for **Four Corners River**, a fixed, symmetric 320 x 320 map.
Choose **Start game**, cycle the controller and team buttons for the four starting slots,
then choose **Begin match**. Slots can be human, AI, or closed; teams range from 1 to 4.
The setup requires exactly one human and at least two opposing teams. The default is a
2-vs-2 match with one AI ally and two AI opponents. Configuration stays fixed during a match.

Each active player starts with a command center, factory, resource extractor, builder, scout,
and funds. Expand extraction, produce an army, and use the river crossings to reach opponents.
Red rings mark resource sites. Team colors show alliances; all units and buildings are visible,
and there is no fog-of-war overlay. Opponents build their economy, form squads, attack, defend,
and retreat for repairs.

The roster shows active, eliminated, and out players. Victory depends on the surviving teams'
ability to continue production, so residual enemy troops do not necessarily prevent a win.
See [match and elimination rules](docs/milestones/opponents.md#skirmish-configuration-and-results).
Use **Settings > New game** or `F2` to return to the cover and configure another match.

The left panel shows resources, notices, pause/help controls, and your automations. The right
**Selection** panel shows selected entities and their available actions. With a mixed selection,
click a type row to narrow the selection to that type and reveal its controls. The bottom command
bar shows actions available for the current selection and spatial target, plus **Settings**.

## Controls

### Selection, camera, and direct orders

| Input | Action |
| --- | --- |
| `1` | Selection mode: click an entity, point, route, or region; drag to select units |
| `Shift` + selection | Toggle additions to the selection |
| Double-click your entity | Select all of your entities of that type currently inside the canvas |
| Double-click enemy entity | Inspect all hostile entities of that type currently inside the canvas |
| Right-click ground or a friendly entity | Move selected units while preserving their formation |
| Double right-click at the same destination | Gather selected units around the destination |
| Right-click an enemy | Order selected armed units to pursue and attack it; double right-click still attacks |
| `X` / `H` | Stop / Hold position; stopped units can yield to traffic, while held units try to return to their anchors |
| `W` / `A` / `S` / `D` (hold) | Move the camera up / left / down / right, including while paused |
| Middle-drag | Pan the map |
| Mouse wheel over the map | Zoom around the cursor, from the full-map view up to 32 pixels per cell |
| `Space` or Pause/Resume | Pause or resume simulation time |
| `Esc` | Clear selection, spatial target, placement, and inspection; return to selection mode |

Box selection selects your units when any are inside the box; otherwise it can inspect hostile
units and their counts. Your entities also take precedence in combined selections. Enemy
inspection exposes attributes without granting orders, construction, or production controls.
A selection drag can end over the command bar, but only units inside the canvas are selected.

### Spatial targets and automations

| Input | Action |
| --- | --- |
| `2` | Draw a route: left-click vertices, then right-click to finish |
| `3` / `4` | Drag a rectangular area / draw a freehand area |
| `T` / `Y` | Create a patrol / defense for selected units using the active point, route, or area |
| `P` | Attach every selected factory's existing production loop to the active defense line or area; start a Loop on each factory first |
| `N` | Name or rename exactly one selected region; type the name and press `Enter` |
| `E` | Redraw the selected point, route, or region to replace its geometry |
| `Delete` | Delete one selected route or region and cancel automations that use it |
| `U` | Retarget the inspected patrol, defense, or production loop's linked defense to the active spatial target |
| `[` / `]` | Lower / raise the inspected automation's priority |

Select units, draw or select a spatial target, then create the automation. The left panel's
**Units**, **Produce**, and **Build** tabs list your unfinished work. Each row offers `||` to pause,
`>` to resume, `X` to cancel, and `...` to open details beside the list. Details show status,
failure/waiting reasons, membership, or progress. Click `...` again or its close button to close.
Scroll with the wheel over the panel or its scrollbar.

Direct orders override the selected units' current automation; other members can keep working.
Production linked to a defense sends existing and future units to that target: an area gathers
them, while a line distributes them along it.

### Production and construction

Factories produce scouts, light tanks, heavy tanks, artillery, and builders. Command centers
produce builders. Select one or more sources of the same type and use the right panel:

| Control | Action |
| --- | --- |
| Unit name | Show that unit's attributes and production cost |
| `+` | Add one unit to each selected source's finite production queue |
| `-` | Remove one unfinished unit of that kind from the newest matching finite order on each source |
| `Loop` | Set continuous production of that kind on each selected source |

Finite orders run in queue order before the loop resumes. A new Loop replaces the previous loop.
Production waits when funds or exit space are unavailable. Canceling a paid unit discards its
progress without a refund.

Select builders and choose a building in the right panel, then left-click a valid site.
Builders can construct factories, repair hubs, resource extractors, Matter Synthesizers, and
defense turrets. Command centers are starting buildings and cannot be constructed.

| Placement input | Action |
| --- | --- |
| Left-click | Issue a construction order, replacing those builders' pending jobs after validation succeeds |
| `Shift` + left-click | Append a construction job and keep placement active |
| `Shift` + left-drag | Preview a line spaced by building footprints; release to queue valid sites in order |
| Right-click during placement | Cancel the unsubmitted preview and close placement, preserving accepted jobs |

On the river map, extractors must occupy resource sites; clicking within three cells of a site's
center snaps placement to it. Completed extractors earn income automatically. Matter Synthesizers
can be built on ordinary valid terrain and provide a more expensive, lower-output alternative.
There is no manual gathering or income activation step.

Builders travel to queued blueprints and can wait for funds or occupants to move. An unstarted
blueprint does not reserve money or space. Payment happens when construction begins, creating a
physical, attackable scaffold. Multiple builders speed up the same job without duplicating its
cost. Canceling construction leaves that scaffold; ordering construction at an unclaimed friendly
scaffold resumes it without another charge. Unfinished buildings cannot produce, earn income,
repair, or fire. Completed turrets automatically attack enemies in range; artillery fires slowly
at long range.

### Repair and reclamation

| Input | Action |
| --- | --- |
| `R` | Send selected mobile units below 50% health to repair, then resume their work or return to their previous positions |
| Builders + right-click a damaged building you own | Repair the completed building |
| Builder action: **Reclaim building (70%)**, then left-click an owned building | Approach and remove the building, refunding 70% of its construction price |
| Right-click while choosing a reclaim target | Cancel target selection |

A completed repair hub automatically heals one mobile unit of the same owner at a time within its radius.
Select the hub to see that radius; a green line identifies its current patient even without
selection. Units on repair orders stop at clear positions within range instead of all converging
on the hub's center. Selected attackers show red target lines; large selections sample those lines
and movement routes.

## Saves and loading

Open **Settings > Save manager** during play or **Load saved game** from the cover. The browser
lists JSON saves in `saves/` and the existing `airts-quicksave.json`, with names, timestamps, and sizes.
All paths are relative to the working directory from which AIRTS was launched.

Choose **New save** to create a separate timestamped file. Select an entry to **Overwrite**,
**Load**, or **Delete**, then confirm the action. Use the wheel, arrow keys, or **Up/Down** buttons
to scroll; **Refresh** discovers external changes. The game pauses while the browser is open.
**Close** restores the previous pause setting; `Esc` first dismisses a pending confirmation,
then closes the browser. A successful load resumes play and clears presentation selection.

`F5` writes the quicksave, replacing `airts-quicksave.json`; `F9` loads it directly.
Saving runs in the background and reports completion or failure. Wait for it to finish before
loading, overwriting, or deleting saves. Normal exit waits for a pending save. Failed loads keep
the current game intact.

Only current-format saves and replays are supported; updates may reject older files without
migration. The [persistence policy](docs/milestones/persistence.md#supported-formats) owns format
support and save guarantees. Managed saves and quicksaves are ignored by Git.

## CLI options and diagnostics

These are separate launch examples. `--map`, `--load-state`, and `--replay` are mutually exclusive;
load and replay require existing files.

```powershell
.\.venv\Scripts\python -m airts --legacy-map
.\.venv\Scripts\python -m airts --map path/to/map.json
.\.venv\Scripts\python -m airts --load-state saves/match.json
.\.venv\Scripts\python -m airts --legacy-map --enemy-spawn-seconds 2.5 --enemy-cap 60
.\.venv\Scripts\python -m airts --legacy-map --enemy-spawn-seconds 0
.\.venv\Scripts\python -m airts --max-frames 100
.\.venv\Scripts\python -m airts --help
```

`--legacy-map` opens the original 64 x 64 sandbox directly; `--map` opens a custom JSON map directly.
For maps without match slots, ambient enemy spawning defaults to one unit per second with a cap
of 100; a zero interval or cap disables it. Team matches use AI production instead. Loaded saves
and replays keep their recorded spawn settings; CLI spawn options do not override them.
`--max-frames N` exits after N rendered frames.
In a sandbox, `F2` resets the startup map, seed, and spawn settings instead of opening team setup.

CLI exports happen after normal window exit and use the currently displayed simulation, including
matches started or saves loaded in the window. Create the output directory before exporting:

```powershell
New-Item -ItemType Directory -Force outputs | Out-Null
.\.venv\Scripts\python -m airts --event-log outputs/events.jsonl
.\.venv\Scripts\python -m airts --save-state outputs/state.json
.\.venv\Scripts\python -m airts --write-replay outputs/replay.json
.\.venv\Scripts\python -m airts --replay outputs/replay.json
```

Command replay is a debugging diagnostic: it re-simulates recorded commands, verifies the final
snapshot and event stream, then opens the resulting state. It does not display an animated replay
timeline, and exact reproduction across versions or hardware is not guaranteed. See the
[replay policy](docs/milestones/persistence.md#save-reliability-and-replay-policy).

**Settings** offers resolution presets and p95 frame/render/present-wait/simulation timings.
**Real FPS** uses rolling p99 completed-frame time; **Submit FPS** is an average diagnostic.
Neither measures physical monitor scan-out. Performance requirements and acceptance workloads
are defined in the [performance contract](docs/milestones/performance.md).

## Windows package

The manually triggered [Windows build workflow](.github/workflows/build-windows.yml) validates
the project and builds a console-enabled PyInstaller directory. Download its **AIRTS-windows-debug**
artifact, extract the whole directory, and run `AIRTS.exe`; keep the supporting files together.
The same CLI options apply to the executable. Packaging CI runs functional validation;
hardware/timing acceptance is separate.

For contributor workflow and validation commands, see [AGENTS](AGENTS.md). The public
`airts.Simulation` API also supports renderer-independent headless use; start with the
[engineering boundaries](docs/architecture/engineering.md#ownership) when changing the core.
