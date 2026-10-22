# AIRTS

AIRTS is a Windows-native single-player RTS with base building, team skirmishes against
rule-based AI, and persistent automations you can inspect and interrupt. Give units direct
orders or draw routes and areas for patrol, defense, and factory reinforcements.

The current game includes scouts, light and heavy tanks, light artillery, tank destroyers, heavy artillery, builders, defense turrets,
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

**Debug mode (record full history)** is unchecked by default on the cover and team setup.
Leave it off for normal play: save/load retains the current world and ongoing orders without
accumulating event or command history. Enable it before starting or loading for full diagnostic
recording. The choice applies to new matches and loads in this session; it is independent of
the resource-bonus **Developer settings**. Loading with Debug off validates existing history but
does not retain it. Existing large files still need to be read; new saves omit their old history.

Each active player starts with a command center, factory, resource extractor, builder, scout,
and funds. Expand extraction, produce an army, and use the river crossings to reach opponents.
Red rings mark resource sites. Team colors show alliances; all units and buildings are visible,
and there is no fog-of-war overlay. Opponents build their economy, form squads, attack, defend,
and retreat for repairs.

The roster shows active, eliminated, and out players. Victory requires destroying every opposing
unit and building; armies continue fighting after their owner loses production.
See [match and elimination rules](docs/milestones/opponents.md#skirmish-configuration-and-results).
Use **Settings > New game** or `F2` to return to the cover and configure another match.

The left panel shows resources, notices, pause/help controls, and your automations. The right
**Selection** panel shows selected entities and their available actions. Enemy inspection also lists
each selected faction's owner, team (in matches), and unit count. With a mixed selection, left-click
a type row to narrow the selection to that type and reveal its controls; right-click
a type row in a mixed selection of your units to remove that type from the selection. The bottom
command bar shows actions available for the current selection and spatial target, plus **Settings**.
The minimap at the top of the right sidebar shows terrain, gold resource sites, and every army in team
colors (green friendly/red enemy in the sandbox). Squares mark buildings; small dots mark units,
with pale outlines for selected entities. The pale rectangle shows the camera's current view.
Selection details and compact action rows sit below the minimap; scroll there to reach content
that does not fit a short window. The minimap stays fixed while those details scroll.

## Controls

Battle units/buildings use packaged pixel art with display-only turning and separate weapon layers.
Art locations, regeneration and replacement are documented in the
[appearance contract](docs/architecture/appearance.md); production portraits are not included.

### Selection, camera, and direct orders

| Input | Action |
| --- | --- |
| `1` | Selection mode: click an entity, point, route, or region; drag to select units |
| `Shift` + left-click or selection drag | Toggle additions to the selection |
| `Shift` + right-click with selected units | Append an order: ground gathers and moves, enemies are attacked, damaged owned buildings are repaired; each unit skips unsupported tasks and finishes its current task before the next |
| Double-click your entity | Select all of your entities of that type currently inside the canvas |
| Double-click enemy entity | Inspect same-type entities belonging to the clicked enemy player currently inside the canvas |
| Right-click ground or a friendly entity, once or repeatedly | Gather and move along shared routes; fire in range while continuing to the destination without combat detours; distance and click count do not change the movement mode |
| **Keep formation** in the bottom command bar | Toggle strict formation for selected mobile units; the active border shows it is enabled |
| Right-click an enemy, including rapid repeated clicks | Pursue and attack that entity; fire at enemies along the route, prioritize the ordered target in range, and continue queued work after destruction |
| `X` / `H` | Stop / Hold position; stopped units can yield to traffic, while held units try to return to their anchors |
| `W` / `A` / `S` / `D` (hold) | Move the camera up / left / down / right, including while paused |
| Middle-drag | Pan the map |
| Left-click or left-drag on the minimap | Center the camera on that location, including while paused |
| Mouse wheel over the map | Zoom around the cursor, from the full-map view up to 32 pixels per cell |
| `Space` or Pause/Resume | Pause or resume simulation time |
| `Esc` | Clear selection, spatial target, placement, and inspection; return to selection mode |

Box selection prefers your entities; otherwise it can inspect hostile entities and their counts.
Within that side, units take precedence over buildings; a box containing only buildings selects them.
Among units, those capable of attacking take precedence over unarmed units such as builders.
Your entities also take precedence in combined selections. Enemy
inspection exposes attributes without granting orders, construction, or production controls.
A selection drag can end over the command bar, but only entities inside the canvas are selected.
Click assistance applies to selection, context orders, and reclaim targeting. At distant zoom,
nearby small targets are easier to hit and direct hits take precedence over assisted hits.
As you zoom in, assistance fades: close views require a click inside the building footprint,
so nearby ground remains a movement destination. Units retain their
displayed body hit area. Shift queues use the same target rules.

### Spatial targets and automations

| Input | Action |
| --- | --- |
| `2` | Draw a route: left-click vertices, then right-click to finish |
| `3` / `4` | Drag a rectangular area / draw a freehand area |
| `T` / `Y` | Create a patrol / defense for selected units using the active point, route, or area |
| `P` / **Produce + Defend** | Save the active defense line or area for every selected factory, including unfinished or idle factories; current and later production sends new units there |
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

In **Units**, the separate **New area repair** row chooses whether new patrols, defenses,
and `P` production defenses automatically retreat for repairs. It defaults to OFF. Open `...`
to toggle auto repair for an existing assignment. Enabled assignments send units strictly below
50% health (the same threshold as `R`) for repairs, then resume their original work at full health.
Changing the new-area switch does not change existing assignments; disabling an assignment's
switch prevents future automatic retreats and lets repairs already underway finish.

Direct orders override the selected units' current automation and pending construction/movement
queues; other members keep working. Shift orders append to direct movement, attack, repair, or
reclamation already in progress. Every Shift ground click appends a distinct move. Moves gather by
default. **Keep formation** records the selection's current layout for subsequent direct movement,
including queued legs: the cohort travels at its slowest member's terrain-adjusted speed, waits for
its members before the next queued task, and restores its layout after squeezing through passages.
The toggle persists on those units; turning it off releases coordination without clearing their jobs.
Previously issued moves keep their destinations. Attack, building work and automations keep their own
control rules. A strict destination must have room for the saved layout; otherwise the move is rejected.
Successful player unit orders also prevent automatic combat support from taking over those units
for five seconds of game time; each new order restarts this micro window. Hold units never join
automatic support until another order replaces Hold. Units can still fire at enemies in range.
Production linked to a defense sends existing and future units to that target: an area gathers
them, while a line distributes them along it.

### Production and construction

Factories produce scouts, light tanks, heavy tanks, light artillery, tank destroyers, artillery, and builders. Command centers
produce builders. Select one or more sources of the same type and use the right panel:

| Control | Action |
| --- | --- |
| Unit name | Show that unit's attributes and production cost |
| `+` | Add one unit to each selected source's finite production queue |
| `-` | Remove one unfinished unit of that kind from the newest matching finite order on each source |
| `Loop` | Start continuous production of that kind on each selected source; click the lit button again to cancel that kind's loops on the selected sources |

Finite orders run in queue order before the loop resumes. A new Loop replaces the previous loop.
With mixed selections, clicking a lit Loop cancels matching loops and leaves other sources alone;
clicking an unlit Loop starts that kind on all selected sources. Finite orders remain queued.
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

The building-line preview uses the submitted grid origins and footprints: green shadows mark valid
sites, and red shadows mark sites that will be skipped. The complete line updates while dragging.

On the river map, extractors must occupy resource sites; clicking within three cells of a site's
center snaps placement to it. Completed extractors earn income automatically. Matter Synthesizers
can be built on ordinary valid terrain and provide a more expensive, lower-output alternative.
There is no manual gathering or income activation step.

Select completed extractors or Matter Synthesizers to upgrade them from T1 to T2 and T3 in the
right panel. It shows current output, the next upgrade's resource cost/time, and running progress.
Upgrades charge the single resource immediately and keep producing at the current tier until
completion. **Cancel upgrade** refunds the full upgrade cost and loses its progress; destruction
does not refund it. Same-type selections apply each button to its displayed tier/progress group,
so idle buildings can start while peers are upgrading. The upgrade button shows how many buildings
you can afford; clicking starts the maximum affordable number at the current balance. Unfinished
buildings and max-tier buildings cannot upgrade; the action is disabled when you cannot afford one.
Upgrade progress and partial production cycles survive save/load. The
[runtime contract](docs/milestones/runtime.md#entity-upgrades) owns the current balance table.

Builders travel to queued blueprints and can wait for funds or occupants to move. Friendly idle
units without current or queued orders automatically walk out of the blueprint footprint; commanded
units keep their orders. Blocked exits leave the blueprint waiting. An unstarted
blueprint does not reserve money or space. Payment happens when construction begins, creating a
physical, attackable scaffold. Multiple builders speed up the same job without duplicating its
cost. Canceling construction leaves that scaffold; ordering construction at an unclaimed friendly
scaffold resumes it without another charge. Selecting builders and right-clicking an owned unfinished
building also resumes it, even after another command interrupted its original builder.
Unfinished buildings cannot produce, earn income,
repair, or fire. Completed turrets automatically attack enemies in range; artillery fires slowly
at long range.

### Repair and reclamation

| Input | Action |
| --- | --- |
| `R` | Send selected mobile units below 50% health to repair, then resume their work or return to their previous positions |
| Builders + right-click a damaged or unfinished building you own | Repair it or continue its construction without another payment |
| Builder action: **Reclaim building (70%)**, then left-click an owned building | Approach and gradually drain its health with a red beam; refund 70% of its construction price only on completion |
| Right-click while choosing a reclaim target | Cancel target selection |

A completed repair hub automatically heals one other mobile unit or building of the same owner at a time within its radius, excluding buildings being reclaimed. It can finish an existing unfinished
building, but cannot place or start blueprints. Select the hub and right-click an in-range building to
choose its service target.
Select the hub to see that radius. Thick green lines connect builders to buildings while constructing
or repairing, and repair facilities to units or buildings while healing, even without selection. Builders work
within the build/repair ranges shown in their details, without needing to touch the building.
Units on repair orders stop at clear positions within range instead of all converging
on the hub's center. Selected orders show red tracking attacks and white movement segments,
including queued tasks. Large selections sample these lines rather than drawing every unit's route.

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
`--debug` enables full history for direct startup. `--event-log`, `--write-replay`, and `--replay`
also enable it. Ordinary startup, including `--load-state`, defaults to recording off.
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
Open **Settings > Developer settings** for the player resource bonus, game speed and AI income.
The player bonus starts OFF; ON adds 2,000 resources per game second in addition to building income.
OFF stops it and clears its partial second. **AI income 5x** multiplies every AI's building income
by five; OFF restores normal income. Both income settings are saved and default to OFF/1x in a new
simulation. Speed offers **0.5x / 1x / 2x / 4x**, defaults to 1x, and stays a local app preference
across new games and loads. Pause suspends all game-time income and progression at every speed.
Building income settles once per game second. The resource header's **(+N)** shows total player
income per game second, including the developer bonus.
Enable **Developer settings > Show AI income** to list each AI's balance and **(+N)** income
below the player's resources in the upper-left panel. The displayed rate includes the AI income
multiplier. This local display option starts OFF; turning it OFF hides the AI rows again.
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
