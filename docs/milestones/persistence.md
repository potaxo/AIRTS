# Persistence and replay diagnostics

[persistence.py](../../src/airts/adapters/persistence.py) and
[replay.py](../../src/airts/adapters/replay.py) own JSON schemas, field validation and migrations.
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
The additive `repair_target_id` defaults to null in supported older saves; patrol interruptions
reuse attack/repair targets and waypoint cursors.

Spatial buckets, numeric scratch, contact graphs/directions/leaders, navigation fields, per-tick
diagnostics and unfinished-automation query indexes rebuild from authoritative state. Presentation
history is not saved. Mobile bodies may share occupancy cells; real buildings remain exclusive.
Complete path events pack integer coordinates in memory and materialize `cells: [[x, y], ...]`
for inspection/export. Loading compacts paths again without changing their payload.
[events.py](../../src/airts/contracts/events.py) owns event queries and JSON Lines export.

## Supported formats and migrations

| Input | Current handling |
| --- | --- |
| Sandbox save v12 | Current write/read format; includes physical construction and recovery state |
| Sandbox saves v7-v9 | Recorded position becomes the unavailable historical Hold anchor; contact state starts fresh; return request/phase starts null/false |
| Sandbox save v10 | Retains anchors/contact/defense arrival state; initializes return request/phase to null/false |
| Sandbox save v11 | Requires recovery request/phase; migrates historical construction |
| Match save v4 | Current write/read format, including command-center production eligibility |
| Match saves v1-v2 | Start with empty opponent memory; survivors regroup on the next decision |
| Match save v3 | Retains opponent memory |
| Sandbox replay v16 / match replay v9 | Current diagnostic formats and exact verification, including mass-weighted contact response |
| Other replay/save formats | Explicit rejection; old replays need their original implementation, with no automatic replay migration |

Sandbox v7/v8 traffic reservations are validated and discarded, and obsolete movement state resets.
Older saves without physical construction restore recorded paid cost/work and create scaffolds
when builders reach valid sites; existing entities migrate as complete. Current saves require
construction progress and site references and validate owner, kind, position, payment and live jobs.
Match v2/v3 already retain physical construction. Match v1-v3 results are first checked under their
old factory/builder rule, then recomputed with command-center production; an old concluded match
can resume when both teams remain viable. Existing event history is retained.

Retired `congestion_stopped: false` and `blocked_ticks: {}` remain in JSON for compatibility;
supported input validates/discards historical values so they cannot suppress current movement.
Re-saving writes sandbox v12 or match v4. Migration uses current gameplay rules and does not
promise previous implementations' future trajectories. The adapter's exact tags/validators are
authoritative when changing compatibility.

Relative mass is immutable profile data resolved from the saved entity kind, not new per-entity
state. The contact/UI module extraction and mass response add no save fields: current and supported
older saves retain their existing migration paths and continue with current profile physics.
Sandbox replay v15 and match replay v8 predate mass-weighted response and are rejected with an
explicit contact-physics diagnostic; use the original implementation to verify their expectations.
Current replay recording/verification remains enabled, with no automatic replay migration.

## Validation and user entry points

[Persistence tests](../../tests/integration/test_persistence.py) cover restoration and continued
behavior; [replay tests](../../tests/integration/test_replay.py) cover the current diagnostic.
Existing exact continuation comparisons may evolve under the
[engineering test policy](../architecture/engineering.md#test-ownership).
[README](../../README.md#run) owns CLI examples and file handling. UI loading clears presentation
selection/inspection even when the adapter restores domain selections.
