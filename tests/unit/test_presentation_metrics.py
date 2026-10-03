"""Deterministic frame-pacing metrics and their acceptance probe."""

from __future__ import annotations

import pytest
from tests.performance.frame_pacing import RealFpsProbe, assert_real_fps

from airts.presentation.metrics import (
    REAL_FPS_FRAME_TIME_PERCENTILE,
    PresentationProfiler,
    real_fps_from_frame_times,
)


def test_presentation_profiler_reports_present_wait_and_frame_pacing() -> None:
    profiler = PresentationProfiler()
    metrics = profiler.metrics
    for frame in range(100):
        metrics = profiler.record(
            presented_at=frame * 0.005,
            frame_ms=5.0,
            render_ms=1.5,
            present_ms=2.5,
            simulation_ms=0.4 if frame % 10 == 0 else 0.0,
        )

    assert metrics.submit_fps == pytest.approx(200.0)
    assert metrics.one_percent_low_fps == pytest.approx(200.0)
    assert metrics.frame_p95_ms == 5.0
    assert metrics.render_p95_ms == 1.5
    assert metrics.present_p95_ms == 2.5
    assert metrics.simulation_p95_ms == 0.4


def test_presentation_profiler_reports_stutter_sensitive_real_fps() -> None:
    profiler = PresentationProfiler()
    metrics = profiler.metrics
    presented_at = 0.0
    for frame in range(200):
        frame_ms = 30.0 if frame % 20 == 0 else 5.0
        presented_at += frame_ms / 1000.0
        metrics = profiler.record(
            presented_at=presented_at,
            frame_ms=frame_ms,
            render_ms=1.5,
            present_ms=2.5,
            simulation_ms=0.0,
        )

    assert metrics.submit_fps > 150.0
    assert metrics.one_percent_low_fps == pytest.approx(1000.0 / 30.0)
    assert metrics.one_percent_low_fps < metrics.submit_fps / 4.0


def test_real_fps_acceptance_rule_remains_inverse_p99_frame_time() -> None:
    frame_times_ms = [5.0] * 190 + [30.0] * 10

    assert REAL_FPS_FRAME_TIME_PERCENTILE == 0.99
    assert real_fps_from_frame_times(frame_times_ms) == pytest.approx(1000.0 / 30.0)

    probe = RealFpsProbe(started_at=0.0)
    completed_at = 0.0
    for frame_ms in frame_times_ms:
        completed_at += frame_ms / 1000.0
        probe.frame_completed(completed_at)

    assert probe.p99_frame_ms == pytest.approx(30.0)
    assert probe.elapsed_seconds == pytest.approx(1.25)
    assert len(frame_times_ms) / probe.elapsed_seconds > 100.0
    with pytest.raises(AssertionError, match="stuttering workload achieved 33.3 Real FPS"):
        assert_real_fps(probe, 100, "stuttering workload")
    assert_real_fps(probe, 30, "within the measured pacing budget")
