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
Full recording is opt-in through the startup Debug mode; ordinary play does not retain event or
command history. Save/load still captures the authoritative world and ongoing intent. Headless
`Simulation` and adapter callers retain their diagnostic default and can pass `record_history=False`.
The frontend passes its local Debug choice explicitly on creation and load. The choice is not
saved world state. Full-history replay verification remains available in Debug mode; exporting
a replay with recording disabled fails explicitly before replacing an existing output. Enabling
Debug after an unrecorded period cannot recover that period's history. Future multiplayer
must define its own synchronization and determinism requirements.

## Authoritative and derived data

Saves include map/team configuration, results, opponent memory, resources, entities, selections,
queues, automation assignments/lifecycle, damage, projectile/trace state and ID counters.
The existing events/command-history arrays are empty when recording is off; Debug saves retain
their full contents. Loading with recording off validates those arrays without constructing or
retaining historical events. JSON decoding of an existing file still reads its full contents.
An optional `command_count` stores the monotonic input revision independently of history, so cache
invalidation and formation command identities work in both modes and after loading. Current-format
files without it derive the revision from their complete command history. This additive metadata
does not change world restoration rules or require a format-tag change. No migration is performed.
AI commands are internal decisions regenerated during replay, not duplicated as external inputs.

Completed/canceled/failed automation members and sources are historical references: destroying a
former builder, factory or repair destination does not invalidate that history. Nonterminal repair
members in the DONE phase have already returned control and need no repair assignment or surviving
entity; their saved phase/return data remain inspectable. Unfinished members still require coherent
live references and assignments. These are current-format restoration rules, not data repair or
migration; invalid active references and lost control claims remain load errors.

Movement continuation retains Hold anchors, internal recovery waypoints/retry ticks, quiet timers,
failed-static-route obstacle snapshots, defense stations/arrival state, progress, return-request age
and released phase. A pathless Hold return can own progress toward its anchor. Request ticks must
be null or non-boolean integers within the saved tick; release requires a request. Restored recovery
owners must be movable Hold units or valid unpaused arrived defenders. Progress must agree with
its anchor or ordinary next waypoint; malformed intent is rejected, not normalized away.
Patrol interruptions reuse attack/repair targets and waypoint cursors.

Patrol anchors denote arrival areas with reachable approaches. Saves retain their cursors and
paths, along with defense stations and deployment slots. Entry-point caches and multi-goal fields
rebuild after load. Formation geometry and arrival rules are owned by
[movement](movement.md#patrol-defense-and-combat).

Spatial buckets, numeric scratch, contact graphs/directions/leaders, navigation fields, per-tick
diagnostics and unfinished-automation query indexes rebuild from authoritative state. Presentation
history is not saved. Mobile bodies may share occupancy cells; real buildings remain exclusive.
Movement feedback masks/work lists and AI decision-position batches are derived scratch as well.
Restoration shares equal immutable entity path/recovery waypoints within a load; each entity keeps
its own mutable path list. No process-wide point cache or coordinate approximation is introduced.
Large file loads drain decoding garbage after restoration at the load boundary; automatic GC
remains enabled during play.
Batching these calculations retains world fields, commands and event payloads; save/replay tags
remain unchanged. Current-format files restore the same movement and task intent. Exact diagnostic
replay remains enabled when history recording is selected.
Queued search-work allowances are also derived. A cold navigation cache after load can defer a
queued leg to a later tick without losing its intent or FIFO tail. This scheduling optimization
does not change authoritative fields or format tags; exact continuation remains diagnostic.
Complete path events pack integer coordinates in memory and materialize `cells: [[x, y], ...]`
for inspection/export. Loading compacts paths again without changing their payload.
Visibility keeps a shared current observation tick for visible cells and stores timestamps when
cells leave sight. Public queries and save export materialize the same exact per-cell last-observed
values. This representation and the per-factory live production index are derived runtime data;
neither changes save/replay tags or command/event formats.
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
| Sandbox save v30 | Current write/read format |
| Match save v24 | Current write/read format |
| Sandbox replay v41 / match replay v44 | Current diagnostic formats and exact verification |
| Other replay/save formats | Explicit rejection; old replays need their original implementation, with no automatic replay migration |

Factories now retain optional production deployment independently of construction and queues:
the line/area, auto-repair setting and shared defense link survive restoration before or after the
first unit is produced. Only factories may carry this state; malformed targets, flags and controller
links are rejected. The added authoritative entity field and `SetProductionDefenseCommand` require
new save/replay tags; previous formats are rejected without migration. Current-format continuation
and command-replay verification remain supported.

Direct and queued repair targets may now identify paid unfinished buildings. Builder and hub
assistance restores that intent, the same physical building and its synchronized live construction
job without another charge. Blueprint clearance paths are ordinary saved movement intent; they
do not become external player commands. These expanded repair-target semantics and regenerated
clearance/service events reject older save/replay tags without migration. Current-format continuation
and command-replay diagnostics remain enabled.

Current saves validate cross-field invariants before publishing a world: ownership and references,
construction sites/payment/progress, production kind/timing/progress, assignment conflicts, movement
recovery, combat targets, upgrade state, income cadence and developer-mode timing. Saved production
controllers carry at most the latest 128 produced entity IDs; longer input is malformed rather than
silently truncated. In Debug mode the event log retains the complete production audit trail.
Movement persistence contains active intent and recovery progress. Unused congestion placeholders
are neither written nor restored; derived navigation and coordination state rebuilds after load.

Income settlement uses the saved tick's global one-second phase. Per-building income timers are
removed; cached owner totals are derived from complete buildings and their effective tiers on load.
The AI income multiplier is required authoritative state (integer 1 or 5), and its commands remain
recorded/replayable. Speed is a local presentation preference, omitted from saves and replay.
The new cadence and authoritative shape reject earlier save/replay tags without migration; current
exact verification remains active for income events, developer bonus timing and AI decisions.

Reload resumes paid production, construction and upgrades without a second charge or refund.
Production already completed at a blocked exit may retain excess progress from repeated pause/resume
cycles. Paid completed progress restores as recorded; runtime no longer increments it while waiting
for capacity. Unpaid excess progress remains malformed. This fixes current-format restoration,
without a schema change or migration.
Entity profiles supply immutable mass, weapon, economy and capability data from each saved kind; derived AI
forecasts, spawn clearance, navigation fields and query caches are rebuilt. Reclaim and repair targets
remain authoritative references and cannot conflict with each other or automation control.

Turret footprint/display size and repair-hub range balance changes use the current profiles
when loading existing current-format saves. Stored fields and format tags are unchanged;
recordings from before the balance change may produce different combat or repair results.
Current command recording and replay verification remain available.

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

Direct Move gathers by default; strict formation is an explicit persistent setting, as owned by
[movement](movement.md#orders-and-routing). The direct
FIFO replaces the point-only movement queue with tagged move/attack/repair orders. It stores
allocated destinations, shared movement goals, target IDs and click positions; current active
work retains its path and attack/repair target. Target orders retain their click positions but
execute and display against the referenced live entity. Queues validate kind, finite coordinates,
capabilities, references and assignment conflicts. Earlier save/replay tags are rejected without
migration; they cannot represent mixed FIFO intent. Navigation fields remain derived, loading
does not reclassify active legs, and current exact replay diagnostics remain enabled.
Capability filtering and firing during persistent pursuit retain the same saved queue/active-intent layout.
Replay tags cover these command and combat semantics too; earlier replay tags are rejected because
their regenerated outcomes can differ. Current saves resume the retained tasks under the current rules.
Direct movement and pending FIFO work now exclude automatic combat-support recruitment for both
human and AI orders. Saved paths, queues and existing pursuits retain their representation; current
saves resume under this priority rule. Replay tags change because regenerated support events and
trajectories can differ. Current-format continuation and exact replay diagnostics remain enabled.

Strict direct formations add required nullable membership, relative-offset, active goal and leg-ID
fields to entities, and goal/leg-ID metadata to queued moves. The leg identity separates repeated
destinations for cohort completion barriers. Loading validates mobile membership, one owner per
cohort, finite bounded offsets, paired goal/leg fields, coherent translated destinations, consistent
cohort goals and conflicts with active work. Current saves retain these offsets through narrow
passages; per-tick cohort grouping and speed/steering arrays rebuild after load. The new authoritative
shape rejects older save/replay tags without migration. Ordinary Move no longer serializes a
`gathering` option; movement intent comes from the explicit formation setting and recorded leg.

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

Surplus expansion derives its spending horizon, weighted construction deficits, nearby infill,
rotating fallback, street lattice, worker demand and batch sizes from current entities/jobs, actual
available cash, map geometry and the saved tick. Placement counters and macro command opportunities
are decision-local scratch. This replaces the sparse one-factory/income/turret-per-sector policy and
prevents repeated tactical orders from starving funded macro work. No new save fields or migrations
are introduced; existing current-format match saves restore and enter the new policy on their next
decision. Changed internally regenerated AI orders invalidate earlier match replay expectations.
Continued reinvestment, concurrent upgrades, soft regional
density, full nearby force estimates and fair multi-cohort dispatch derive from existing world and
cohort memory; they add no authoritative fields or save migration. Sandbox replay is unaffected, and current-format replay
verification remains enabled.

Mass-assault intent reuses cohort objectives/phases and ordinary entity attack intent. Batched
repair enrollment likewise retains the existing saved destinations, phases and return ownership;
source geometry, connectivity and routing fields are derived. Current saves, including ongoing
repairs and fragmented armies, remain loadable. The new army decisions and distance-ranked deferred
repair selection change regenerated trajectories/events, so older sandbox/match replay tags are
rejected without migration. Current recording and replay diagnostics remain enabled.

Surplus background macro scheduling, uncapped parallel funded sites and standing military
production reuse existing construction/production automations, budget timestamps/credit and cohort
memory. They add no save fields: current saves restore ongoing finite work, manual pauses and
standing production before entering the new policy. Changed internal AI orders advance only the
match replay tag; sandbox replay is unchanged by this AI policy update.

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
