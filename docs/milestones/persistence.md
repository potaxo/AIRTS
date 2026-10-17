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
| Sandbox save v25 | Current write/read format |
| Match save v19 | Current write/read format |
| Sandbox replay v32 / match replay v31 | Current diagnostic formats and exact verification |
| Other replay/save formats | Explicit rejection; old replays need their original implementation, with no automatic replay migration |

Current saves validate cross-field invariants before publishing a world: ownership and references,
construction sites/payment/progress, production kind/timing/progress, assignment conflicts, movement
recovery, combat targets, upgrade state, income cadence and developer-mode timing. Saved production
controllers carry at most the latest 128 produced entity IDs; longer input is malformed rather than
silently truncated. The event log retains the complete production audit trail.
The retired `congestion_stopped` placeholder and its normalization logic are removed from the current
entity format; recovery continues through the authoritative movement intent fields above.

Income settlement uses the saved tick's global one-second phase. Per-building income timers are
removed; cached owner totals are derived from complete buildings and their effective tiers on load.
The AI income multiplier is required authoritative state (integer 1 or 5), and its commands remain
recorded/replayable. Speed is a local presentation preference, omitted from saves and replay.
The new cadence and authoritative shape reject earlier save/replay tags without migration; current
exact verification remains active for income events, developer bonus timing and AI decisions.

Reload resumes paid production, construction and upgrades without a second charge or refund. Entity
profiles supply immutable mass, weapon, economy and capability data from each saved kind; derived AI
forecasts, spawn clearance, navigation fields and query caches are rebuilt. Reclaim and repair targets
remain authoritative references and cannot conflict with each other or automation control.

Defense parameters carry a required boolean marking production-managed groups. Several production
sources may reference the same controller; one live group per owner and exact target is allowed.
The loader rejects duplicate groups, malformed flags and production links to ordinary defenses.
The new grouping and faster fixed-turret cadence reject earlier save/replay tags without migration;
current-format command replay continues to verify grouped membership and combat events.

Artillery now predicts a fixed ground impact from current-tick observed movement, and blast traces
expire after three ticks. These replace the previous aiming/effect semantics, so earlier save and
replay tags are rejected without migration. The stored shell destination remains authoritative;
in-flight shells never predict again after load. Velocity lookup rows and their tick stamp are derived
movement scratch, reconstructed before the next firing pass; no extra history is required in saves.
Current command recording and exact replay diagnostics remain enabled.

Direct Move now selects compact marching for distant targets and formation micro for nearby
targets, as owned by [movement](movement.md#orders-and-routing). Queued geometry uses preceding
goals, and compact routes retain their shared weighted approach until the arrival area. Replay
tags change because replaying the same click can now produce different paths/events. Save tags
remain unchanged: allocated goals, paths and FIFO queues already capture the complete intent,
and loading does not reclassify an active leg. No saved group controller or cache is required.

Current replays retain external commands and expectations. AI decisions and automatic repair commands
are regenerated from authoritative state instead of being recorded as duplicate external inputs.
The adapter's exact tags and validators are authoritative; legacy migration branches and historical
per-version behavior lists are intentionally not maintained here.

The army-wave policy replaces independent raids, direct singleton reinforcement and whole-economy
production reservations. Existing cohort phases/objectives remain authoritative; their first active
cohort leads the common army objective. Match saves additionally require finite, nonnegative
`growth_credit` and a non-future `budget_tick`. Credit is an earmark, not an independent resource
balance, and the next budget update reconciles it against actual cash and outstanding jobs.
Earlier match save/replay tags are rejected without migration. Sandbox formats are unchanged;
current match save continuation and exact command-replay diagnostics remain enabled.

Economy-aware mixed production derives composition from owned/queued units, effective income and
available military funds. It adds no authoritative memory or save fields.
Changed purchasing, specialist savings and continuous-loop pauses regenerate different AI commands,
so earlier match replay tags are rejected. Exact verification and
current-format match save continuation remain active.

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
