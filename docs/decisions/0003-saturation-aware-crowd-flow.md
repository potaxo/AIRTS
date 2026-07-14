# ADR 0003: Use saturation-aware shared crowd flow before per-agent ORCA

**Status:** Accepted

## Context

Late-game tests with roughly 1,000 to 1,282 scouts exposed a simulation-side collapse that did not
appear in the existing open-field benchmarks. Focus-attacking one enemy, defending or patrolling a
tiny area, and feeding the same force through a bridge produced 2--6 submitted frames per second.
The event stream repeatedly alternated `NO_PROGRESS_YIELD` and `DESTINATION_DELAY_REPATH`.

The causes were concrete. Tiny defense areas repeated a few station coordinates for the whole
force. Area patrol members converged on the same waypoints. Explicit attackers continued toward a
target's adjacent cells after entering weapon range. Delayed routes treated moving queue members as
dynamic A* obstacles, while contested final approaches could launch additional unbudgeted searches.
Later full-cross testing also found crowded waypoint lookahead cutting diagonally across water and
cluster routes funneling every member through one exact shared-field anchor.

Three research directions are relevant. Continuum Crowds computes a dynamic potential for groups
with common goals and shows why group-scale fields can combine navigation and congestion response.
ORCA assigns reciprocal pairwise avoidance responsibility and solves a low-dimensional linear
program per agent. ClearPath formulates local velocity selection as a parallel discrete
optimization. The latter two are credible future local solvers, but adopting either now would
replace AIRTS's movement behavior and require new deterministic numeric and replay contracts.

Primary sources:

* [Continuum Crowds](https://grail.cs.washington.edu/projects/crowd-flows/78-treuille.pdf)
* [Optimal Reciprocal Collision Avoidance](https://gamma-web.iacs.umd.edu/ORCA/)
* [ClearPath](https://diglib.eg.org/items/bdfea054-b571-4b5a-a44c-38b47876604f)

## Decision

AIRTS keeps authoritative movement deterministic and CPU-resident. Large groups continue to use
cached reverse navigation fields for global direction and the existing spatial broadphase for local
steering and collision. Saturation behavior changes as follows:

* undersized defense targets expand into unique reachable hex-packed holding slots;
* large-formation slots are assigned by approach order while small gathering groups remain
  center-first, and clustered shared routes branch before their exact anchor;
* point and area patrol forces move through their cycle as one collision-safe formation;
* explicit attackers stop their pursue path once the ordered target is in weapon range;
* crowded waypoint lookahead preserves terrain topology by requiring a passable cached corridor on
  the current grid axis;
* moving queue members are handled by local steering, collision pressure, and bounded yielding;
* only settled units are fed back into dynamic military-penalty A*;
* delayed checks use stable per-entity phases, and all stalled/final-approach searches are bounded.

Expanded defenses keep their deterministic initial slots, but saturation may make an exact lattice
coordinate counterproductive. For groups larger than 128 units, a congestion-stopped unit inside
the formation core may adopt its current position as its station. After a 500-tick deployment
window, a unit not under collision pressure anywhere inside the declared overflow envelope may do
the same. Smaller groups retain exact stations. The adopted station remains serialized and
inspectable; the unit is not removed from simulation, collision, or automation ownership.

GPU rasterization and interpolation remain presentation-only. Authoritative ORCA, ClearPath, native
extensions, worker scheduling, or GPU compute require a separate decision after this policy fails a
checked-in sustained workload.

## Consequences

Tiny areas describe the defended or patrolled objective, not a promise that every assigned unit can
fit inside the geometry. Overflow forces visibly occupy nearby reachable space. Large area patrols
advance coherently instead of distributing members across opposing phases. Bridge queues may wait
locally rather than attempt futile alternate A* routes, but units are never removed from collision
or simulation.

The executable acceptance tests keep roughly 1,000 units in independent Real FPS and focus-fire
stress workloads. Capacity-specific formation and bridge correctness use 400 units: the defense
must settle with 0.90 map-unit center spacing and p95 tick time below 100 ms, while every bridge unit
must eventually reach the east bank without deep overlap. The enlarged bridge map prevents a
destination formation from spanning the choke, and bridge throughput and later formation settling
are diagnostic rather than acceptance. ORCA remains the preferred experiment if local motion
quality—not Python path-search waste—is the next measured limiter. ClearPath-style or GPU
parallelism is considered
only after a CPU reference
solver and deterministic replay comparison exist.
