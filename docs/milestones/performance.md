# Performance contract

[Design](../design.md) owns scope. These are acceptance requirements, not evidence that the current
checkout passes. Preserve workload sizes, geometry, durations, collision thresholds and metrics.
Linked tests own exact fixtures/assertions. [AGENTS](../../AGENTS.md#validation) owns marker selection,
native-Windows validation, repeated runs and artifact recording; packaging CI is functional evidence.

## FPS and rendering

**Real FPS = 1000 / p99 completed-frame milliseconds.** Every 100 Real FPS gate requires p99 <=10 ms;
sustained native-4K complex battle requires >=80 Real FPS (p99 <=12.5 ms) and reports 100 FPS as its
optimization goal. Use [metrics](../../src/airts/presentation/metrics.py) and the
[completed-frame probe](../../tests/performance/frame_pacing.py). Average Submit FPS is diagnostic.
Commands, scheduled ticks, drawing and per-frame GPU completion stay inside consecutive samples;
setup/JIT warm-up is outside timing. Tick budgets are separate. Never adjust thresholds/percentiles
to pass a run. Offscreen/app FPS does not establish physical monitor scan-out.

Current simulation uses serial CPU kernels at 10 Hz; this is a baseline rather than a required
execution strategy. Optimizations follow [engineering policy](../architecture/engineering.md#change-policy)
and preserve gameplay safety and these gates. Large-scene construction drains old garbage before
live frames; automatic GC remains enabled. Frontend ceiling is 1,000 FPS with VSync requested off.
Native OpenGL 3.3 uses `OPENGL | DOUBLEBUF | RESIZABLE`, never `SCALED` or silent fallback. Hardware
evidence uses native WGL, excluding llvmpipe, softpipe, SwiftShader, GDI Generic and Microsoft Basic
Render Driver. WSLg/Wayland compatibility is not native-Windows acceptance evidence.

Rendering preserves all terrain/grid elements, units, buildings, ownership, selection and inspected
health. Large selections use tint/aggregate details and 1-32 representative routes including the
inspected route. No visual reduction may skip units, collision or simulation.
Terrain uploads once per map as exact nearest RGB texels on one world rectangle; all eight-cell
grid lines remain world primitives. Camera pan/zoom/resize changes uniforms, not terrain or dynamic
world buffers. Dynamic capacity persists; geometry rebuilds on relevant tick/selection/inspection
changes. [Appearance](../architecture/appearance.md#shared-style) owns display-only material detail.
Software maps scale a viewport-sized crop of a coarse cell map or a lazily cached fixed-resolution
detail source; zoom never allocates a full zoomed world.

Projectiles, traces, assembly glow, resource sites, blueprints, placement/scaffold feedback, spatial
references and drafts use cached world primitives. Immutable traces reuse geometry until expiry;
active projectiles update per tick. Concave fills triangulate on edit. Grid/line widths, progress
height, text and projectile cores stay fixed in pixels. Construction percentages/region names use
a cached text atlas. Panels/font textures upload dirty regions; camera changes never dirty the
whole canvas. Status refreshes in three-tick buckets; explicit interaction remains immediate.
The minimap caches one terrain texel per cell and its scaled overview; contacts refresh with the
status bucket or explicit commands/selection. Camera changes update only its small overlay rectangle,
reusing terrain and contact surfaces and all world GPU buffers. Map replacement releases its sources.
Sidebar status updates upload the populated selection area rather than its empty lower portion;
selection replacement, scrolling and changing upgrade groups refresh the full selection viewport.

Artillery prediction reads original positions by binary search of the existing sorted movement
workspace rows, allocating no per-army velocity/index table. It solves one scalar intercept per actual
shot; ordinary weapons and in-flight shells do no prediction work. It performs no extra spatial query,
pathfinding or simulated future ticks. Shared blast expiry limits both backends' active effect geometry.

GPU interpolation mixes previous/current centers and visual headings without per-frame rebuilds
or simulation feedback and resets on load/reset. New world-batch packing can stage for one
presentation call after a tick: that call displays the previous current endpoint, and the next
publishes the complete new snapshot. This adds at most one presentation interval to the 100 ms
interpolation delay and separates tick work from batch assembly. Commands, selection changes,
births, resets and skipped ticks bypass staging. A removed entity may retain its old endpoint for
the staged call; every survivor is still rendered. Inspection world markers can follow on the next
call. Tick-driven overlays stage on the first interpolation frame after a tick (at most one frame
text delay). Software caches composed Surfaces, rebuilds on the following presentation call and
catches up if another tick arrives. Its interactive
framebuffer is bounded; native-4K test Surfaces do not prove native-4K interactive software rendering.
OpenGL resolution presets span 1280x720 through 3840x2160; settings reports p95 frame/render/simulation/
swap wait. Renderer correctness and resource lifetime belong to the linked integration tests.

## Required workloads

Behavior and timing variants retain identical full workloads; only timing enforces wall-clock
budgets. Software and GPU gates measure different costs and remain separate.
Production-density stress fixtures explicitly use five-tick unit profiles to retain the same spawn
counts and frame/tick windows independently of normal gameplay production balance. The dedicated
production integration tests use the real per-kind profiles and verify their completion boundaries.
The mixed production/arrival integration scenario explicitly enables developer income so
opening income balance cannot reduce the required population. Selected AI budget/placement tests
use a declared 25/s established-economy fixture; upgrade and opening-income coverage uses real profiles.

| Executable contract | Workload and gate |
| --- | --- |
| [Selected army](../../tests/performance/test_thousand_unit_100fps.py) | 1,000 light tanks, 80 x 60 mixed terrain; move/patrol/defend each retain selection/ownership, advance ten ticks, move >=100 units, draw 100 software frames at >=100 Real FPS |
| [4K software](../../tests/performance/test_4k_thousand_scout_100fps.py) | Two opposing 500-scout formations plus four buildings; 3840 x 2160; separate draw/CPU budgets and combined 100-frame/ten-tick >=100 Real FPS; >=750 move and orders remain active |
| [4K OpenGL](../../tests/performance/test_opengl_thousand_scout_100fps.py) | Same scout fixture; CPU submission plus hardware end-to-end 100-frame/ten-tick gates; real collision, >=750 moving scouts, pixel readback, per-frame GPU completion, >=100 Real FPS |
| [Sustained battle](../../tests/performance/test_sustained_complex_battle_performance.py) | 500 vs 500, each 70/20/10 scout/light/heavy, 80 x 60 mixed terrain; friendly line defense plus enemy move; 30 ticks/300 native-4K hardware frames, ten interpolation samples/tick, six inspection changes; real projectiles, damage, collision, pixel readback, >=80 Real FPS required, 100 Real FPS optimization goal |
| [Continuous production](../../tests/performance/test_crowd_congestion_performance.py) | 296 defenders + four factories ->304 defenders; 100 software frames at 1920 x 1080/ten ticks; stable unique stations, separation >=90% contact distance, no building intrusion, >=100 Real FPS |
| [Opponent decisions](../../tests/performance/test_opponent_performance.py) | 500 vs 500 mixed units, 60 ticks; original 80 x 80 opening-budget case, a 320 x 320, 32-builder, 100,000-resource surplus case, and the same large-map army/budget with 50 extra factories, 40 synthesizers, 36 turrets, seven repair hubs, a command center and extractor; unique bounded squads and per-decision command/candidate/forecast limits; timing requires complete tick p95 <100 ms and reports AI decisions/commands, starting funds, map size, builders and developed holdings |

The native-4K large-map camera case in the OpenGL test above retains all 320 x 320 terrain colors,
checks cell-center readback at three zoom levels, and requires 100 synchronized frames at >=100
Real FPS with camera changes. It measures rendering separately from army throughput.

Renderer startup, primitive counts, buffer reuse, release/error paths, and interpolation correctness
live in [presentation integration tests](../../tests/integration/test_opengl_presentation.py).

## Crowd and routing

| Executable contract | Additional gates and retained behavior |
| --- | --- |
| [Crowd](../../tests/performance/test_crowd_congestion_performance.py) | 999 attackers: >=24 in range after 220 ticks, all in-range attackers stopped, tick p95 <100 ms. 400 scouts around a 2x2 defense: >=90% spacing, tick p95 <100 ms. All 400 cross the nine-cell bridge within fixture limits. |
| [Large army](../../tests/performance/test_large_army_performance.py) | All 1,000 gathering members receive routes within 32 ticks using the global 32-route allowance. 500-unit choke retains the 6,000 collision-pair limit. Cold/changed 320x320 local and cross-river targets require command <=250 ms for two scouts and <=750 ms for 1,000. Other move/patrol/repair/focus, line, choke and opposing-army cases retain full participants. |
| [Behavior](../../tests/integration/test_large_force_behavior_regressions.py) | Identity, swept clearance, speed, front-rank continuity, 307-scout bridge progress, held-formation traffic/recovery and building clearance. Shared defense: 160 unique stations, 159 after a casualty with <=1 remap. Four factories: 132 spaced stations. |
| [Severe recovery](../../tests/integration/test_overlap_recovery.py) / [timing](../../tests/performance/test_overlap_recovery_performance.py) | Behavior retains 100/400/1,000 bodies, 80/50/30% spacing and coincident centers, internal extraction and continued settling. Timing retains all 1,000 bodies and 300 ticks for 30% spacing/coincident centers with tick p95 <100 ms. |

Route dispatch latency is not arrival latency. No gate permits omitted queued/stationary members
or missing passive/recovery collision work. Reinforcements retain valid stations; contraction may
replace obsolete outer slots. Stable identities and safe waiting take precedence over rematching
or physically impossible passage. [Movement](movement.md) owns these gameplay rules.
Severe-recovery evidence includes first progress, squad completion, longest stall, overlap depth/
pairs, spatial distribution, passive settling, work counters and tick/frame tails. These preparations
are injected separately from normal spawn/arrival behavior; preserve the fixture's complete run.

## Contact solver measurement

`collision_pair_check_count` counts each evaluated disc-pair relationship in goal/spawn capacity,
prediction, admitted fringe discovery, coincidence embedding and seed/final certification.
Repeated visits in separate phases or prediction sweeps count again, including pathless yielding
and recovery. Joint anchor projection also counts each pair constraint evaluated when admitting
or checking its active set, even when the normal is already cached. Sparse matrix-vector products
and scalar projection within an individual body's already constructed planes are numerical work
rather than additional pair checks; the complete tick timer includes them. Capture the
counter before and after a command and report its delta with the command duration, before the next
tick resets diagnostics; tick samples alone do not include destination allocation.
`contact_candidate_count` counts non-self candidates returned
by the numeric contact solver's local queries, including repeated visits and subsequently skipped
same-component candidates. Destination allocation and spawning contribute to collision checks;
their separate broadphase candidates are not included in this numeric-solver counter.
`contact_coordination_work` counts prediction pair evaluations, admitted fringe queue visits and
component member-direction evaluations. Joint anchor projection additionally counts its actual
edge and endpoint visits, active-set checks and sparse product/dot visits. It is an operation
counter, not a count of every scalar projection operation. `contact_component_count` counts regions
admitted for release or joint return. `contact_blocked_component_count` counts incompatible resting
destinations, zero certified release capacity and exhausted joint return directions requiring a
new opening request.
The latter reports the solver's missing certificate and does not prove that reachable space is
physically absent. `movement_replan_count` counts admitted stalled-path and anchor-route attempts,
rather than only newly built shared navigation fields. These diagnostics do not affect trajectories
and reset each tick; they are disposable after load.
Spatial bucket filtering is broadphase work, not a replacement for required collision constraints.
Stable regions still pay for linear state packing/indexing and local overlap discovery, but do not
run displacement certificates without an intent/correction. Report tick timing separately from
completed-frame p99 and Real FPS. Constructor/JIT warm-up stays outside both timing streams;
specified simulation commands, passive submission, Hold/defense recovery and replanning stay inside.
Movement ordering geometry uses reusable numeric scratch; recovery flags share its packed input
while request ages retain integer precision. This does not omit stationary bodies or certification.

Joint anchor projection's refinement allowance is `8 * (P + E + sum(degree_i ** 2))`, for P bodies,
E retained edges and endpoint degrees. Each CG solve has dimension <=`min(active_edges, 2 * P)`
and may stop on residual, a multiplier boundary or allowance exhaustion. Count actual active-set,
edge/endpoint and sparse-product/dot visits, skipping inactive zero edges. All constraint admission,
final scans, both contact sweeps and full swept/static certification remain measured and mandatory.
Intermediate numerical termination cannot omit safety checks; excluded projection pairs still
contribute their evaluated certification costs. Algorithm behavior belongs to [movement](movement.md).

Buckets and scratch storage do not imply universal linear time: dense chains/coincident centers
can produce quadratic candidates or worse refinement. Never truncate neighbors or remove safety
certification to meet a gate. Compare tick tails separately from completed-frame tails.
Sustained battle reports renderer, 300 completed frames/30 ticks, p99, Real FPS, floor and goal-met
properties. Its cadence stays 100 frames per simulated second despite the 80 FPS floor.

## Visual inspection

The non-discovered [GUI scenarios](../../tests/gui_scenarios/crowd_congestion_gui.py) use the normal
OpenGL app and block until closed. From the repository root, run:

```powershell
.\.venv\Scripts\python -m pytest -s tests/gui_scenarios/crowd_congestion_gui.py
```

Use `-k` to select a scenario; close its window to proceed to the next selected case.
Other modules in that directory cover large armies, behavior regressions, and rendering/battle.
Visual inspection complements automated progress/timing checks; a screenshot alone cannot prove
liveness, smoothness, or physical display FPS.
