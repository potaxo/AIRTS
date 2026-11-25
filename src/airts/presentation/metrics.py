"""Rolling battlefield and frame submission measurements."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil

REAL_FPS_FRAME_TIME_PERCENTILE = 0.99


def real_fps_from_frame_times(frame_times_ms: Iterable[float]) -> float:
    """Return AIRTS's permanent stutter-sensitive FPS acceptance metric."""

    frame_p99_ms = _percentile(frame_times_ms, REAL_FPS_FRAME_TIME_PERCENTILE)
    return 1000.0 / frame_p99_ms if frame_p99_ms > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class PresentationMetrics:
    """Rolling measurements for the application-to-compositor presentation path."""

    submit_fps: float = 0.0
    one_percent_low_fps: float = 0.0
    frame_p95_ms: float = 0.0
    render_p95_ms: float = 0.0
    present_p95_ms: float = 0.0
    simulation_p95_ms: float = 0.0
    battlefield_fps: float = 0.0
    simulation_tps: float = 0.0


class PresentationProfiler:
    """Measure frame pacing without adding another limiter or profiler dependency."""

    SAMPLE_SECONDS = 2.0
    MAX_SAMPLE_COUNT = 20_000
    REFRESH_SECONDS = 0.25

    def __init__(self) -> None:
        self._presented_at: deque[float] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._frame_ms: deque[float] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._render_ms: deque[float] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._present_ms: deque[float] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._simulation_samples: deque[tuple[float, float]] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._simulation_ticks: deque[tuple[float, int]] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._battlefield_intervals: deque[tuple[float, float]] = deque(
            maxlen=self.MAX_SAMPLE_COUNT
        )
        self._last_battlefield_frame: tuple[int, float] | None = None
        self._last_battlefield_at = 0.0
        self._last_refresh = 0.0
        self.metrics = PresentationMetrics()

    def reset_battlefield(self) -> None:
        self._simulation_ticks.clear()
        self._battlefield_intervals.clear()
        self._last_battlefield_frame = None
        self._last_refresh = float("-inf")

    def record(
        self,
        *,
        presented_at: float,
        frame_ms: float,
        render_ms: float,
        present_ms: float,
        simulation_ms: float,
        battlefield_frame: tuple[int, float] | None = None,
        simulation_tick: int | None = None,
    ) -> PresentationMetrics:
        self._presented_at.append(presented_at)
        self._frame_ms.append(frame_ms)
        self._render_ms.append(render_ms)
        self._present_ms.append(present_ms)
        if simulation_ms > 0.0:
            self._simulation_samples.append((presented_at, simulation_ms))
        if simulation_tick is None:
            if self._simulation_ticks:
                self.reset_battlefield()
        else:
            self._simulation_ticks.append((presented_at, simulation_tick))
            previous = self._last_battlefield_frame
            if battlefield_frame is not None and (previous is None or battlefield_frame > previous):
                if previous is not None:
                    self._battlefield_intervals.append(
                        (presented_at, (presented_at - self._last_battlefield_at) * 1000.0)
                    )
                self._last_battlefield_frame = battlefield_frame
                self._last_battlefield_at = presented_at
        while (
            len(self._presented_at) > 1
            and presented_at - self._presented_at[0] > self.SAMPLE_SECONDS
        ):
            self._presented_at.popleft()
            self._frame_ms.popleft()
            self._render_ms.popleft()
            self._present_ms.popleft()
        while (
            self._simulation_samples
            and presented_at - self._simulation_samples[0][0] > self.SAMPLE_SECONDS
        ):
            self._simulation_samples.popleft()
        while (
            len(self._simulation_ticks) > 1
            and presented_at - self._simulation_ticks[0][0] > self.SAMPLE_SECONDS
        ):
            self._simulation_ticks.popleft()
        while (
            self._battlefield_intervals
            and presented_at - self._battlefield_intervals[0][0] > self.SAMPLE_SECONDS
        ):
            self._battlefield_intervals.popleft()
        if presented_at - self._last_refresh < self.REFRESH_SECONDS:
            return self.metrics
        self._last_refresh = presented_at
        elapsed = (
            self._presented_at[-1] - self._presented_at[0] if len(self._presented_at) > 1 else 0.0
        )
        submit_fps = (len(self._presented_at) - 1) / elapsed if elapsed > 0.0 else 0.0
        battlefield_p99 = _percentile(
            (gap for _, gap in self._battlefield_intervals), REAL_FPS_FRAME_TIME_PERCENTILE
        )
        if self._last_battlefield_frame is not None:
            # During an ongoing freeze the next real interval is already at
            # least this long, even if the renderer keeps submitting frames.
            battlefield_p99 = max(
                battlefield_p99, (presented_at - self._last_battlefield_at) * 1000.0
            )
        simulation_tps = 0.0
        if self._simulation_ticks:
            first_at, first_tick = self._simulation_ticks[0]
            if presented_at > first_at and simulation_tick is not None:
                simulation_tps = (simulation_tick - first_tick) / (presented_at - first_at)
        self.metrics = PresentationMetrics(
            submit_fps=submit_fps,
            one_percent_low_fps=real_fps_from_frame_times(self._frame_ms),
            frame_p95_ms=_percentile(self._frame_ms, 0.95),
            render_p95_ms=_percentile(self._render_ms, 0.95),
            present_p95_ms=_percentile(self._present_ms, 0.95),
            simulation_p95_ms=_percentile(
                deque(value for _, value in self._simulation_samples),
                0.95,
            ),
            battlefield_fps=1000.0 / battlefield_p99 if battlefield_p99 > 0.0 else 0.0,
            simulation_tps=simulation_tps,
        )
        return self.metrics


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, ceil(fraction * len(ordered)) - 1))
    return ordered[index]
