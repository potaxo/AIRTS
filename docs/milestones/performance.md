# AIRTS Implemented Performance Milestones

This document records the executable 1,000-unit software, native-4K, and OpenGL performance
contracts and the limits of their evidence. It is normative for current behavior.

The contracts currently expose unresolved p99 frame stalls in the simulation-bearing 1,000-unit
workloads; they must not be described as achieved until every Real FPS assertion passes. Earlier
average-throughput results remain useful profiling history but are not acceptance evidence under
ADR 0004. The crowd contracts also expose unresolved deep overlap in settled formations and choke
traffic; successful throughput does not excuse stacked unit centers.

[Design index](../design.md)

---

# 38. Thousand-Unit 100 Real FPS Interaction Milestone

The frontend uses a 1,000 FPS application-clock ceiling while the authoritative simulation remains
fixed at 10 ticks per second; every frame-rate acceptance budget requires at least 100 `Real FPS`.
The invariant criterion is `1000 / p99 completed-frame milliseconds`, so 100 Real FPS requires a
p99 interval at or below 10 ms. Average submission throughput is diagnostic only and cannot pass an
FPS test. [ADR 0004](../decisions/0004-real-fps-acceptance.md) owns this permanent rule. The required
interaction workload contains 1,000
selected player light tanks on an
80 x 60 map with grass, road, and forest terrain. Move, patrol, and defend commands must each be
accepted for the complete selection; the UI must not deselect, hide, suspend, or omit simulation
work for any unit to meet the budget.

`tests/performance/test_thousand_unit_100fps.py` is the executable expected-behavior contract. For
each command
it records 100 consecutive completed-frame intervals containing command submission, 100 complete
Pygame software-surface draw passes, and ten authoritative simulation advances. The p99 completed
frame interval must remain at or below 10 ms.
Afterward, all 1,000 units must still belong to the order, the simulation must have advanced ten
ticks, and at least 100 units must have changed position. Large-selection route feedback must remain
visible through one to 32 deterministic representative paths. Timing setup and the initial warm-up
draw occur outside the measured interval; command planning, movement, automation work, visibility,
collision, panel drawing, and entity drawing occur inside it.

The milestone uses algorithmic and data-layout changes within the existing Python process:

* dense reverse-navigation fields with uniform and weighted builders;
* exact per-row bit-mask unions for visibility;
* one reused spatial-neighbor result per unit movement attempt;
* cached per-frame map transforms and bounded large-selection visual detail.

The simulation remains single-threaded. Rust or worker threads may be reconsidered only after a
measured workload exceeds this architecture's budget; neither is required for this target, and
nondeterministic worker scheduling must never alter authoritative results.

This acceptance contract measures portable core GUI work on a Pygame software surface. Physical
display presentation also depends on CPU speed, refresh rate, VSync, desktop composition, and
GPU/driver behavior, so it is not a cross-machine guarantee of 100 displayed refreshes per second.
The milestone covers move, patrol, and defend responsiveness, not worst-case 1,000-unit combat or
choke throughput; dense choke behavior has its own 500-unit regression.

---

# 39. 4K Thousand-Scout Movement and Collision Milestone

The 4K acceptance workload adds simultaneous rendering and dense movement pressure that the
general interaction milestone does not exercise. It contains two oppositely directed friendly
500-scout formations, four ordinary friendly buildings outside the traffic lane, and mixed grass,
road, and forest on
an 80 x 60 map. Both formations receive head-on move commands and must physically interact during
the measured second. No unit may be hidden, deselected, removed from collision, or omitted from
authoritative simulation to satisfy the budget.

`tests/performance/test_4k_thousand_scout_100fps.py` is the executable expected-behavior contract.
It first
isolates 100 complete draws on a real 3840 x 2160 Pygame software Surface and then isolates two
command submissions plus ten authoritative collision ticks. The end-to-end test measures those
commands, ten ticks interleaved at 10 Hz, and 100 complete 4K draws as consecutive frame intervals
whose p99 must remain at or below 10 ms. All 1,000 scouts must retain active orders,
the simulation must advance exactly ten ticks, and at least 750 scouts must change position. Its
runtime-configuration assertion additionally requires a bounded logical window opened with both
`SCALED` and `RESIZABLE`.

This milestone remains within the declared Python and `pygame-ce` dependencies. The renderer
caches static terrain scaling, per-tick large-scene transforms, sprite Surfaces, and representative
routes, then batches unit blits. The simulation uses compact reused collider snapshots, cached
static building occupancy, exact bit-mask visibility unions, and larger scout-only staging
clusters. These are data-layout and redundant-work reductions; entity movement, collision,
visibility, selection, buildings, UI panels, and command ownership remain authoritative.

The 3840 x 2160 Surface test verifies CPU-side full-frame construction and remains suitable for
headless regression testing. It cannot prove that a physical 4K monitor presents 100 distinct
refreshes per second. The explicit software runtime still renders a smaller logical Surface and
asks SDL to scale it; backend acceleration is environment-dependent, and `pygame.SCALED` may report
that no fast renderer is available. Section 40 adds the separate native OpenGL contract. Worst-case
1,000-unit combat and dense-choke throughput remain separate workloads.

---

# 40. Native-4K OpenGL Rendering Milestone

The interactive runtime defaults to an explicit OpenGL 3.3 renderer implemented with ModernGL.
The renderer must use a native physical framebuffer with `OPENGL | DOUBLEBUF | RESIZABLE`; it must
not use `SCALED` or silently substitute the software backend. OpenGL or dependency failure is an
actionable startup error. `--renderer software` is the only supported way to request the existing
software path.

The GPU scene consists of native-pixel instanced rectangles and analytic antialiased circles.
Every terrain cell and grid line remains present. Every unit and building remains visible, and
selection tint, group outline, damaged/inspected health bars, and one to 32 representative routes
remain consistent with the software renderer. Static terrain data is uploaded once per transform;
dynamic instance and line buffers update at simulation-tick cadence rather than render cadence.
The GPU performs rasterization and composition. The CPU still performs authoritative simulation,
collision, commands, input, buffer preparation, and font rasterization.

Dynamic shape instances contain both the previous and current fixed-tick center. The vertex shader
mixes those endpoints with a per-render interpolation uniform, so high-rate submissions produce
distinct unit and projectile positions between 10 Hz ticks while reusing the same resident buffer.
The simulation remains authoritative and interpolation intentionally adds at most 100 ms of visual
latency rather than predicting or mutating future state. The application passes `vsync=0` and uses
a 1,000 FPS Pygame clock ceiling; the driver and compositor may still impose physical presentation
limits.

The existing Pygame interaction UI is preserved as a cached transparent native-resolution texture.
Spatial editing feedback, construction previews, gathering glow, panels, settings, and help are
rebuilt only when their authoritative or interaction state changes and are then composed by
OpenGL. Projectile bodies, trajectories, and retained traces use the normal GPU shape and line
batches rather than the full-frame CPU texture. Status-only texture changes are coalesced into
three-tick buckets, while explicit interaction changes remain immediate and world batches still
update every simulation tick. This hybrid keeps the complete interface available without uploading
or redrawing the 1,000-unit base scene in software.

`tests/performance/test_opengl_thousand_scout_100fps.py` is the executable contract. It verifies:

* native 3840 x 2160 framebuffer coordinates and a 1.0 pixel scale;
* OpenGL, double buffering, resizing, and absence of `SCALED` on the default backend;
* native platform context selection: WGL on Windows and Wayland preference on WSLg;
* all 4,800 terrain cells, 1,000 scouts, four buildings, selection, and bounded routes;
* one cached terrain draw, one entity draw, one bounded line draw, and one UI composition draw;
* diagnostic context failure with no hidden software fallback;
* deterministic buffer reuse and explicit GPU-resource release;
* previous/current shape endpoints and changing GPU interpolation without dynamic-buffer rebuilds;
* a 1,000 FPS application-clock ceiling and explicit VSync-off request;
* two head-on 500-scout commands, ten collision ticks, 100 native-4K GPU frames, a non-background
  rendered-pixel check, per-frame GPU completion, and at least 100 Real FPS;
* rejection of llvmpipe, softpipe, SwiftShader, or another software rasterizer as hardware proof.

The dependency is `moderngl>=5.12,<6`, which provides OpenGL 3.3 core access and instanced buffer
submission on Python 3.13. A passing offscreen hardware benchmark proves GPU rasterization and the
100 Real FPS work budget on the tested adapter. The verifier uses ModernGL's native platform backend,
including WGL on Windows, instead of hard-coding EGL; known Mesa software rasterizers, SwiftShader,
GDI Generic, and the Microsoft Basic Render Driver are rejected as hardware evidence. It still
cannot prove that a compositor and physical monitor display 100 distinct refreshes, and it does
not change the simulation's fixed 10 Hz rate. The runtime settings menu supplies resolution presets
from 1280 x 720 to 3840 x 2160 and rolling p95 frame, render, simulation, and swap-wait diagnostics.
These metrics localize application or swap pressure but do not replace ETW/PresentMon when
displayed-frame cadence is the question.

---

# Sustained Complex 1,000-Unit Battle Milestone

The sustained contract closes the gap between the collision-only workloads above and actual combat.
It contains 500 player units and 500 enemy units on an 80 x 60 mixed-terrain map. Each owner has a
deterministic 70/20/10 mix of scouts, light tanks, and heavy tanks. The friendly army receives one
persistent line-defense automation while the enemy army receives a head-on move command. Units
acquire hostile targets, launch projectiles, take damage, resolve collision, update visibility, and
remain fully represented in the native-4K scene. The player selection contains all 500 friendly
units; the active defend automation and an enemy are inspected; a spatial target and normal panels
are visible. Defend response uses the same deterministic spatial broadphase as other local queries
instead of scanning every assigned responder for every attacked unit.

`tests/performance/test_sustained_complex_battle_100fps.py` is the executable contract. After one
warm-up frame, its measured interval includes the 500-unit defend and enemy-move command submissions,
30 authoritative
ticks, 300 native-4K hardware frames, ten interpolation samples per tick, normal clock-value
fluctuation, six inspected-target changes, per-frame GPU completion, and a rendered-pixel readback.
The test requires real collision work, live projectiles, health loss, both hostile owners, every
mobile unit profile, and at least 100 Real FPS over the complete three-second work budget under the
same p99 criterion. Software rasterizers remain invalid evidence under the Section 40 hardware
rules.

The interactive cache key uses an O(1) command count instead of copying replay history. Per-frame
FPS sampling does not invalidate the full 31.64 MiB 4K overlay; the displayed status is refreshed
in bounded three-tick buckets. On the reference AMD Radeon RX 7900 XT, the original direct-combat
workload improved from 24.5 FPS to 152.3 FPS after bounding overlay work. The strengthened defend
automation workload achieved 188.1 FPS after GPU projectile batching and spatial responder queries.
These are offscreen workload results, not proof that a physical monitor or compositor presents the
same number of distinct frames per second.

A separate historical Windows interactive capture used PresentMon 2.5.1 against an uncapped build
launched with the same 500-vs-500 mixed-unit battle at the default 1428 x 872 resolution. Over
9.968 seconds the application submitted 44,665 frames (4,480.7 presents/s), while Windows recorded 1,268
display changes (127.2 displayed frames/s) in `Composed: Copy with GPU GDI` mode. CPU-busy and GPU
time p95 were 0.662 ms and 0.675 ms respectively; display-latency p95 was 19.844 ms and median
displayed duration was 6.95 ms. This capture demonstrates more than 100 actually displayed frames
per second on the reference machine and identifies the display/compositor cadence, rather than
simulation or frame construction, as the remaining ceiling. It is machine-specific ETW evidence,
not a portable automated guarantee; the checked-in hardware test remains the reproducible work
budget contract.

The simulation remains authoritative, deterministic, and single-threaded. The measured result does
not justify an OpenGL 4.3 compute requirement, a native extension, or worker scheduling. Those are
future options only if a broader sustained workload misses its budget after measured CPU data-layout
and redundant-work improvements.

---

# Saturated Crowd and Choke Milestone

`tests/performance/test_crowd_congestion_performance.py` is the executable regression for the
late-game cases that are intentionally harsher than the open-field and mixed-battle contracts:

* 999 scouts explicitly focus one durable enemy for 220 ticks;
* 400 scouts must fully settle with at least 0.90 map-unit center spacing around an undersized
  two-by-two defense while authoritative tick p95 remains below 100 ms;
* all 400 scouts must cross a nine-cell bridge opening on a 120 x 80 map without deep overlap;
  bridge completion has only a generous deadlock guard, not a throughput or formation-settling
  deadline;
* smaller correctness cases prove unique initial defend stations, coherent point/area patrol slots,
  and topology-safe crowded-waypoint lookahead at a bridge turn.

The 400-unit formation and bridge counts are deliberate capacity tests rather than reduced
performance targets. A 1,000-unit formation at the required visual spacing consumes a large fraction
of an 80 x 60 map and can place valid destination slots on the wrong side of a choke, conflating map
capacity with routing correctness. Independent software, native-4K, OpenGL, mixed-battle, and
focus-fire contracts retain roughly 1,000 authoritative units and the unchanged 100 Real FPS target.

Every unit remains authoritative, visible to normal renderers, collision-enabled, and assigned to
its command or automation. A target that cannot contain the group expands into deterministic
reachable hex-packed holding slots. Explicit attackers clear their pursue path at weapon range.
Large-formation assignment pairs front-to-back arrivals with far-to-near slots along the approach
axis; small gathering groups preserve center-first behavior. Nearby slots share reverse-field
anchors but branch toward their final destinations before reaching
the exact anchor, avoiding a secondary point bottleneck. Moving queue members continue to use the
shared static navigation field and local collision solver; crowded lookahead may skip an occupied
waypoint only while the next route cell remains on the same grid axis and the cached static corridor
is passable, so it cannot cut diagonally across water.
Only settled blockers can trigger a dynamic military-penalty replan. Delayed-route checks are
distributed over stable 50-tick phases, final-approach replans share the stalled-route budget, and
blocked recovery performs at most four searches per tick.

A large expanded defense starts with deterministic hex-packed stations. Congestion-stopped arrivals
may adopt their current position inside the formation core, and after a 500-tick deployment window
an arrival not under collision pressure anywhere inside the declared overflow envelope becomes its
new inspectable station. This bounds exact-slot churn without changing the defended target or
removing a unit from the automation; groups of at most 128 units retain exact stations.

The sustained timing cases require p95 authoritative tick time below the fixed 100 ms tick budget.
The bridge test requires the complete force east of the river regardless of throughput, and the
tiny-defense test requires every unit to have no retained route, remain inside the expanded
formation envelope, and preserve its minimum spacing. The
implementation therefore cannot pass by freezing the queue, parking units on the near side, or
measuring only an early fast interval. These are simulation timings, not displayed-frame
measurements, and other hardware may differ.

The current policy is deliberately not a full ORCA, ClearPath, or Continuum Crowds implementation.
It applies their useful architectural split—shared group-scale routing plus bounded local avoidance—
without changing AIRTS's deterministic command, collision, and replay semantics. ADR 0003 records
the alternatives and the evidence required before native or GPU crowd compute is justified.

---

# Human-Inspection GUI Scenarios

The non-discovered modules under `tests/gui_scenarios/` provide live native-window counterparts for
visual performance workloads. They cover saturated crowd and bridge flow, large-army routing and
formations, 1,000 selected-unit commands, native-4K OpenGL movement and feedback, and the sustained
500-vs-500 mixed-unit battle. Each scenario uses the normal `AirtsApp` OpenGL frontend, advances the
authoritative simulation in the application loop, and remains open until the operator closes the
window.

These modules deliberately do not use `test_*.py` filenames. A normal `pytest` invocation therefore
does not collect or block on them. Run a module explicitly, and optionally select one scenario with
`-k`:

```powershell
.\.venv\Scripts\python -m pytest -s tests\gui_scenarios\crowd_congestion_gui.py
.\.venv\Scripts\python -m pytest -s tests\gui_scenarios\large_army_gui.py -k choke
.\.venv\Scripts\python -m pytest -s tests\gui_scenarios\rendering_performance_gui.py -k battle
```

Closing a scenario window completes that pytest item; the result records successful setup and a
clean frontend shutdown, not an automated frame-rate threshold or a human judgment. The automated
contracts under `tests/performance/` remain the authoritative reproducible acceptance evidence, and
every FPS claim uses the shared Real FPS rule rather than average throughput.
Physical-window inspection adds evidence about native window creation, compositor-visible rendering,
interaction, and qualitative motion, but results depend on the active desktop, monitor mode, window
occlusion, compositor, and GPU driver.

---
