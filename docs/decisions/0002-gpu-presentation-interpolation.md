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

The application frame limiter is disabled and the SDL OpenGL window requests VSync off. Windowed
resolution presets allow the player to trade pixel workload for detail. Rolling application-side
timings distinguish simulation, render submission, and `display.flip()` wait, but external ETW
instrumentation remains authoritative for compositor/display cadence.

## Consequences

Motion remains deterministic and gains distinct intermediate positions without dynamic-buffer
uploads at render cadence. The cost is two additional floats per shape instance and up to one
100 ms tick of visual latency. Projectile trajectory lines and spatial paths still update at tick
cadence; only moving shape centers interpolate. The software compatibility renderer is unchanged.
Driver-forced synchronization and monitor refresh can still limit actually displayed frames even
when application submission exceeds 100 FPS.
