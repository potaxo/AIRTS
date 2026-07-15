# ADR 0002: Interpolate fixed-tick presentation on the GPU

**Status:** Accepted

## Context

AIRTS advances authoritative state at a deterministic 10 Hz but can submit more than 100 OpenGL
frames per second. Reusing an unchanged world buffer between ticks made the counter high while
motion still appeared to update ten times per second. Raising the simulation rate would multiply
pathfinding, collision, combat, visibility, and automation work and would change replay semantics.
CPU interpolation would rebuild 1,000-unit geometry every presented frame. Extrapolation would be
more responsive but could display positions the simulation later rejects.

## Decision

The presentation adapter snapshots mobile entity and projectile positions immediately before each
authoritative tick. Each dynamic shape instance stores its previous and current pixel center. The
OpenGL 3.3 vertex shader linearly interpolates those centers using the fixed-step accumulator ratio
on every submitted frame. Static terrain and buildings use the same center at both endpoints.
Presentation history is reset on load and new game. It is never serialized and cannot influence
simulation state, command validation, hit testing, targeting, or replay.

Periodic CPU overlay conversion is staged onto the first interpolation frame after a changed tick,
while world buffers update immediately. The explicit software backend applies the same pacing rule
to complete composed Surfaces: it presents the retained frame on the simulation-bearing call,
reconstructs on the next call, and forces an immediate catch-up if the caller advances again first.
Terrain, shape, and line buffers begin with bounded reusable capacities and retain their storage;
they orphan and grow only when a scene exceeds the current capacity. This removes allocator and
driver growth spikes from ordinary 1,000-unit frames without placing a fixed upper bound on maps.

The application frame limiter uses a 1,000 FPS ceiling and the SDL OpenGL window requests VSync
off. The ceiling prevents accidental unbounded submission while remaining an order of magnitude
above the 100 Real FPS acceptance target. Windowed resolution presets allow the player to trade pixel
workload for detail. Rolling application-side
timings distinguish simulation, render submission, and `display.flip()` wait, but external ETW
instrumentation remains authoritative for compositor/display cadence.

## Consequences

Motion remains deterministic and gains distinct intermediate positions without dynamic-buffer
uploads at render cadence. The cost is two additional floats per shape instance and up to one
100 ms tick of visual latency. Projectile trajectory lines and spatial paths still update at tick
cadence; only moving shape centers interpolate. Tick-driven text and the software compatibility
frame add at most one presentation-call latency, which separates periodic simulation and raster
work without moving either outside the measured frame stream.
Driver-forced synchronization and monitor refresh can still limit actually displayed frames even
when Submit FPS exceeds 100.
