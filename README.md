# AIRTS

AIRTS is a small research environment for human-in-the-loop, language-driven RTS
automation. The current milestone adds a responsive two-sided RTS interface, builder
construction, ordered multi-unit factory queues, and continuous production to the
deterministic Phase 5 economy and combat core. Its verified responsiveness targets include
1,000 selected ground units executing move, patrol, or defend, plus 1,000 selected scouts
colliding head-on while the complete 4K software-surface workload renders at 100 frames per
second. It does not add a language model yet.
Units never retreat automatically because of low health;
repair-and-return runs only after an explicit player command or automation request.

The authoritative project scope and architecture are defined in
[`docs/design.md`](docs/design.md).

## Setup

AIRTS is developed in WSL2 Ubuntu with Python 3.13. From the repository root:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

The project uses `pygame-ce`; do not install the separate `pygame` package.

Development plus packaging
```bash
.venv/bin/python -m pip install -e ".[dev,package]"
```

## Run

```bash
.venv/bin/python -m airts
```

The bundled scenario is a validated 64 × 64 map with opposing forces, support and
economic buildings, roads, forest, a river, and a bridge. A custom map can be
supplied with `--map PATH`.

Enemy generation is configurable for new games:

```bash
# One enemy every 2.5 seconds, with at most 60 active enemy mobile units
.venv/bin/python -m airts --enemy-spawn-seconds 2.5 --enemy-cap 60

# Disable automatic enemy generation
.venv/bin/python -m airts --enemy-spawn-seconds 0
```

The default is one enemy per second with a cap of 100 active enemy mobile units.
The interval and cap are preserved by saves and replays.

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
| Double-click friendly entity | Select every visible friendly entity of the same type |

Line, rectangle, and freehand tools return to selection mode after one completed
operation. Drawing creates stable route and region IDs. Named regions are persistent and
must have unique names; overlapping regions are allowed. Click an automation card to
inspect its provenance, owner, priority, reason, timestamps, and entities. The panel
also provides pause/resume and cancel controls, and the event view includes validation
reasons where available. Terminal and entity-less automations are removed from the live
panel so newer active work remains visible; their event and replay history is preserved.
Production target counts and reinforcement minimums are
editable through the shared Python command interface.

## Architecture

The core simulation modules are authoritative and do not import Pygame. Map, geometry,
entity, occupancy, pathfinding, visibility, command, automation, validation, control,
persistence, replay, event, and spatial-index modules are independently testable. The Pygame app
converts user input into the same tagged commands used by tests and future control
sources. Simulation advances at a fixed 10 ticks per second independently of rendering.
Local steering, collision broadphase, and nearby targeting use deterministic spatial buckets
instead of global entity-pair scans; each movement attempt builds one compact local-collider
snapshot and reuses it for steering, collision safety, and stationary-blocker checks. Static
building cells are computed once per movement tick, and squared distances avoid repeated square
roots in the inner loop. Reverse navigation fields use dense indexed storage, with a layered
builder for uniform terrain and weighted Dijkstra for mixed terrain. Large scout formations share
8 x 8 staging clusters before branching to unique final slots; other unit kinds retain the
existing 5 x 5 grouping. Visibility unions exact circular sight into per-row bit masks before
materializing visible cells. The UI caches scaled terrain, per-tick large-scene transforms, unit
sprites, and representative route transforms, targets 100 FPS, and submits cached unit blits in
one batch. Large selected groups use a color lift and group outline instead of a second outline and
full-health bar for every unit; damaged and inspected health bars and normal buildings remain
visible. Repeated physical corrections are limited to one structured push event per unit per tick.

The runtime keeps a bounded logical Pygame software framebuffer and opens it with
`SCALED | RESIZABLE`; SDL scales that frame to the physical window and translates mouse input back
to logical coordinates. This prevents a 4K desktop from multiplying every Python/Pygame draw by
the physical pixel count. Pygame's Surface drawing remains CPU work; the active SDL backend may
accelerate presentation, but AIRTS does not require or claim an explicit GPU rendering pipeline.

Every automation follows an explicit lifecycle from proposal and validation through
active, waiting, paused, blocked, and terminal states. Creating a new patrol or defend
automation for selected units explicitly replaces their older normal assignment; an
empty replaced automation is canceled and leaves the live panel. Emergency repair may
temporarily suspend one assignment so it can be restored afterward.

Each factory can produce scouts, light tanks, heavy tanks, and builders through one authoritative
FIFO production queue: the first unfinished request runs, later requests
wait visibly, and completion or cancellation starts the next job. Pausing preserves progress;
resuming an active or queued job does not create a control conflict. Factories reserve unit
costs before building and wait visibly when funds are insufficient. A continuous production
request remains active and starts the next unit after every spawn. When a factory and polygon
area are selected together, each produced unit joins a persistent military gathering defense.
There is no fixed unit cap: unique reachable stations are allocated center-out across the map, so
the formation and its translucent glow expand like a snowball. Only four new routes are calculated
per tick. An incoming unit that meets its own settled formation stops at the outskirts instead of
pushing through it, leaving the interior motionless. Creating another
continuous request for the same factory cancels its older unfinished
continuous request. A finite player queue preempts the current continuous job, runs first, and
then lets the loop resume; finite jobs retain FIFO ordering. A loop is production-only until
`Produce + Defend` or the automation inspector explicitly attaches its current unit kind to a
selected polygon area. Units take five ticks (0.5 seconds) to
build. An ordered request stores exact per-kind quantities and advances stage by stage before
leaving the live automation list. Factory controls apply to every selected friendly factory: a
Loop click creates the same independent continuous request on each factory, starting an ordered
queue copies the staged sequence to each factory, and `Produce + Defend` retargets every selected
factory's current loop. Each action still submits ordinary authoritative commands per factory, so
resource costs, build timing, persistence, replay, and finite-queue priority remain unchanged.
Contextual actions use the same selection-wide rule for other compatible buildings; for example,
`Develop economy` assigns all selected friendly resource generators. Builders cost 75 resources
and can place factories, repair hubs,
and resource generators on clear, passable, grid-aligned footprints. A selected builder group
shares one construction job: its 400, 250, or 200 resource cost is reserved once and each assigned
builder contributes its profile's 5-value build speed per tick toward the 100-value total only
while inside its 2.5-map-unit build range. Out-of-range builders path to the site before work
begins. Shift-clicking additional placements reserves their footprints and appends FIFO jobs; each
queued cost is charged only when that job starts. The canvas shows a green or red footprint before
placement, a builder range ring, and a progress bar during construction. A completed site waits
instead of placing while any unit occupies its footprint; an assigned builder inside the footprint
routes back outside before contributing more work.
Command centers are scenario
anchors and cannot be built. Resource generators produce 1,000 resources every second; an economy
automation monitors progress toward a target and exposes it through the normal lifecycle.
GUI games create seeded, deterministic enemy light or heavy tanks on the right side at the
configured interval and stop at the configured cap. Defend behavior evenly assigns exact stations, locally rallies nearby defenders
against the source of incoming fire, limits pursuit, and returns survivors to their stations.
Reinforcement transfers eligible units to another automation. Manual repair filters the selection
before routing, so only units strictly below 30% health are claimed. It selects destinations by
repair-hub/factory/command-center order and valid path cost, then restores the original assignment;
an unassigned unit instead returns to its stored pre-repair position.

`Produce + Defend` attaches the selected factory's current continuous loop to either a polygon
gathering area or a polyline defense. Polygon forces keep the expanding center-out formation;
line forces redistribute across evenly spaced stations along the full route as units are produced.

The left status rail includes the live frame rate. Middle-drag pans the map canvas, and window
resizing recomputes the canvas and both side rails for compact through 4K displays.

Movement uses deterministic four-direction A* with terrain costs. Terrain and building
footprints are hard obstacles. Units sharing patrol, repair, or clustered large-group movement
destinations reuse deterministic reverse navigation fields instead of running an independent
full-map search for every unit. The cache is bounded, and large move formations branch through
nearby staging anchors before reaching their unique final slots. This preserves deterministic
replay without worker-thread scheduling. A deterministic local swarm controller ranks short steering
velocities by route progress, unit separation, and a left-hand passing convention. Moving
units look past contested intermediate waypoints, and separate commands reserve distinct
destination cells. Group moves fill forward formation slots first so early arrivals do not
plug the approach. Intermediate A* cell centers use a small completion radius, while final
destinations remain exact and reroute around settled units when necessary. A unit still
blocked uses a free sidestep and reallocates a crowded destination as final recovery. Group
destinations and patrol starts remain distributed. Line-patrol groups start from the first
vertex together and use same-direction formation slots at each route vertex, preventing the
old opposing-flow endpoint jam. The UI displays the global path rather than deriving one itself.
On the final waypoint a unit snaps to its validated destination and becomes idle (or resumes
its assigned behavior), preventing local separation steering from making it oscillate there.
Every unit has a physical collider and mass. Contact pressure is resolved continuously over
simulation ticks rather than by bouncing or teleporting a blocker into another cell. Every unit
can push moving or stationary units; displacement per tick is inversely proportional to the
pushed unit's mass, so heavy tanks accelerate more slowly. Opposing forces combine
deterministically, equal head-on pressure may stalemate, and touching chains propagate force.
Swept contact clamping prevents deep overlap. Pushing preserves each unit's current order and is
recorded as structured `unit_pushed` events. If an order makes no meaningful progress toward its
current waypoint for three seconds, the unit temporarily yields and records a
`movement_yielded` event. The path and destination remain intact; staggered physical retries keep
pushing blockers and automatically restore full movement when space opens. Unit occupancy no
longer duplicates collider blocking at cell boundaries: moving units have right-of-way pressure,
while stationary units are pushed forward or yield laterally when forward displacement is blocked.

Combat uses authoritative direct-hit projectiles. Firing creates a visible bullet that moves
on deterministic simulation ticks, records its map trajectory, and applies the firing unit's
damage only when it reaches the selected target. Bullets use one small, high-contrast visual size
at every UI scale. If a target is destroyed first, an in-flight bullet continues to the target's
last known position and lands there without applying damage. Completed trajectories remain briefly visible;
projectiles and traces are included in save/load and replay state. Scouts, light tanks, and heavy
tanks retain distinct damage and projectile-speed profiles; their attack ranges are 5, 6, and 7
map units respectively. Weapon firing never clears a movement path: explicit attack orders pursue
and fire concurrently, while units moving, patrolling, or defending automatically fire at enemies
in range without abandoning their current locomotion or automation order.

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

The 1,000-unit interaction budget has a dedicated expected-behavior test:

```bash
.venv/bin/python -m pytest tests/test_thousand_unit_100fps.py
```

The denser 4K scout movement-and-collision budget is verified separately:

```bash
.venv/bin/python -m pytest tests/test_4k_thousand_scout_100fps.py
```

That contract independently measures static 4K rendering and command-plus-collision CPU work,
then measures an end-to-end second containing two 500-scout head-on move commands, ten
authoritative ticks, and 100 complete 3840 x 2160 software-surface draws. All scouts remain
selected and ordered, collision work must occur, and at least 750 must make progress.

## Current limitations and exclusions

Resources are a single integer balance per owner; builders do not gather resources, construction
cannot be canceled for a refund, and there is no technology tree, ballistic
terrain collision, armor, cover modifier, or tactical enemy AI.
Combat uses deterministic direct-hit projectile, range, damage, and cooldown profiles; it does
not currently model splash damage or missed shots. Visibility does not include
line-of-sight occlusion, last-known enemy observations, or a fog overlay. Save and replay schemas are
versioned and reject older incompatible schemas.

Geometry editing replaces a complete point, route, or region rather than offering
per-vertex handles. Multi-region selections are grounded and inspectable but are not yet
interpreted by language. Full fog of war, LM Studio or other AI
providers, voice, MCP, scouting reports, multiplayer, Unity, and a map editor are not
implemented in this phase.

The 4K 100 FPS acceptance test covers two command submissions, ten authoritative collision-heavy
simulation ticks, and 100 complete Pygame software-surface draws for 1,000 selected scouts, normal
buildings, UI panels, and mixed passable terrain. It validates the CPU-side frame construction at
3840 x 2160, not physical presentation. A real window uses a bounded logical surface and SDL
scaling, so monitor refresh below 100 Hz, VSync, desktop composition, WSLg, SDL renderer fallback,
GPU/driver behavior, and machine speed can still prevent 100 displayed refreshes per second.
`pygame.SCALED` is experimental in pygame-ce, and a backend without a fast renderer may fall back
to slower scaling. Worst-case 1,000-unit combat and choke-point throughput are also separate
workloads; the existing dense-choke regression currently covers 500 units.
