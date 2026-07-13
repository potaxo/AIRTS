# AIRTS Implemented Performance Milestones

This document records the executable 1,000-unit software, native-4K, and OpenGL performance
contracts and the limits of their evidence. It is normative for current behavior.

[Design index](../design.md)

---

# 38. Thousand-Unit 100 FPS Interaction Milestone

The frontend is uncapped while the authoritative simulation remains fixed at 10 ticks per second;
the acceptance budget remains at least 100 FPS. The required interaction workload contains 1,000
selected player light tanks on an
80 x 60 map with grass, road, and forest terrain. Move, patrol, and defend commands must each be
accepted for the complete selection; the UI must not deselect, hide, suspend, or omit simulation
work for any unit to meet the budget.

`tests/performance/test_thousand_unit_100fps.py` is the executable expected-behavior contract. For
each command
it measures one interval containing command submission, 100 complete Pygame software-surface draw
passes, and ten authoritative simulation advances. The interval must complete within one second.
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
commands, ten ticks interleaved at 10 Hz, and 100 complete 4K draws in one interval that must finish
within one second. All 1,000 scouts must retain active orders, collision-pair checks must occur,
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
mixes those endpoints with a per-render interpolation uniform, so uncapped submissions produce
distinct unit and projectile positions between 10 Hz ticks while reusing the same resident buffer.
The simulation remains authoritative and interpolation intentionally adds at most 100 ms of visual
latency rather than predicting or mutating future state. The application passes `vsync=0` and uses
an uncapped Pygame clock; the driver and compositor may still impose physical presentation limits.

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
* an uncapped application clock and explicit VSync-off request;
* two head-on 500-scout commands, ten collision ticks, 100 native-4K GPU frames, a non-background
  rendered-pixel check, and a final GPU completion wait within one second;
* rejection of llvmpipe, softpipe, SwiftShader, or another software rasterizer as hardware proof.

The dependency is `moderngl>=5.12,<6`, which provides OpenGL 3.3 core access and instanced buffer
submission on Python 3.13. A passing offscreen hardware benchmark proves GPU rasterization and the
100 FPS work budget on the tested adapter. The verifier uses ModernGL's native platform backend,
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
fluctuation, six inspected-target changes,
GPU completion, and a rendered-pixel readback. The test requires real collision work, live
projectiles, health loss, both hostile owners, every mobile unit profile, and at least 100 FPS over
the complete three-second work budget. Software rasterizers remain invalid evidence under the
Section 40 hardware rules.

The interactive cache key uses an O(1) command count instead of copying replay history. Per-frame
FPS sampling does not invalidate the full 31.64 MiB 4K overlay; the displayed status is refreshed
in bounded three-tick buckets. On the reference AMD Radeon RX 7900 XT, the original direct-combat
workload improved from 24.5 FPS to 152.3 FPS after bounding overlay work. The strengthened defend
automation workload achieved 188.1 FPS after GPU projectile batching and spatial responder queries.
These are offscreen workload results, not proof that a physical monitor or compositor presents the
same number of distinct frames per second.

A separate Windows interactive capture used PresentMon 2.5.1 against a window launched from the
same 500-vs-500 mixed-unit battle at the default 1428 x 872 resolution. Over 9.968 seconds the
uncapped application submitted 44,665 frames (4,480.7 presents/s), while Windows recorded 1,268
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

# Saturated 1,000-Unit Crowd and Choke Milestone

`tests/performance/test_crowd_congestion_performance.py` is the executable regression for the
late-game cases that are intentionally harsher than the open-field and mixed-battle contracts:

* 999 scouts explicitly focus one durable enemy for 220 ticks;
* 1,000 scouts defend a two-by-two target for 200 ticks;
* 1,000 scouts must pass through a six-cell opening to reach the same undersized defense;
* smaller correctness cases prove unique defend stations and coherent point/area patrol slots.

Every unit remains authoritative, visible to normal renderers, collision-enabled, and assigned to
its command or automation. A target that cannot contain the group expands into deterministic
reachable hex-packed holding slots. Explicit attackers clear their pursue path at weapon range.
Moving queue members continue to use the shared static navigation field and local collision solver;
only settled blockers can trigger a dynamic military-penalty replan. Delayed-route checks are
distributed over stable 50-tick phases, final-approach replans share the stalled-route budget, and
blocked recovery performs at most four searches per tick.

The two sustained timing cases require p95 authoritative tick time below the fixed 100 ms tick
budget. The bridge case additionally requires at least 500 scouts to have crossed by tick 200, so
the implementation cannot pass by freezing or parking the force on the near side. On the reference
Python 3.13 Windows environment, the peak tiny-defense convergence measured 95.6 ms p95, the bridge
queue measured 88.7 ms p95 with 557 scouts across, and sustained focus fire measured 62.7 ms p95.
These are simulation timings, not displayed-frame measurements, and other hardware may differ.

The current policy is deliberately not a full ORCA, ClearPath, or Continuum Crowds implementation.
It applies their useful architectural split—shared group-scale routing plus bounded local avoidance—
without changing AIRTS's deterministic command, collision, and replay semantics. ADR 0003 records
the alternatives and the evidence required before native or GPU crowd compute is justified.

---
