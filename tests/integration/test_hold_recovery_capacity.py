"""Compatible resting destinations settle after contact pressure ends."""

from __future__ import annotations

import numpy as np
import pytest
from tests.integration.scenarios import injected_overlap_simulation
from tests.integration.test_overlap_recovery import _advance_checked, _positions

from airts.contracts.automations import DefendParameters
from airts.contracts.commands import CreateDefendCommand, HoldPositionCommand, MoveCommand
from airts.contracts.spatial import Point, rectangle_region
from airts.navigation.collision import collision_radius
from airts.simulation import Simulation
from airts.systems.movement import HOLD_TOLERANCE
from airts.world.entities import UnitState
from airts.world.map import EntityKind


def _advance_weak_checked(simulation: Simulation, ids: tuple[str, ...], before: np.ndarray):
    after, metrics = _advance_checked(simulation, ids, before)
    limits = np.array(
        [simulation.entities[key].speed * simulation.TICK_SECONDS * 0.5 for key in ids]
    )
    assert float((np.linalg.norm(after - before, axis=1) - limits).max()) <= 1e-8
    assert metrics[2] <= 1e-7
    return after, metrics


def _assert_passive_half_steps(
    simulation: Simulation,
    ids: tuple[str, ...],
    passive: tuple[str, ...],
    before: np.ndarray,
    after: np.ndarray,
) -> None:
    indices = {key: index for index, key in enumerate(ids)}
    rows = np.array([indices[key] for key in passive])
    limits = np.array(
        [simulation.entities[key].speed * simulation.TICK_SECONDS * 0.5 for key in passive]
    )
    assert float((np.linalg.norm(after[rows] - before[rows], axis=1) - limits).max()) <= 1e-8


def _resting_formation(
    unit_count: int, behavior: str = "hold", selected_count: int = 1
) -> tuple[Simulation, tuple[str, ...], tuple[str, ...], dict[str, Point], str | None]:
    simulation, ids, selected = injected_overlap_simulation(
        unit_count, 1.0, selected_count=selected_count
    )
    anchors = {key: simulation.entities[key].position for key in ids}
    automation_id = None
    if behavior == "hold":
        assert simulation.execute(HoldPositionCommand(ids)).accepted
    else:
        result = simulation.execute(
            CreateDefendCommand(ids, rectangle_region(Point(32, 32), Point(64, 64)))
        )
        assert result.accepted and result.automation_id is not None
        automation_id = result.automation_id
        parameters = simulation.automations[automation_id].parameters
        assert isinstance(parameters, DefendParameters)
        # Preparation records an already-arrived, physically legal tight defense.
        # The business station identities stay fixed throughout recovery.
        parameters.stations = dict(anchors)
        for key in ids:
            entity = simulation.entities[key]
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.DEFENDING
            entity.defend_arrived = True
    return simulation, ids, selected, anchors, automation_id


def _displace_legally(
    simulation: Simulation, ids: tuple[str, ...], selected: tuple[str, ...], direction: Point
) -> None:
    axis = int(direction.y != 0)
    sign = direction.y if axis else direction.x
    points = [simulation.entities[key].position for key in ids]
    edge = max((point.y if axis else point.x) * sign for point in points) * sign
    for index, key in enumerate(selected):
        entity = simulation.entities[key]
        outside = edge + sign * (5.3 + index * 0.9)
        entity.position = (
            Point(entity.position.x + index * 0.9, outside)
            if axis
            else Point(outside, entity.position.y + index * 0.9)
        )
        simulation.occupancy.move(key, entity.occupied_cells, frozenset(ids))


def _assert_restored_and_stable(
    simulation: Simulation,
    ids: tuple[str, ...],
    anchors: dict[str, Point],
    behavior: str,
    automation_id: str | None = None,
) -> None:
    tolerance = HOLD_TOLERANCE if behavior == "hold" else simulation.DEFEND_STATION_TOLERANCE
    errors = {key: simulation.entities[key].position.distance_to(anchors[key]) for key in ids}
    assert max(errors.values()) <= tolerance + 1e-8, (
        "Compatible unoccupied resting destinations must settle after pressure ends: "
        f"{sum(error <= tolerance + 1e-8 for error in errors.values())}/{len(ids)} restored; "
        f"maximum error {max(errors.values()):.9f}"
    )
    all_mobile_ids = tuple(
        sorted(key for key, entity in simulation.entities.items() if entity.is_movable)
    )
    before = _positions(simulation, all_mobile_ids)
    settled = before.copy()
    for _ in range(40):
        before, metrics = _advance_weak_checked(simulation, all_mobile_ids, before)
        assert metrics[2] <= 1e-7
        assert abs(before - settled).max() <= 1e-8
    for key in ids:
        entity = simulation.entities[key]
        assert entity.owner_id == "player"
        assert not entity.path and entity.move_target is None
        assert entity.recovery_request_tick is None and not entity.recovery_released
        if behavior == "hold":
            assert entity.hold_anchor == anchors[key] and entity.state is UnitState.HOLDING
        else:
            assert entity.state is UnitState.DEFENDING and entity.defend_arrived
            assert simulation.assignments[key] == automation_id
    if automation_id is not None:
        parameters = simulation.automations[automation_id].parameters
        assert isinstance(parameters, DefendParameters) and parameters.stations == anchors


@pytest.mark.parametrize("unit_count", [100, 400, 1_000])
@pytest.mark.parametrize("behavior", ["hold", "settled-defend"])
def test_vacant_compatible_hold_anchor_recovers_without_persistent_pull(
    unit_count: int, behavior: str
) -> None:
    simulation, ids, selected, anchors, automation_id = _resting_formation(unit_count, behavior)
    identities = {key: simulation.entities[key] for key in ids}
    # Preparation is legal and separate from the normal-operation replay: one body has
    # yielded beyond the formation, leaving its unique, compatible original anchor empty.
    _displace_legally(simulation, ids, selected, Point(1, 0))
    before = _positions(simulation, ids)
    for _ in range(1_000):
        before, metrics = _advance_weak_checked(simulation, ids, before)
        assert metrics[2] <= 1e-7
    _assert_restored_and_stable(simulation, ids, anchors, behavior, automation_id)
    for key in ids:
        assert simulation.entities[key] is identities[key]


@pytest.mark.parametrize("direction", [Point(-1, 0), Point(0, -1), Point(0, 1)])
def test_compatible_hold_return_has_no_fixed_direction_priority(direction: Point) -> None:
    simulation, ids, selected, anchors, _ = _resting_formation(100)
    _displace_legally(simulation, ids, selected, direction)
    before = _positions(simulation, ids)
    for _ in range(1_000):
        before, metrics = _advance_weak_checked(simulation, ids, before)
        assert metrics[2] <= 1e-7
    _assert_restored_and_stable(simulation, ids, anchors, "hold")


def test_multiple_displaced_hold_members_all_return_to_their_original_anchors() -> None:
    simulation, ids, selected, anchors, _ = _resting_formation(100, selected_count=3)
    _displace_legally(simulation, ids, selected, Point(1, 0))
    before = _positions(simulation, ids)
    for _ in range(1_000):
        before, metrics = _advance_weak_checked(simulation, ids, before)
        assert metrics[2] <= 1e-7
    _assert_restored_and_stable(simulation, ids, anchors, "hold")


def test_actual_strong_passage_preserves_and_recovers_all_passive_hold_anchors() -> None:
    simulation, ids, selected, anchors, _ = _resting_formation(100)
    passive = tuple(key for key in ids if key not in selected)
    passive_anchors = {key: anchors[key] for key in passive}
    assert simulation.execute(MoveCommand(selected, Point(70, 48))).accepted
    destination = simulation.entities[selected[0]].move_target
    assert destination is not None
    before = _positions(simulation, ids)
    for _ in range(1_000):
        after, metrics = _advance_checked(simulation, ids, before)
        _assert_passive_half_steps(simulation, ids, passive, before, after)
        before = after
        assert metrics[2] <= 1e-7
    assert not simulation.entities[selected[0]].path
    completed = next(
        event
        for event in reversed(simulation.events.events)
        if event.subject_id == selected[0] and event.event_type.value == "movement_completed"
    )
    assert Point(*completed.details["position"]).distance_to(destination) <= 1e-8
    assert simulation.entities[selected[0]].position.distance_to(destination) <= 0.6
    _assert_restored_and_stable(simulation, passive, passive_anchors, "hold")


@pytest.mark.parametrize("displace_second", [False, True])
def test_independent_hold_destination_regions_recover_without_shared_leader(
    displace_second: bool,
) -> None:
    simulation, ids, _, _, _ = _resting_formation(200)
    first, second = ids[:100], ids[100:]
    for group, center in ((first, Point(30, 48)), (second, Point(66, 48))):
        for index, key in enumerate(group):
            entity = simulation.entities[key]
            entity.position = Point(center.x + (index % 10 - 4.5) * 0.6, 45.3 + index // 10 * 0.6)
            simulation.occupancy.move(key, entity.occupied_cells, frozenset(ids))
    assert simulation.execute(HoldPositionCommand(ids)).accepted
    anchors = {key: simulation.entities[key].position for key in ids}
    untouched = _positions(simulation, second)
    _displace_legally(simulation, first, (first[44],), Point(1, 0))
    if displace_second:
        _displace_legally(simulation, second, (second[44],), Point(-1, 0))
    before = _positions(simulation, ids)
    for _ in range(1_000):
        before, _ = _advance_weak_checked(simulation, ids, before)
        if not displace_second:
            assert abs(_positions(simulation, second) - untouched).max() <= 1e-8
    _assert_restored_and_stable(simulation, ids, anchors, "hold")


def test_repeated_strong_pressure_restarts_return_and_preserves_passive_anchors() -> None:
    simulation, ids, selected, anchors, _ = _resting_formation(100)
    passive = tuple(key for key in ids if key not in selected)
    passive_anchors = {key: anchors[key] for key in passive}
    before = _positions(simulation, ids)
    for destination in (Point(70, 48), Point(25, 48)):
        assert simulation.execute(MoveCommand(selected, destination)).accepted
        accepted_target = simulation.entities[selected[0]].move_target
        assert accepted_target is not None
        for _ in range(1_000):
            after, metrics = _advance_checked(simulation, ids, before)
            _assert_passive_half_steps(simulation, ids, passive, before, after)
            before = after
            assert metrics[2] <= 1e-7
        completed = next(
            event
            for event in reversed(simulation.events.events)
            if event.subject_id == selected[0] and event.event_type.value == "movement_completed"
        )
        assert Point(*completed.details["position"]).distance_to(accepted_target) <= 1e-8
        assert simulation.entities[selected[0]].position.distance_to(accepted_target) <= 0.6
        _assert_restored_and_stable(simulation, passive, passive_anchors, "hold")
        before = _positions(simulation, ids)


def test_mixed_profiles_return_with_their_existing_half_speed_budgets() -> None:
    simulation, ids, _, _, _ = _resting_formation(100)
    kinds = (EntityKind.SCOUT, EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK, EntityKind.BUILDER)
    for row in range(10):
        x = 44.0
        previous_radius = 0.0
        for column in range(10):
            index = row * 10 + column
            entity = simulation.entities[ids[index]]
            entity.kind = kinds[index % len(kinds)]
            radius = collision_radius(entity.kind)
            x += previous_radius + radius
            entity.position = Point(x, 43.95 + row * 0.9)
            simulation.occupancy.move(entity.entity_id, entity.occupied_cells, frozenset(ids))
            previous_radius = radius
    assert simulation.execute(HoldPositionCommand(ids)).accepted
    anchors = {key: simulation.entities[key].position for key in ids}
    profiles = {key: simulation.entities[key].kind for key in ids}
    selected = (min(ids, key=lambda key: anchors[key].distance_to(Point(48, 48))),)
    _displace_legally(simulation, ids, selected, Point(1, 0))
    before = _positions(simulation, ids)
    for _ in range(1_000):
        before, _ = _advance_weak_checked(simulation, ids, before)
    _assert_restored_and_stable(simulation, ids, anchors, "hold")
    assert all(simulation.entities[key].kind is profiles[key] for key in ids)


def test_new_strong_request_during_joint_return_preserves_other_hold_intents() -> None:
    simulation, ids, selected, anchors, _ = _resting_formation(100, selected_count=2)
    returning, commanded = selected
    _displace_legally(simulation, ids, (returning,), Point(1, 0))
    before = _positions(simulation, ids)
    for _ in range(800):
        before, _ = _advance_weak_checked(simulation, ids, before)
        if any(simulation.entities[key].recovery_released for key in ids) and any(
            simulation.entities[key].position.distance_to(anchors[key]) > HOLD_TOLERANCE
            for key in ids
        ):
            break
    else:
        pytest.fail("The interrupted run must reach an unfinished common return phase")
    passive = tuple(key for key in ids if key != commanded)
    passive_anchors = {key: anchors[key] for key in passive}
    assert simulation.execute(MoveCommand((commanded,), Point(70, 48))).accepted
    target = simulation.entities[commanded].move_target
    assert target is not None and simulation.entities[commanded].hold_anchor is None
    assert simulation.entities[commanded].recovery_request_tick is None
    assert not simulation.entities[commanded].recovery_released
    for _ in range(1_000):
        after, metrics = _advance_checked(simulation, ids, before)
        _assert_passive_half_steps(simulation, ids, passive, before, after)
        before = after
        assert metrics[2] <= 1e-7
    completed = next(
        event
        for event in reversed(simulation.events.events)
        if event.subject_id == commanded and event.event_type.value == "movement_completed"
    )
    assert Point(*completed.details["position"]).distance_to(target) <= 1e-8
    assert simulation.entities[commanded].position.distance_to(target) <= 0.6
    _assert_restored_and_stable(simulation, passive, passive_anchors, "hold")
