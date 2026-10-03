# Performance contract

[Design](../design.md) owns scope. These are acceptance requirements, not a claim that every run
or machine passes. Preserve workload sizes, geometry, durations, collision thresholds, and metrics.
Exact fixtures/assertions are owned by the linked tests; historical benchmark prose is not evidence
for the current checkout.

## FPS and rendering

**Real FPS = 1000 / p99 completed-frame milliseconds.** Every 100 Real FPS gate requires p99 <=
10 ms, using `real_fps_from_frame_times` in [metrics.py](../../src/airts/presentation/metrics.py)
and [the shared probe](../../tests/performance/frame_pacing.py).
Average Submit FPS/elapsed throughput is diagnostic only. Record consecutive completed frames;
GPU tests synchronize every frame before sampling. Commands, scheduled ticks, rendering, and
required synchronization stay inside the measured frame stream. Do not tune the percentile or
threshold to pass. Simulation-tick budgets are separate from FPS. Physical monitor/compositor
cadence needs external measurement; neither offscreen GPU tests nor application FPS prove scan-out.

Simulation remains deterministic, single-threaded CPU work at 10 Hz. The frontend has a 1,000 FPS
ceiling and requests VSync off. Native OpenGL 3.3 uses `OPENGL | DOUBLEBUF | RESIZABLE`, never
`SCALED` or silent software fallback. Context failures are diagnostic. Windows verification uses
the native WGL backend. The app retains a WSLg/Wayland compatibility branch, but repository
development and acceptance verification use native Windows as specified in [AGENTS](../../AGENTS.md).
Software rasterizers (including llvmpipe, softpipe, SwiftShader, GDI Generic, and Microsoft Basic
Render Driver) cannot count as hardware evidence. Dependencies stay in [pyproject](../../pyproject.toml).

OpenGL preserves every terrain/grid element, unit, building, selection, and inspected health bar.
Large selections use tint/aggregate details and 1-32 representative routes, including the inspected
route; visual detail reduction cannot hide units, drop ownership, or disable collision/simulation.
Terrain uploads on transform changes; dynamic buffers retain capacity and rebuild at tick/relevant
UI changes. Normal batches are terrain, entities, bounded lines, and UI composition. GPU shapes
include projectiles, retained traces, and assembly glow. Font/interaction textures upload dirty
regions; status refreshes in three-tick buckets and explicit interaction stays immediate.

GPU interpolation mixes previous/current fixed-tick centers without per-frame buffer rebuilds or
feedback into authoritative state. It adds at most 100 ms visual latency and resets on load/reset.
Tick-driven overlay updates stage onto the first interpolation frame after a tick, adding at most
one frame of text latency. Explicit software mode caches complete composed Surfaces, reconstructs
on the following presentation call after a tick, and catches up immediately if another tick arrives.
The interactive software backend scales a bounded logical framebuffer; a native-4K software test
Surface does not establish native-4K interactive software rendering.
Native OpenGL resolution presets span 1280 x 720 through 3840 x 2160; settings exposes p95 frame, render,
simulation, and swap-wait diagnostics. Projectile cores remain small and fixed in pixel size.

## Required workloads

Setup/warm-up is outside timing; specified commands and ongoing work are inside. Retain all
participants and assertions. Separate backend tests measure different costs and are not duplicates.

| Executable contract | Workload and gate |
| --- | --- |
| [Selected army](../../tests/performance/test_thousand_unit_100fps.py) | 1,000 light tanks, 80 x 60 mixed terrain; move/patrol/defend each retain selection/ownership, advance ten ticks, move >=100 units, draw 100 software frames at >=100 Real FPS |
| [4K software](../../tests/performance/test_4k_thousand_scout_100fps.py) | Two opposing 500-scout formations plus four buildings; 3840 x 2160; separate draw/CPU budgets and combined 100-frame/ten-tick >=100 Real FPS; >=750 move and orders remain active |
| [4K OpenGL](../../tests/performance/test_opengl_thousand_scout_100fps.py) | Same scout fixture; CPU submission plus hardware end-to-end 100-frame/ten-tick gates; real collision, >=750 moving scouts, pixel readback, per-frame GPU completion, >=100 Real FPS |
| [Sustained battle](../../tests/performance/test_sustained_complex_battle_100fps.py) | 500 vs 500, each 70/20/10 scout/light/heavy, 80 x 60 mixed terrain; friendly line defense plus enemy move; 30 ticks/300 native-4K hardware frames, ten interpolation samples/tick, six inspection changes; real projectiles, damage, collision, pixel readback, >=100 Real FPS |
| [Continuous production](../../tests/performance/test_crowd_congestion_performance.py) | 296 defenders + four factories ->304 defenders; 100 software frames at 1920 x 1080/ten ticks; stable unique stations, separation >=90% contact distance, no building intrusion, >=100 Real FPS |

Renderer startup, primitive counts, buffer reuse, release/error paths, and interpolation correctness
live in [presentation integration tests](../../tests/integration/test_opengl_presentation.py).

## Crowd and routing

[Crowd scenarios](../../tests/performance/test_crowd_congestion_performance.py) require a 999-attacker
focus scenario to have at least 24 attackers in range after 220 ticks, with every in-range attacker
stopped and tick p95 <100 ms; 400 scouts around a 2 x 2 defense to settle with >=0.90 center spacing
and tick p95 <100 ms; and all 400 scouts to cross the nine-cell bridge on a 120 x 80 map within the
test's tick limit and overlap tolerance. Capacity-specific counts do not
replace the independent 1,000-unit performance gates. Tiny patrols, stationary hostile anchors,
and bridge-turn lookahead remain distinct regressions.

[Large-army scenarios](../../tests/performance/test_large_army_performance.py) retain 1,000-unit
move/patrol/repair/gathering/focus commands, gathering expansion/contraction, and 500-unit line,
repair, delayed-route, choke, opposing-army, and determinism checks. Keep per-tick route budgets
and the 6,000 collision-pair limit in the 500-unit choke; do not omit queued members to meet timing.

[Behavior regressions](../../tests/integration/test_large_force_behavior_regressions.py) preserve
identity (no exact position exchanges), per-tick speed bounds, unobstructed front-rank continuity,
307-scout four-cell bridge progress, traffic past held formations with bounded displacement and
safe anchor recovery, and building clearance. Contact and Hold behavior are defined in
[runtime](runtime.md#contact-yielding-and-hold-anchors).
The movement scenarios retain continuous contact clearance and per-tick speed checks; passive
displacement and anchor recovery count toward both simulated work and collision metrics. Shared
defenses must yield 160 unique stations; one casualty leaves 159 with at most one survivor remapped.
Four factories must produce 132 unique, spaced stations. Reinforcements retain valid established
stations; contraction may replace obsolete outer slots. Stable identities take precedence over
fresh global minimum-distance matching. Dense continuous packing must preserve orders and physical
clearance without requiring a free traffic-lattice slot. The current movement contract is
described in [runtime](runtime.md#movement-and-combat).

## Contact solver measurement

Count all narrow contact-plane evaluations in prediction and final certification, including
pathless displacement, recovery and repeated sweeps; destination-capacity checks also count.
Spatial bucket filtering is broadphase work, not a replacement for required collision constraints.
Stable regions still pay for linear state packing/indexing and local overlap discovery, but do not
run displacement certificates without an intent/correction. Report tick timing separately from
completed-frame p99 and Real FPS. Constructor/JIT warm-up stays outside both timing streams;
specified simulation commands, passive submission, Hold/defense recovery and replanning stay inside.
Use identical fixture, seed, duration and timer for before/after measurements and keep raw samples
under ignored `outputs/`. Existing frame and 6,000-pair choke gates remain unchanged.

Fixed contact sweeps bound propagation work, not neighbor count. Ordinary separated distributions
have small local neighborhoods; dense chains increase those neighborhoods and may need more ticks
to clear. Identical centers can produce quadratic candidate count, and repeated half-plane solving
can be worse than quadratic in an extreme dense component. There is no universal linear-time claim,
neighbor truncation, N-by-N distance matrix or wall-clock-dependent simulation budget. Safety
certification is not omitted in those cases; blocked space may legitimately prevent passage.

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
