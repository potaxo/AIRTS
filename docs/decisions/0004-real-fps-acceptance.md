# ADR 0004: Use Real FPS for every frame-rate acceptance test

**Status:** Accepted and invariant

## Context

Average throughput can report a high submission rate while intermittent long frames make motion
visibly uneven. AIRTS already exposes `Real FPS` as the inverse of the p99 completed-frame interval
over its sample window. Several performance tests still divided total frame count by total elapsed
time, so they could pass under a criterion different from the player-facing metric. Offscreen GPU
tests also waited for completion only after a batch, which measured queued throughput rather than
individual completed-frame pacing.

## Decision

Every automated test that makes an FPS acceptance claim must record consecutive completed-frame
intervals and use `airts.presentation.app.real_fps_from_frame_times`. The percentile is permanently
`0.99`: `Real FPS = 1000 / p99 frame time in milliseconds`. A 100 Real FPS contract therefore
requires p99 completed-frame time at or below 10 ms. Average `Submit FPS`, total elapsed time, and
mean throughput may appear only as diagnostics; they cannot decide whether an FPS contract passes.

Hardware GPU workloads must wait for GPU completion before recording each measured frame. Commands,
scheduled simulation ticks, rendering, presentation work, and required synchronization remain in
their natural measured frame. Tests for authoritative simulation-tick budgets are not FPS tests and
retain their explicit millisecond or elapsed-time criteria.

The percentile constant and representative stutter behavior are locked by regression tests. This
rule must not be tuned, renamed, or replaced to make a workload pass. A genuinely different future
measurement contract requires an explicit superseding architecture decision rather than an edit to
an individual benchmark.

## Consequences

Acceptance now tracks the same stutter-sensitive metric shown to the player. Periodic simulation,
collision, UI, or GPU stalls can fail a workload even when its average throughput is above target.
Per-frame GPU completion makes hardware tests stricter and less vulnerable to command-queue
buffering. Results remain application-side measurements; physical scan-out still requires external
ETW or PresentMon evidence.
