# AIRTS Performance Guidance

AIRTS performance work protects deterministic simulation and interaction responsiveness without
claiming one portable frame rate across different Windows machines.

[Design overview](design.md) · [Movement architecture](architecture/movement.md)

## What performance tests mean

The authoritative simulation runs at 10 ticks per second. A slow tick is relevant because it can
prevent real-time execution, but elapsed timings depend on CPU, GPU-independent software drawing,
Python build, background load, and test environment.

Performance tests therefore combine two kinds of evidence:

- machine-independent correctness assertions, such as unit counts, deterministic results, valid
  routes, unique stations, collision separation, and eventual progress; and
- timing diagnostics or generous regression guards that catch severe accidental slowdowns on the
  development machine without promising the same throughput everywhere.

AIRTS has one Pygame software frontend. There is no OpenGL backend, hardware-renderer acceptance
contract, VSync contract, `Real FPS` metric, or GPU proof requirement.

## Current workload coverage

The compact performance suite owns two smoke workloads:

- opposing 500-scout groups accept their orders and move in the requested directions; and
- a 96-scout defense materially converges on its target region.

Both have a deliberately generous 30-second guard. Detailed routing, collision, formation,
persistence, replay, combat, and presentation correctness belongs to the focused unit, movement,
and integration suites instead of being duplicated in timing tests.

Run the performance suite with:

```powershell
.\.venv\Scripts\python -m pytest tests\performance
```

Run the complete required validation from [AGENTS.md](../AGENTS.md) before declaring a change
complete.

## Profiling and reporting

Measure before optimizing. A useful report includes:

- commit and scenario;
- Windows, CPU, Python, and dependency versions;
- map dimensions, unit mix, and active commands;
- warm-up and measured tick or frame counts;
- median and tail timings rather than average alone;
- the correctness assertions checked after timing;
- whether the workload used the headless simulation or software frontend.

Do not place one-off machine results in normative design documents. Keep dated experimental output
with the experiment or issue that interprets it.

## Optimization boundaries

Safe optimization removes redundant work while preserving authoritative results. Current examples
are cached shared routes, deterministic spatial indexes, bounded path work, and reused per-tick
spatial data.

Performance work must not weaken validation, remove units from simulation or collision, change
terrain reachability, introduce group-size-dependent command meaning, or make replay depend on
thread scheduling. A consequential algorithm change requires documentation and an architecture
decision record.
