# AIRTS Information and Integration Architecture

This document owns visibility authority, observation, persistence, replay, model integration, voice, MCP, and evaluation boundaries.

[Design index](../design.md) · [Roadmap](../roadmap.md)

---

# 24. Fog of War and Information Authority

AIRTS must distinguish between:

* complete internal simulation state;
* information visible to the player;
* previously observed information;
* uncertain or inferred information.

The model receives only information available to the player unless a specific debugging mode is active.

Last-known information should include:

* observation time;
* previous position;
* confidence or certainty;
* whether the entity is still visible.

The model must not receive hidden enemy locations during normal evaluation.

The implemented large-scene visibility update remains exact: sources with the same owner, cell,
and sight radius share cached row masks, and those masks are unioned into authoritative visible and
explored bit sets. Grouping removes repeated raster work without approximating sight geometry or
changing save, replay, or information-authority semantics.

---

# 25. Simulation-Time Model

AIRTS uses a deterministic fixed-step simulation.

The simulation update rate must be independent of the rendering frame rate.

Provisional values:

```text
Simulation: 10 ticks per second
Rendering:  1,000 FPS submission ceiling; 100 Real FPS acceptance budget
```

The game should support:

* pause;
* normal speed;
* accelerated testing speed;
* deterministic execution from the same state and seed.

Model latency must not stop the simulation unless the player explicitly pauses the game.

Every model request should record the simulation tick of its input snapshot.

When a response returns, it must be validated against the current world state rather than blindly applied to the older snapshot.

---

# 26. User Interface

The initial UI is a debugging and interaction interface, not a polished commercial interface.

An operating-system window-close request must stop event handling before another simulation or
render pass. The frontend initializes only the Pygame subsystems it uses and releases retained
graphics objects, pending events, fonts, the display, and global Pygame state in a deterministic
order on both normal and exceptional exits.

The default renderer uses a Pygame-created, double-buffered OpenGL 3.3 core window and ModernGL.
It renders directly to the physical framebuffer size: a 3840 x 2160 window therefore receives
native 4K terrain, analytic antialiased unit circles, buildings, health feedback, selection state,
and bounded route lines instead of an enlarged low-resolution image. Terrain and grid instances
are uploaded only when the map transform changes. Entity instances carry previous and current
authoritative centers and route vertices are rebuilt at most once per simulation tick or relevant
UI change. Those buffers begin with bounded reusable terrain, shape, and line capacities, grow only
for a larger scene, and remain resident for intervening render frames. A vertex-shader uniform
interpolates unit, health, selection, range, and projectile centers between fixed ticks without a
per-render CPU rebuild or simulation mutation. Interpolation adds up to one fixed tick of visual
latency and never feeds presentation state back into geometry, combat, replay, or commands. The
normal scene is submitted as one instanced terrain draw, one instanced entity draw, and one bounded
line draw.

Pygame font output and infrequent interaction feedback are rasterized into a cached transparent
native-resolution texture and composited by OpenGL. Per-frame FPS samples do not invalidate this
full-frame texture; status-only refreshes are coalesced to one update per three simulation ticks.
Opaque panel regions and transient construction regions are redrawn and uploaded independently,
and their tick-driven upload is scheduled on the first interpolation frame after simulation work.
Explicit interaction changes remain immediate. Projectile bodies, trajectories, retained traces,
and assembly glows are packed into the native GPU shape and line batches, so combat feedback updates with the
world frame without software rasterization or a full RGBA overlay upload. This does not make the
application "GPU only": the simulation, commands, input, frame-data preparation, and font
rasterization remain CPU work, while the GPU owns scene rasterization, antialiasing, projectile
feedback, and final composition. The legacy bounded
logical `SCALED | RESIZABLE` software framebuffer remains an explicit `--renderer software` mode
for headless CI and compatibility. OpenGL startup failures are diagnostic and never silently fall
back, because doing so would make performance evidence and renderer identity untrustworthy.

On Windows, SDL creates the interactive OpenGL context through the native Windows driver stack,
and ModernGL's standalone verifier uses its native WGL backend. Context creation requires OpenGL
3.3 or newer. The application clock has a 1,000 FPS ceiling and the OpenGL window explicitly
requests VSync off. Settings expose 1280 x 720 through 3840 x 2160 presets and rolling p95 frame,
renderer, simulation, and buffer-swap wait measurements. The left rail's `Real FPS` is the rolling
1%-low rate obtained by inverting the p99 completed-swap frame interval, while Settings retains the
rolling average `Submit FPS`. This makes the primary readout respond to frame-pacing stalls instead
of hiding them inside an average. Swap wait includes work that SDL cannot separate into GPU, driver,
and compositor stages; neither rate counts frames physically scanned out by the monitor. Physical
refresh rate, driver overrides, desktop composition, and monitor timing remain outside the
deterministic simulation contract.

The inverse-p99 `Real FPS` definition is also the invariant acceptance rule for every automated test
that makes a frame-rate claim. Those tests sample consecutive completed frames through the shared
presentation metric; average throughput is diagnostic only. Hardware tests synchronize each frame
before sampling so queued GPU work cannot satisfy the contract. ADR 0004 records this decision.

WSLg remains a compatibility path and prefers Wayland when no SDL video driver is selected.

Entity hit testing uses the visible occupied footprint for buildings rather than only their center
point. Large focus-fire groups share deterministic reverse navigation fields to the target's valid
interaction perimeter so selecting a building does not trigger one full search per attacker.
When more than 128 units are selected, the renderer preserves selection feedback with a brighter
unit color and an aggregate panel readout, omits per-unit outlines and redundant health bars, and
draws at most 32 evenly sampled authoritative routes. The inspected-unit health bar remains visible. An
inspected route is retained in the representative set. This is visual level of detail only: every
selected unit remains selected, simulated, collision-enabled, visible, and owned by its command.
For large selections, unit and building screen transforms and representative route transforms are
rebuilt at most once per simulation tick or relevant UI-state change. Normal buildings and
inspected health feedback remain part of every complete frame. The explicit software backend
retains a complete composed Surface, copies it for unchanged presentation calls, and reconstructs
a changed tick on the following presentation call. A second tick before that call forces an
immediate catch-up. This bounded one-frame staging is presentation-only and never changes simulation
or command timing.

It should eventually display:

* the grid map;
* terrain;
* units;
* buildings;
* resources;
* selected entities;
* selected points;
* drawn paths;
* selected regions;
* named regions;
* active automations;
* automation status;
* unit states;
* recent failures;
* path previews;
* visibility;
* event logs.

## 26.1 Automation panel

The automation panel should allow the player to:

* inspect an automation;
* view its title;
* view its status;
* view assigned entities;
* view target geometry;
* view relevant parameters;
* pause it;
* resume it;
* cancel it;
* modify selected parameters.

Editable parameters may initially include:

* priority;
* retreat threshold;
* patrol order;
* reinforcement threshold;
* risk tolerance;
* completion condition.

## 26.2 Visual style

The initial visual style should remain simple.

Possible representations include:

* colored shapes;
* basic tank icons;
* simple building icons;
* symbolic unit markers;
* clear region overlays.

A lightweight style inspired by simple 2D RTS games is sufficient.

Correctness and observability are more important than visual quality.

---

# 27. Map Editor

The map editor should not be the first subsystem implemented.

The correct dependency order is:

```text
map data model
→ hand-written example map
→ simulation
→ renderer
→ map editor
```

A future map editor may include:

* terrain brush;
* region-drawing tool;
* region naming;
* unit placement;
* building placement;
* resource placement;
* bridge placement;
* validation;
* loading and saving.

The editor must produce the same stable map format used by the simulation.

---

# 28. Observability and Logging

## 28.1 Structured event log

AIRTS should generate structure for:

* player commands;
* language instructions;
* selections;
* region creation;
* automation creation;
* automation modification;
* automation state transitions;
* validation failures;
* unit-state transitions;
* movement failure;
* pathfinding failure;
* combat;
* production;
* resource changes;
* repair;
* visibility changes;
* scouting observations;
* model requests;
* raw model outputs;
* repaired model outputs.

## 28.2 Failure reports

Failure reports should identify:

* which automation failed;
* which operation failed;
* the direct reason;
* relevant entities;
* relevant regions;
* current state;
* whether execution stopped;
* whether partial progress remains;
* whether automatic recovery is possible;
* possible repair actions.

## 28.3 Replay information

A reproducible experiment should record:

* AIRTS version;
* scenario version;
* map version;
* random seed;
* initial state;
* player inputs;
* spatial selections;
* original language instructions;
* model inputs;
* raw model outputs;
* validated commands;
* automation transitions;
* world events;
* final state;
* evaluation metrics.

---

# 29. Evaluation Plan

## 29.1 Possible experimental conditions

AIRTS may compare:

1. mouse and keyboard only;
2. language only;
3. cursor grounding plus text;
4. cursor grounding plus voice;
5. direct commands only;
6. persistent automations;
7. unrestricted model output;
8. constrained automation templates;
9. human-defined geometry versus model-proposed geometry.

## 29.2 Quantitative metrics

Possible metrics include:

* task success rate;
* task completion time;
* number of player actions;
* number of language interactions;
* invalid-output rate;
* clarification rate;
* repair success rate;
* automation-failure rate;
* number of manual corrections;
* resource efficiency;
* unit losses;
* scouting coverage;
* report factual accuracy;
* inference latency;
* token usage;
* automation interruption rate;
* time spent managing automations.

## 29.3 Human-centered metrics

Possible measures include:

* perceived workload;
* perceived control;
* trust;
* predictability;
* comprehensibility;
* ease of correction;
* satisfaction;
* preference;
* perceived strategic freedom;
* perceived responsiveness.

---
