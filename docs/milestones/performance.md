# AIRTS Implemented Performance Milestones

This document records the executable 1,000-unit software, native-4K, and OpenGL performance
contracts and the limits of their evidence. It is normative for current behavior.

[Design index](../design.md)

---

# 38. Thousand-Unit 100 FPS Interaction Milestone

The frontend targets 100 FPS while the authoritative simulation remains fixed at 10 ticks per
second. The required interaction workload contains 1,000 selected player light tanks on an
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
general interaction milestone does not exercise. It contains two opposing 500-scout formations,
four ordinary friendly buildings outside the traffic lane, and mixed grass, road, and forest on
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

The existing Pygame interaction UI is preserved as a cached transparent native-resolution texture.
Spatial editing feedback, construction previews, gathering glow, projectiles, panels, settings, and
help are rebuilt only when their authoritative or interaction state changes and are then composed
by OpenGL. This hybrid keeps the complete interface available without uploading or redrawing the
1,000-unit base scene in software.

`tests/performance/test_opengl_thousand_scout_100fps.py` is the executable contract. It verifies:

* native 3840 x 2160 framebuffer coordinates and a 1.0 pixel scale;
* OpenGL, double buffering, resizing, and absence of `SCALED` on the default backend;
* native platform context selection: WGL on Windows and Wayland preference on WSLg;
* all 4,800 terrain cells, 1,000 scouts, four buildings, selection, and bounded routes;
* one cached terrain draw, one entity draw, one bounded line draw, and one UI composition draw;
* diagnostic context failure with no hidden software fallback;
* deterministic buffer reuse and explicit GPU-resource release;
* two head-on 500-scout commands, ten collision ticks, 100 native-4K GPU frames, a non-background
  rendered-pixel check, and a final GPU completion wait within one second;
* rejection of llvmpipe, softpipe, SwiftShader, or another software rasterizer as hardware proof.

The dependency is `moderngl>=5.12,<6`, which provides OpenGL 3.3 core access and instanced buffer
submission on Python 3.13. A passing offscreen hardware benchmark proves GPU rasterization and the
100 FPS work budget on the tested adapter. The verifier uses ModernGL's native platform backend,
including WGL on Windows, instead of hard-coding EGL; known Mesa software rasterizers, SwiftShader,
GDI Generic, and the Microsoft Basic Render Driver are rejected as hardware evidence. It still
cannot prove that a compositor and physical monitor display 100 distinct refreshes, and it does
not change the simulation's fixed 10 Hz rate.

---
