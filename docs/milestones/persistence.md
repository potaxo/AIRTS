# Persistence and replay diagnostics

[persistence.py](../../src/airts/adapters/persistence.py) and
[replay.py](../../src/airts/adapters/replay.py) own JSON schemas and field validation.
This document owns reliability and compatibility policy; [design](../design.md) owns release priorities.

## Save reliability and replay policy

Supported saves restore coherent ownership, resources, entities, selections, queues, automation,
combat and movement intent. Continued play must satisfy gameplay contracts; future trajectories
and events need not exactly match uninterrupted play under different execution. Temporary worker
state must be completed, captured or reconstructible at a save boundary, never a partial world.

Command replay is an optional debugging tool. The current adapter re-simulates recorded commands
and compares the final `snapshot()` and ordered events with recorded expectations, rather than
the full `export_state()`. Normal gameplay does not periodically re-run that verification.
Seeds and useful command/failure records support best-effort reproduction; exact replay is not
guaranteed across worker counts, hardware or versions. Execution changes must document retained
diagnostics, version/reject incompatible files when needed, and report verification failures.
Existing recording and verification remain active until intentionally changed. Future multiplayer
must define its own synchronization and determinism requirements.

## Authoritative and derived data

Saves include map/team configuration, results, opponent memory, resources, entities, selections,
queues, automation assignments/lifecycle, damage, projectile/trace state, events and ID counters.
AI commands are internal decisions regenerated during replay, not duplicated as external inputs.

Movement continuation retains Hold anchors, internal recovery waypoints/retry ticks, quiet timers,
failed-static-route obstacle snapshots, defense stations/arrival state, progress, return-request age
and released phase. A pathless Hold return can own progress toward its anchor. Request ticks must
be null or non-boolean integers within the saved tick; release requires a request. Restored recovery
owners must be movable Hold units or valid unpaused arrived defenders. Progress must agree with
its anchor or ordinary next waypoint; malformed intent is rejected, not normalized away.
Patrol interruptions reuse attack/repair targets and waypoint cursors.

Spatial buckets, numeric scratch, contact graphs/directions/leaders, navigation fields, per-tick
diagnostics and unfinished-automation query indexes rebuild from authoritative state. Presentation
history is not saved. Mobile bodies may share occupancy cells; real buildings remain exclusive.
Complete path events pack integer coordinates in memory and materialize `cells: [[x, y], ...]`
for inspection/export. Loading compacts paths again without changing their payload.
[events.py](../../src/airts/contracts/events.py) owns event queries and JSON Lines export.

## Supported formats

Only the latest save formats are supported. Prefer the current implementation and optimization
without preserving backward compatibility or maintaining migrations. Older saves may become
unloadable after an update; reject outdated schema tags explicitly. Change the current schema tag
when the authoritative shape or required semantics change. Current-format saves must still restore
coherent state, and malformed data must fail with useful diagnostics. Replay remains a current-format
diagnostic, without automatic migration.

| Input | Current handling |
| --- | --- |
| Sandbox save v16 | Current write/read format; includes developer mode, its income timer and per-assignment auto repair |
| Match save v9 | Current write/read format with victory based on surviving assets |
| Sandbox replay v21 / match replay v15 | Current diagnostic formats and exact verification |
| Other replay/save formats | Explicit rejection; old replays need their original implementation, with no automatic replay migration |

Current saves require construction progress and site references and validate owner, kind,
position, payment and live jobs. Match results use current surviving-asset victory rules.
The adapter's exact tags/validators are authoritative. Legacy migration branches are removed;
old sandbox and match saves are rejected before world reconstruction.

Forward expansion, siege production and continuous cleanup regenerate AI decisions from the saved
squad memory and current world. They introduce no additional memory fields. Match save v9 rejects
earlier result semantics, and match replay v15 rejects earlier AI trajectories and events; sandbox
formats are unchanged. Current continuation and command replay verification remain enabled.

Developer mode and its partial-second tick counter are authoritative save/snapshot state.
Current formats require a boolean toggle and a counter within the enabled one-second interval;
disabled mode requires a zero counter. Toggle commands remain recorded and replayable, and exact
verification includes the bonus income events and timer. Earlier save/replay tags are rejected.

Patrol, defense and production parameters require a boolean auto-repair choice. Saves retain it
alongside emergency repair phases and suspended assignments. The new-area UI preference remains
local presentation state. Generated automatic repair commands are regenerated during replay,
not recorded again as external inputs; creation and toggle commands retain the player's choice.
Earlier schema tags are explicitly rejected without migration; current replay verification remains active.

Relative mass is immutable profile data resolved from the saved entity kind, not new per-entity
state. Current saves continue with current profile physics.
Sandbox replay v15 and match replay v8 predate mass-weighted response and are rejected with an
explicit contact-physics diagnostic; use the original implementation to verify their expectations.
Current replay recording/verification remains enabled, with no automatic replay migration.
Reclaim targets are authoritative entity references and cannot conflict with repair or automation
control. Saves retain approaching builders and fixed turret attack orders. Sandbox replay v16 and
match replay v9 predate range-based repair arrival and are explicitly rejected. New kinds and cadence
resolve through immutable profiles, while refunds/removal are regenerated from reclaim commands;
internal removal is never recorded a second time as an external command.

The current command update retains per-unit FIFO movement destinations, explicit attack gathering
positions, and capability-based combat support. Patrol arrival and area-defense distribution change
execution trajectories and events; older save/replay tags are rejected without migration. Current
command recording and exact verification remain enabled, with queue/combat continuation tests.

Terrain-adjusted speed derives from the saved map and immutable mobile profile each tick;
it adds no authoritative save state. Current replay recording and exact verification remain
active. Sandbox replay v17 and match replay v10 predate terrain-adjusted movement and are rejected
explicitly, since their recorded trajectories and events may differ; verify those recordings
with their original implementation.

## Save execution and management

The UI captures authoritative state between ticks. Background writing reads only that captured
state and the append-only frozen event records; it never reads or mutates the live simulation.
Packed path events expand one at a time during encoding. Compact JSON is encoded in bounded
sections instead of pretty-printing the entire document through Python's incremental encoder.
All events and commands remain in the save and replay diagnostics are unchanged.

Writes use a unique temporary file in the destination directory, flush and synchronize it, then
atomically replace the destination. Failure before replacement preserves the existing save;
temporary files are cleaned up and errors are shown. The UI admits one save at a time, blocks
load/overwrite/delete while writing, and completes a pending write before normal window exit.
CLI saves remain synchronous and use the same snapshot and atomic writer.

The modal save browser lists file names, modification times and sizes without parsing every save.
It pauses the world while open, preserves the player's pause setting on close, and supports new
timestamped saves, overwrite, load, delete, refresh and scrolling. Overwrite, load and delete require
an explicit in-page confirmation. A failed load keeps the current world and page intact.
[README](../../README.md#controls) owns entry points, paths and shortcuts.

## Validation and user entry points

[Persistence tests](../../tests/integration/test_persistence.py) cover restoration and continued
behavior; [replay tests](../../tests/integration/test_replay.py) cover the current diagnostic.
Existing exact continuation comparisons may evolve under the
[engineering test policy](../architecture/engineering.md#test-ownership).
[README](../../README.md#run) owns CLI examples and file handling. UI loading clears presentation
selection/inspection even when the adapter restores domain selections.
