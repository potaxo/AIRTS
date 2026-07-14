"""Shared frame-pacing measurement for AIRTS performance contracts."""

from __future__ import annotations

from time import perf_counter

from airts.presentation.app import real_fps_from_frame_times


class RealFpsProbe:
    """Collect consecutive completed-frame intervals using the runtime's Real FPS rule."""

    def __init__(self, started_at: float | None = None) -> None:
        self._previous_at = perf_counter() if started_at is None else started_at
        self.frame_times_ms: list[float] = []

    def frame_completed(self, completed_at: float | None = None) -> None:
        now = perf_counter() if completed_at is None else completed_at
        self.frame_times_ms.append((now - self._previous_at) * 1000.0)
        self._previous_at = now

    @property
    def real_fps(self) -> float:
        return real_fps_from_frame_times(self.frame_times_ms)

    @property
    def p99_frame_ms(self) -> float:
        real_fps = self.real_fps
        return 1000.0 / real_fps if real_fps > 0.0 else 0.0

    @property
    def elapsed_seconds(self) -> float:
        return sum(self.frame_times_ms) / 1000.0


def assert_real_fps(probe: RealFpsProbe, target_fps: float, workload: str) -> None:
    """Apply the single authoritative FPS acceptance rule with pacing diagnostics."""

    assert probe.real_fps >= target_fps, (
        f"{workload} achieved {probe.real_fps:.1f} Real FPS "
        f"(p99 frame {probe.p99_frame_ms:.3f} ms; "
        f"{len(probe.frame_times_ms)} frames in {probe.elapsed_seconds:.3f}s)"
    )
