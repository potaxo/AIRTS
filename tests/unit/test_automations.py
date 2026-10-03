"""Unit tests for automation lifecycle and deterministic geometry planning."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    AutomationTransitionError,
    PatrolParameters,
    build_patrol_waypoints,
)
from airts.contracts.spatial import Point, PointTarget, PolygonRegion
from airts.world.map import GameMap


def _automation() -> Automation:
    target = PointTarget(Point(3, 3))
    return Automation(
        automation_id="automation_001",
        title="Patrol Test Area",
        kind=AutomationKind.PATROL,
        owner_id="player",
        priority=0,
        created_tick=0,
        modified_tick=0,
        original_instruction="",
        entity_ids=["unit"],
        parameters=PatrolParameters(target, (Point(3, 3),)),
    )


def test_lifecycle_records_legal_transitions_and_reasons() -> None:
    automation = _automation()

    automation.transition(AutomationStatus.VALIDATING, 0, "VALIDATION_STARTED")
    automation.transition(AutomationStatus.ACTIVE, 0, "VALIDATION_SUCCEEDED")
    automation.transition(AutomationStatus.WAITING, 3, "NO_UNITS")
    automation.transition(AutomationStatus.ACTIVE, 5, "UNITS_AVAILABLE")
    automation.transition(AutomationStatus.COMPLETED, 8, "DONE")

    assert automation.status is AutomationStatus.COMPLETED
    assert [item.current for item in automation.transition_history] == [
        AutomationStatus.PROPOSED,
        AutomationStatus.VALIDATING,
        AutomationStatus.ACTIVE,
        AutomationStatus.WAITING,
        AutomationStatus.ACTIVE,
        AutomationStatus.COMPLETED,
    ]
    assert automation.reason_code == "DONE"


def test_lifecycle_rejects_illegal_or_mutating_same_state_transitions() -> None:
    automation = _automation()

    with pytest.raises(AutomationTransitionError, match="illegal transition"):
        automation.transition(AutomationStatus.ACTIVE, 0, "SKIPPED_VALIDATION")
    with pytest.raises(AutomationTransitionError, match="same-state"):
        automation.transition(AutomationStatus.PROPOSED, 0, "CHANGED_REASON")


def test_lifecycle_supports_confirmation_before_activation() -> None:
    automation = _automation()

    automation.transition(AutomationStatus.VALIDATING, 0, "VALIDATION_STARTED")
    automation.transition(AutomationStatus.AWAITING_CONFIRMATION, 0, "CONFIRMATION_REQUIRED")
    automation.transition(AutomationStatus.ACTIVE, 2, "PLAYER_CONFIRMED")

    assert automation.status is AutomationStatus.ACTIVE


def test_blocked_automation_can_be_paused_and_retried() -> None:
    automation = _automation()
    automation.transition(AutomationStatus.VALIDATING, 0, "VALIDATION_STARTED")
    automation.transition(AutomationStatus.ACTIVE, 0, "VALIDATION_SUCCEEDED")

    automation.transition(AutomationStatus.BLOCKED, 4, "NO_PATH")
    automation.transition(AutomationStatus.PAUSED, 5, "PLAYER_PAUSED")
    automation.transition(AutomationStatus.ACTIVE, 7, "PLAYER_RESUMED")

    assert automation.status is AutomationStatus.ACTIVE
    assert automation.transition_history[-3].current is AutomationStatus.BLOCKED


def test_area_waypoints_are_deterministic_and_inside_region(
    make_map: Callable[[int], GameMap],
) -> None:
    game_map = make_map(1)
    region = PolygonRegion((Point(2, 2), Point(9, 2), Point(9, 8), Point(2, 8)))

    first = build_patrol_waypoints(region, game_map)
    second = build_patrol_waypoints(region, game_map)

    assert first == second
    assert 1 < len(first) <= 24
    assert all(region.contains(point) for point in first)
