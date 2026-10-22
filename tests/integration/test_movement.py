"""Physical guarantees of the replacement controller."""

from collections.abc import Callable
from itertools import pairwise
from math import ceil, floor, sqrt
from pathlib import Path

import pytest
from tests.integration.scenarios import hold_contact_simulation

from airts.adapters.persistence import load_simulation, save_simulation
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import DefendParameters
from airts.contracts.commands import (
    CancelAutomationCommand,
    CreateDefendCommand,
    CreatePatrolCommand,
    HoldPositionCommand,
    MoveCommand,
    SetFormationCommand,
    StopCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PointTarget
from airts.navigation.collision.geometry import collision_radius
from airts.simulation import Simulation
from airts.systems.movement import position_clear, skip_crowded_waypoints
from airts.world.entities import UnitState
from airts.world.map import GameMap, load_map_data
from airts.world.skirmish import create_skirmish_map
from airts.world.units.catalog import EntityKind


def test_quiet_body_writeback_clears_previous_pressure_and_keeps_wait_countdown() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "quiet_feedback",
                "name": "Quiet feedback",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "quiet", "kind": "scout", "position": [2.5, 2.5]},
                    {"id": "previous_contact", "kind": "scout", "position": [8.5, 8.5]},
                ],
            }
        )
    )
    body = simulation.entities["previous_contact"]
    body.collision_pressure = 1
    body.contact_wait_ticks = 2
    simulation.advance()
    assert body.collision_pressure == 0
    assert body.contact_wait_ticks == 1
    simulation.advance()
    assert body.contact_wait_ticks == 0
    assert body.position == Point(8.5, 8.5)
    assert simulation.entities["quiet"].position == Point(2.5, 2.5)


def test_contact_wait_is_not_arrival_at_a_defenders_occupied_station() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "waiting_return",
                "name": "Waiting return",
                "width": 16,
                "height": 16,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "defender", "kind": "light_artillery", "position": [6.5, 6.5]},
                    {"id": "occupant", "kind": "scout", "position": [12.5, 12.5]},
                ],
            }
        )
    )
    result = simulation.execute(CreateDefendCommand(("defender",), PointTarget(Point(6.5, 6.5))))
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, DefendParameters)
    station = parameters.stations["defender"]
    defender = simulation.entities["defender"]
    # A settled defender was displaced; its station is now occupied. The valid
    # return intent must wait without interpreting the zero steering step as arrival.
    defender.position = Point(station.x - 1, station.y)
    occupant = simulation.entities["occupant"]
    occupant.position = station
    allowed = frozenset(("defender", "occupant"))
    simulation.occupancy.move("defender", defender.occupied_cells, allowed)
    simulation.occupancy.move("occupant", occupant.occupied_cells, allowed)
    assert simulation.execute(HoldPositionCommand(("occupant",))).accepted
    defender.path = [station]
    defender.move_target = station
    defender.defend_arrived = True
    defender.state = UnitState.DEFENDING
    defender.contact_wait_ticks = 4
    previous = defender.position
    simulation.advance()
    assert defender.position == previous
    assert defender.move_target == station
    assert defender.path == [station]
    assert defender.contact_wait_ticks == 3


@pytest.mark.parametrize("kind", [EntityKind.SCOUT, EntityKind.HEAVY_TANK, EntityKind.BUILDER])
@pytest.mark.parametrize(
    ("terrain", "speed_ratio"),
    [("grass", 1.0), ("forest", 2 / 3), ("road", 4 / 3), ("bridge", 1.25)],
)
def test_terrain_changes_actual_movement_speed(
    kind: EntityKind, terrain: str, speed_ratio: float
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "terrain_speed",
                "name": "Terrain speed",
                "width": 24,
                "height": 12,
                "terrain": {"default": terrain},
                "entities": [{"id": "unit", "kind": kind.value, "position": [2.5, 5.5]}],
            }
        )
    )
    unit = simulation.entities["unit"]
    assert simulation.execute(MoveCommand(("unit",), Point(20.5, 5.5))).accepted
    start = unit.position
    simulation.advance()
    assert unit.position.distance_to(start) == pytest.approx(
        unit.speed * Simulation.TICK_SECONDS * speed_ratio
    )
    assert simulation.occupancy.cells_for("unit") == unit.occupied_cells


def test_terrain_speed_updates_when_entering_and_leaving_forest() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "terrain_boundary",
                "name": "Terrain boundary",
                "width": 24,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": [[4, 0, 2, 12, "forest"]]},
                "entities": [{"id": "unit", "kind": "scout", "position": [2.5, 5.5]}],
            }
        )
    )
    unit = simulation.entities["unit"]
    assert simulation.execute(MoveCommand(("unit",), Point(20.5, 5.5))).accepted
    samples: dict[str, list[float]] = {"grass": [], "forest": []}
    for _ in range(24):
        start = unit.position
        terrain = simulation.game_map.terrain_at(start).value
        simulation.advance()
        distance = unit.position.distance_to(start)
        samples[terrain].append(distance)
        limit = 1 / 3 if terrain == "forest" else 0.5
        assert distance <= limit + 1e-8
    assert unit.position.x > 6
    assert max(samples["forest"]) == pytest.approx(1 / 3)
    assert samples["grass"][0] == pytest.approx(0.5)
    assert samples["grass"][-1] == pytest.approx(0.5)


def test_forest_diagonal_uses_speed_allowance_without_stopping_at_each_cell() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "forest_diagonal",
                "name": "Forest diagonal",
                "width": 32,
                "height": 32,
                "terrain": {"default": "forest"},
                "entities": [{"id": "unit", "kind": "scout", "position": [3.5, 3.5]}],
            }
        )
    )
    unit = simulation.entities["unit"]
    target = Point(23.5, 23.5)
    assert simulation.execute(MoveCommand(("unit",), target)).accepted
    for _ in range(100):
        start = unit.position
        simulation.advance()
        distance = unit.position.distance_to(start)
        assert distance <= 1 / 3 + 1e-8
        if not unit.path:
            break
        assert distance >= 0.30
    assert unit.position == target
    assert simulation.tick <= 95


@pytest.mark.parametrize("obstacle", ["rock", "water", "building"])
@pytest.mark.parametrize("replanned", [False, True], ids=["stalled", "retained-after-replan"])
def test_stalled_lookahead_retains_a_turn_around_grazing_obstacles(
    obstacle: str,
    replanned: bool,
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "grazing_route",
                "name": "Grazing route",
                "width": 24,
                "height": 16,
                "terrain": {
                    "default": "grass",
                    "rectangles": [] if obstacle == "building" else [[9, 5, 4, 4, obstacle]],
                },
                "entities": [
                    {"id": "unit", "kind": "heavy_tank", "position": [3.5, 4.5]},
                    *(
                        [{"id": "building", "kind": "factory", "position": [9, 5]}]
                        if obstacle == "building"
                        else []
                    ),
                ],
            }
        )
    )
    unit = simulation.entities["unit"]
    assert simulation.execute(MoveCommand(("unit",), Point(20.5, 5.1))).accepted
    # A clear center ray skims the obstacle, while the heavy tank cannot fit.
    # Reproduce the retained route at a stalled corner, then exercise recovery.
    unit.path = [Point(8.5, 4.5), Point(20.5, 5.1)]
    if replanned:
        simulation._movement_blocked.add(unit.entity_id)
    else:
        unit.no_progress_ticks = simulation.NO_PROGRESS_YIELD_TICKS
    skip_crowded_waypoints(simulation, unit)
    assert unit.path[0] == Point(8.5, 4.5)
    _advance_with_clearance(simulation, 100)
    assert not unit.path
    assert unit.position.distance_to(Point(20.5, 5.1)) < 1e-8


@pytest.mark.parametrize("target", [Point(0.1, 4.5), Point(8.9, 7.5)])
def test_single_gather_uses_reachable_capacity_at_edges(target: Point) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "edge_gather",
                "name": "Edge gather",
                "width": 24,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": [[9, 5, 4, 4, "water"]]},
                "entities": [{"id": "unit", "kind": "heavy_tank", "position": [3.5, 4.5]}],
            }
        )
    )
    assert simulation.execute(MoveCommand(("unit",), target)).accepted
    unit = simulation.entities["unit"]
    goal = unit.move_target
    assert goal is not None and goal != target
    _advance_with_clearance(simulation, 100)
    assert unit.position.distance_to(goal) < 1e-8 and not unit.path


@pytest.mark.parametrize(("terrain", "maximum_step"), [("forest", 1 / 6), ("road", 1 / 3)])
def test_hold_return_obeys_terrain_adjusted_passive_speed(
    terrain: str, maximum_step: float
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "terrain_hold_return",
                "name": "Terrain Hold return",
                "width": 24,
                "height": 12,
                "terrain": {"default": terrain},
                "entities": [{"id": "unit", "kind": "scout", "position": [5.5, 5.5]}],
            }
        )
    )
    unit = simulation.entities["unit"]
    anchor = unit.position
    assert simulation.execute(HoldPositionCommand(("unit",))).accepted
    unit.position = Point(7.5, 5.5)
    simulation.occupancy.move("unit", unit.occupied_cells, frozenset(simulation.entities))
    start = unit.position
    simulation.advance()
    assert 0 < unit.position.distance_to(start) <= maximum_step + 1e-8
    for _ in range(40):
        start = unit.position
        simulation.advance()
        assert unit.position.distance_to(start) <= maximum_step + 1e-8
    assert unit.state is UnitState.HOLDING
    assert unit.hold_anchor == anchor
    assert unit.position.distance_to(anchor) <= 0.04


def test_move_from_injected_overlapping_selection_allocates_compatible_destinations() -> None:
    """An artificial compressed source must not become impossible final goals."""
    simulation = Simulation(
        load_map_data(
            {
                "id": "compressed_selection_goals",
                "name": "Compressed selection goals",
                "width": 24,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "scout", "kind": "scout", "position": [2.5, 6.5]},
                    {"id": "light", "kind": "light_tank", "position": [4.5, 6.5]},
                    {"id": "heavy", "kind": "heavy_tank", "position": [6.5, 6.5]},
                ],
            }
        )
    )
    for entity in simulation.entities.values():
        entity.position = Point(4.5, 6.5)
        simulation.occupancy.move(
            entity.entity_id, entity.occupied_cells, frozenset(simulation.entities)
        )
    ids = tuple(simulation.entities)
    assert simulation.execute(MoveCommand(ids, Point(18.5, 8.5))).accepted
    for index, key in enumerate(ids):
        first = simulation.entities[key]
        assert first.move_target is not None
        for other_key in ids[index + 1 :]:
            second = simulation.entities[other_key]
            assert second.move_target is not None
            assert first.move_target.distance_to(second.move_target) >= (
                collision_radius(first.kind) + collision_radius(second.kind) - 1e-8
            )


def test_explicit_strict_move_preserves_existing_physically_legal_translated_layout() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "legal_selection_goals",
                "name": "Legal selection goals",
                "width": 24,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "scout", "kind": "scout", "position": [2.5, 6.5]},
                    {"id": "heavy", "kind": "heavy_tank", "position": [4.5, 6.5]},
                ],
            }
        )
    )
    assert simulation.execute(SetFormationCommand(("scout", "heavy"), True)).accepted
    assert simulation.execute(MoveCommand(("scout", "heavy"), Point(12.5, 8.5))).accepted
    assert simulation.entities["scout"].move_target == Point(11.5, 8.5)
    assert simulation.entities["heavy"].move_target == Point(13.5, 8.5)


@pytest.mark.parametrize("unit_count", [1, 3, 120])
@pytest.mark.parametrize("far", [False, True], ids=["micro", "march"])
def test_default_move_gathers_independently_of_distance_and_army_size(
    unit_count: int, far: bool
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "explicit_move_formation",
                "name": "Explicit move formation",
                "width": 96,
                "height": 64,
                "terrain": {"default": "grass"},
                "entities": [
                    {
                        "id": f"unit_{index:03d}",
                        "kind": "scout",
                        "position": [3.5 + index % 12 * 3, 3.5 + index // 12 * 3],
                    }
                    for index in range(unit_count)
                ],
            }
        )
    )
    ids = tuple(simulation.entities)
    positions = {key: simulation.entities[key].position for key in ids}
    center = Point(
        sum(point.x for point in positions.values()) / unit_count,
        sum(point.y for point in positions.values()) / unit_count,
    )
    target = Point(60.5, 35.5) if far else Point(center.x + 6, center.y + 4)
    assert simulation.execute(MoveCommand(ids, target)).accepted
    goals = []
    for key in ids:
        goal = simulation.entities[key].move_target
        assert goal is not None
        goals.append(goal)
    if unit_count > 1:
        assert max(goal.distance_to(target) for goal in goals) < max(
            point.distance_to(center) for point in positions.values()
        )
        for index, goal in enumerate(goals):
            assert all(goal.distance_to(other) >= 0.6 - 1e-8 for other in goals[index + 1 :])
    if unit_count <= 3:
        _advance_with_clearance(simulation, 250)
        for key, goal in zip(ids, goals, strict=True):
            entity = simulation.entities[key]
            assert entity.move_target is None
            # Arrived idle bodies can yield to companions finishing their approach.
            assert entity.position.distance_to(goal) <= collision_radius(entity.kind) + 1e-8


@pytest.mark.parametrize("distance", [3.0, 20.0])
def test_nearby_and_distant_moves_and_queued_legs_always_gather(distance: float) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "move_distance_boundary",
                "name": "Move distance boundary",
                "width": 48,
                "height": 24,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "a", "kind": "scout", "position": [3.5, 6.5]},
                    {"id": "b", "kind": "scout", "position": [7.5, 6.5]},
                ],
            }
        )
    )
    ids = tuple(simulation.entities)
    target = Point(5.5 + distance, 6.5)
    assert simulation.execute(MoveCommand(ids, target)).accepted
    goals = {key: simulation.entities[key].move_target for key in ids}
    assert all(goal is not None for goal in goals.values())
    a, b = goals["a"], goals["b"]
    assert a is not None and b is not None
    assert a.distance_to(b) < 2
    fields = simulation.navigation_field_build_count
    micro = Point(target.x + 3, target.y)
    assert simulation.execute(MoveCommand(ids, micro, queued=True)).accepted
    for key in ids:
        goal = goals[key]
        assert goal is not None
        queue = simulation.entities[key].order_queue
        assert len(queue) == 1
        assert queue[0].position.distance_to(micro) < 2
    assert (
        simulation.entities["a"]
        .order_queue[0]
        .position.distance_to(simulation.entities["b"].order_queue[0].position)
        < 2
    )
    # Validating a queue must not build a map-sized field for each final slot.
    assert simulation.navigation_field_build_count == fields
    assert simulation.execute(MoveCommand(ids, Point(4.5, 18.5), queued=True)).accepted
    queued = [simulation.entities[key].order_queue[-1].position for key in ids]
    assert queued[0].distance_to(queued[1]) < 2
    assert simulation.navigation_field_build_count == fields
    _advance_with_clearance(simulation, 200)
    assert simulation.navigation_field_build_count == fields
    for key, goal in zip(ids, queued, strict=True):
        entity = simulation.entities[key]
        assert not entity.path and not entity.order_queue
        assert entity.position.distance_to(goal) <= collision_radius(entity.kind) + 1e-8


@pytest.mark.parametrize("terrain", ["forest", "water"])
def test_march_shares_weighted_detour_and_restores_through_save_and_replay(
    terrain: str, tmp_path: Path
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "shared_march_detour",
                "name": "Shared march detour",
                "width": 48,
                "height": 24,
                "terrain": {"default": "grass", "rectangles": [[12, 8, 20, 6, terrain]]},
                "entities": [
                    {"id": "a", "kind": "scout", "position": [3.5, 10.5]},
                    {"id": "b", "kind": "scout", "position": [3.5, 14.5]},
                ],
            }
        )
    )
    ids = tuple(simulation.entities)
    target = Point(40.5, 11.5)
    expected = simulation._allocate_destinations(ids, target)
    assert simulation.execute(MoveCommand(ids, target)).accepted
    assert simulation.navigation_field_build_count == 1
    for key in ids:
        entity = simulation.entities[key]
        assert entity.move_target == expected[key]
        assert all(
            simulation._routes.clear_segment(start, end, frozenset())
            for start, end in pairwise((entity.position, *entity.path))
        )
    assert simulation.execute(MoveCommand(ids, Point(4.5, 11.5), queued=True)).accepted
    return_goals = {key: simulation.entities[key].order_queue[-1].position for key in ids}
    simulation.advance(15)
    save_path = tmp_path / "march.json"
    save_simulation(simulation, save_path)
    restored = load_simulation(save_path)
    for world in (simulation, restored):
        _advance_with_clearance(world, 320)
        # The queued return retains one common approach field, rather than
        # expanding a separate weighted search for every member's final slot.
        assert world.navigation_field_build_count <= 3
        for key in ids:
            assert not world.entities[key].path
            assert not world.entities[key].order_queue
            assert world.entities[key].position.distance_to(return_goals[key]) <= 0.3
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


def test_queued_manual_override_uses_current_positions_instead_of_automation_goals(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(3))
    ids = tuple(simulation.entities)
    assert simulation.execute(CreatePatrolCommand(ids, PointTarget(Point(9.5, 9.5)))).accepted
    simulation.advance()
    target = Point(4.5, 3.5)
    expected = simulation._allocate_destinations(ids, target)
    assert simulation.execute(MoveCommand(ids, target, queued=True)).accepted
    for key in ids:
        assert key not in simulation.assignments
        assert simulation.entities[key].move_target == expected[key]
        assert not simulation.entities[key].order_queue


@pytest.mark.parametrize("count", [1, 4, 120, 2000])
@pytest.mark.parametrize(
    "target", [Point(0.1, 0.1), Point(10, 10), Point(24, 12)], ids=["edge", "building", "wall"]
)
def test_bounded_gathering_matches_complete_slots_and_reports_capacity(
    count: int, target: Point
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "bounded_gathering",
                "name": "Bounded gathering",
                "width": 48,
                "height": 24,
                "terrain": {"default": "grass", "rectangles": [[24, 0, 1, 24, "rock"]]},
                "entities": [{"id": "factory", "kind": "factory", "position": [10, 10]}],
            }
        )
    )
    spatial_target = PointTarget(target)
    blocked = simulation._terrain_obstacles | simulation._building_cells()
    anchor = simulation._routes.nearest_cell(target, simulation._building_cells())
    reachable = simulation._routes.reachable_cells(anchor, simulation._building_cells())
    spacing = 0.3 * 4 + 0.12
    row_height = spacing * sqrt(3) / 2
    complete = []
    for row in range(floor(-target.y / row_height), ceil((24 - target.y) / row_height)):
        offset = (row % 2) * spacing / 2
        for column in range(
            floor((-target.x - offset) / spacing), ceil((48 - target.x - offset) / spacing)
        ):
            point = Point(target.x + column * spacing + offset, target.y + row * row_height)
            if (
                simulation.game_map.contains(point)
                and reachable[floor(point.y), floor(point.x)]
                and position_clear(point, 0.3, 48, 24, blocked)
            ):
                complete.append(point)
    expected = tuple(sorted(complete, key=lambda p: (p.distance_to(target), p.y, p.x)))
    # Growth, eviction and cold reconstruction must all choose the same exhaustive prefix.
    simulation._gathering_slots(spatial_target, 1, 0.3, require_count=False)
    local = simulation._gathering_slots(spatial_target, count, 0.3, require_count=False)
    assert local == expected[:count]
    simulation._gathering_slot_cache.clear()
    assert simulation._gathering_slots(spatial_target, count, 0.3, require_count=False) == local
    if len(local) < count:
        with pytest.raises(ValueError, match="insufficient map space"):
            simulation._gathering_slots(spatial_target, count, 0.3)
    # A different center in the same component can share reachability; it must not
    # retain a separate map-sized copy on every right click.
    next_target = PointTarget(Point(target.x + 0.1, target.y + 0.1))
    simulation._gathering_slots(next_target, 1, 0.3)
    if target != Point(24, 12):
        assert (
            simulation._gathering_reachable_cache[next_target]
            is simulation._gathering_reachable_cache[spatial_target]
        )


def test_retired_gathering_targets_bound_cache_and_reconstruct_identical_stations() -> None:
    simulation = Simulation(create_skirmish_map())
    ids = tuple(
        key
        for key, entity in simulation.entities.items()
        if entity.owner_id == "player" and entity.is_movable
    )
    first_target = PointTarget(Point(80.5, 275.5))
    first = simulation.execute(CreateDefendCommand(ids, first_target, gathering_point=True))
    assert first.accepted
    first_parameters = simulation.automations[first.automation_id or ""].parameters
    assert isinstance(first_parameters, DefendParameters)
    stations = dict(first_parameters.stations)
    assert simulation.execute(CancelAutomationCommand(first.automation_id or "")).accepted
    for offset in range(Simulation.GATHERING_CACHE_TARGET_LIMIT + 3):
        target = PointTarget(Point(90.5 + offset, 275.5))
        result = simulation.execute(CreateDefendCommand(ids, target, gathering_point=True))
        assert result.accepted
        assert simulation.execute(CancelAutomationCommand(result.automation_id or "")).accepted
        assert len(simulation._gathering_slot_cache) <= Simulation.GATHERING_CACHE_TARGET_LIMIT
        assert len(simulation._gathering_reachable_cache) <= Simulation.GATHERING_CACHE_TARGET_LIMIT
        assert (
            sum(len(entry.points) for entry in simulation._gathering_slot_cache.values())
            <= Simulation.GATHERING_CACHE_SLOT_LIMIT
        )
    assert not any(automation.entity_ids for automation in simulation.live_automations)
    # No whole-map candidate list survives for these tiny, cancelled tasks.
    assert sum(len(entry.points) for entry in simulation._gathering_slot_cache.values()) < 256
    repeated = simulation.execute(CreateDefendCommand(ids, first_target, gathering_point=True))
    assert repeated.accepted
    parameters = simulation.automations[repeated.automation_id or ""].parameters
    assert isinstance(parameters, DefendParameters)
    assert parameters.stations == stations


def _advance_with_clearance(simulation: Simulation, ticks: int = 1) -> None:
    """Check actual trajectories, total speed, static clearance and submitted occupancy."""
    units = [entity for entity in simulation.entities.values() if entity.is_movable]
    blocked = frozenset(
        (x, y)
        for y, row in enumerate(simulation.game_map.terrain)
        for x, terrain in enumerate(row)
        if not terrain.passable
    ) | frozenset(
        cell
        for entity in simulation.entities.values()
        if not entity.is_movable
        for cell in entity.occupied_cells
    )
    for _ in range(ticks):
        before = {entity.entity_id: entity.position for entity in units}
        simulation.advance()
        for index, entity in enumerate(units):
            start = before[entity.entity_id]
            step_limit = (
                entity.speed
                * Simulation.TICK_SECONDS
                / simulation.game_map.terrain_at(start).movement_cost
            )
            assert entity.position.distance_to(start) <= step_limit + 1e-8
            assert position_clear(
                entity.position,
                collision_radius(entity.kind),
                simulation.game_map.width,
                simulation.game_map.height,
                blocked,
            )
            for sample in range(1, 5):
                time = sample / 5
                assert position_clear(
                    Point(
                        start.x + (entity.position.x - start.x) * time,
                        start.y + (entity.position.y - start.y) * time,
                    ),
                    collision_radius(entity.kind),
                    simulation.game_map.width,
                    simulation.game_map.height,
                    blocked,
                )
            assert simulation.occupancy.cells_for(entity.entity_id) == entity.occupied_cells
            assert all(
                entity.entity_id in simulation.occupancy.occupants(cell)
                for cell in entity.occupied_cells
            )
            for other in units[index + 1 :]:
                other_start = before[other.entity_id]
                dx, dy = start.x - other_start.x, start.y - other_start.y
                vx = entity.position.x - start.x - other.position.x + other_start.x
                vy = entity.position.y - start.y - other.position.y + other_start.y
                time = (
                    min(1.0, max(0.0, -(dx * vx + dy * vy) / (vx * vx + vy * vy)))
                    if vx or vy
                    else 0.0
                )
                contact = collision_radius(entity.kind) + collision_radius(other.kind)
                assert (dx + vx * time) ** 2 + (dy + vy * time) ** 2 >= contact**2 - 1e-8


def _displace_holder_for_recovery(simulation: Simulation, position: Point) -> None:
    """Inject a legal displaced body to isolate recovery from the pending contact solver."""
    holder = simulation.entities["holder"]
    holder.position = position
    simulation.occupancy.move("holder", holder.occupied_cells, frozenset(simulation.entities))


@pytest.mark.parametrize("vertical", [False, True], ids=["horizontal", "vertical"])
@pytest.mark.parametrize("terrain", ["grass", "forest", "road"])
def test_opposing_steps_cannot_tunnel_or_exceed_speed(vertical: bool, terrain: str) -> None:
    def coordinates(x: float, y: float) -> list[float]:
        return [y, x] if vertical else [x, y]

    simulation = Simulation(
        load_map_data(
            {
                "id": "crossing",
                "name": "Crossing",
                "width": 12,
                "height": 12,
                "terrain": {"default": terrain},
                "entities": [
                    {"id": "a", "kind": "scout", "position": coordinates(4.5, 5.5)},
                    {"id": "b", "kind": "scout", "position": coordinates(5.8, 5.5)},
                ],
            }
        )
    )
    assert simulation.execute(MoveCommand(("a",), Point(*coordinates(9.5, 5.5)))).accepted
    assert simulation.execute(MoveCommand(("b",), Point(*coordinates(1.5, 5.5)))).accepted
    _advance_with_clearance(simulation, 40)


def test_held_unit_preserves_clearance_and_recovers_after_another_passes(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    held = simulation.entities["unit_02"].position
    assert simulation.execute(HoldPositionCommand(("unit_02",))).accepted
    assert simulation.execute(MoveCommand(("unit_01",), Point(1.5, 7.5))).accepted
    _advance_with_clearance(simulation, 60)
    assert simulation.entities["unit_01"].position == Point(1.5, 7.5)
    assert simulation.entities["unit_02"].state is UnitState.HOLDING
    assert simulation.entities["unit_02"].position.distance_to(held) <= 0.05


@pytest.mark.parametrize("behavior", ["idle", "stop", "hold"])
def test_stationary_unit_yields_to_traffic_in_single_file_throat(behavior: str) -> None:
    """The exit has space to pass, but a held whole-cell wall would reject the order."""
    simulation = hold_contact_simulation()
    holder = simulation.entities["holder"]
    anchor = holder.position
    if behavior == "hold":
        assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    elif behavior == "stop":
        assert simulation.execute(StopCommand(("holder",))).accepted
    target = Point(12.5, 5.5)
    result = simulation.execute(MoveCommand(("mover",), target))
    assert result.accepted, (
        f"a mobile collider must not become a static route wall: {result.reason}"
    )

    maximum_displacement = 0.0
    for _ in range(140):
        _advance_with_clearance(simulation)
        maximum_displacement = max(maximum_displacement, holder.position.distance_to(anchor))
        assert holder.state is (UnitState.HOLDING if behavior == "hold" else UnitState.IDLE)
        assert "holder" not in simulation.assignments
        assert not holder.pursue_target
    assert maximum_displacement > 0.05, "stationary bodies must actually yield to contact"
    assert simulation.entities["mover"].position.distance_to(target) <= 0.05
    assert not any(
        event.subject_id == "holder"
        and event.event_type in {EventType.MOVEMENT_STARTED, EventType.MOVEMENT_COMPLETED}
        for event in simulation.events.events
    ), "passive yielding/hold recovery must not fabricate a player movement order"
    if behavior == "hold":
        assert holder.position.distance_to(anchor) <= 0.05
        assert holder.hold_anchor == anchor
    else:
        assert holder.position.distance_to(anchor) > 0.05
    settled = holder.position
    _advance_with_clearance(simulation, 20)
    assert holder.position.distance_to(settled) <= 1e-4, "pressure-free units must settle"


def test_hold_recovers_displaced_body_without_active_movement_paths() -> None:
    simulation = hold_contact_simulation()
    anchor = simulation.entities["holder"].position
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    _displace_holder_for_recovery(simulation, Point(7.5, 5.5))
    assert all(not entity.path for entity in simulation.entities.values())

    _advance_with_clearance(simulation, 60)

    holder = simulation.entities["holder"]
    assert holder.state is UnitState.HOLDING
    assert holder.position.distance_to(anchor) <= 0.05, "Hold must retain the original anchor"
    settled = holder.position
    _advance_with_clearance(simulation, 20)
    assert holder.position.distance_to(settled) <= 1e-4


def test_reissued_hold_uses_current_position_as_the_new_anchor() -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    new_anchor = Point(7.5, 5.5)
    _displace_holder_for_recovery(simulation, new_anchor)
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    _displace_holder_for_recovery(simulation, Point(9.5, 5.5))

    _advance_with_clearance(simulation, 60)

    assert simulation.entities["holder"].position.distance_to(new_anchor) <= 0.05
    assert simulation.entities["holder"].state is UnitState.HOLDING


@pytest.mark.parametrize("replacement", ["stop", "move"])
def test_accepted_control_replacement_ends_hold_recovery(replacement: str) -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    displaced = Point(7.5, 5.5)
    _displace_holder_for_recovery(simulation, displaced)
    destination = Point(12.5, 5.5) if replacement == "move" else displaced
    command = (
        MoveCommand(("holder",), destination) if replacement == "move" else StopCommand(("holder",))
    )
    assert simulation.execute(command).accepted

    _advance_with_clearance(simulation, 80)

    assert simulation.entities["holder"].position.distance_to(destination) <= 0.05
    assert simulation.entities["holder"].state is UnitState.IDLE
    assert simulation.entities["holder"].hold_anchor is None


def _contact_queue(count: int = 4, *, throat: bool = False, closed: bool = False) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "contact_queue",
                "name": "Contact queue",
                "width": 24,
                "height": 12,
                "terrain": {
                    "default": "grass",
                    "rectangles": (
                        (
                            [[0, 0, 7, 5, "rock"], [0, 6, 7, 6, "rock"]]
                            + ([[7, 0, 17, 12, "rock"]] if closed else [])
                        )
                        if throat
                        else []
                    ),
                },
                "entities": [
                    {"id": f"unit_{i}", "kind": "scout", "position": [3.5 + i, 5.5]}
                    for i in range(count)
                ],
            }
        ),
        random_seed=181,
    )


def test_contact_pressure_transmits_through_a_short_stationary_queue() -> None:
    simulation = _contact_queue(throat=True)
    starts = {k: e.position for k, e in simulation.entities.items()}
    assert simulation.execute(MoveCommand(("unit_0",), Point(16.5, 5.5))).accepted
    _advance_with_clearance(simulation, 80)
    assert simulation.entities["unit_0"].position.distance_to(Point(16.5, 5.5)) < 0.05
    assert all(
        simulation.entities[k].position.distance_to(starts[k]) > 0.05
        for k in starts
        if k != "unit_0"
    )
    assert all(e.state is UnitState.IDLE and not e.path for e in simulation.entities.values())
    settled = simulation.snapshot()
    positions = {k: e.position for k, e in simulation.entities.items()}
    _advance_with_clearance(simulation, 20)
    assert positions == {k: e.position for k, e in simulation.entities.items()}
    assert settled["entities"] == simulation.snapshot()["entities"]


@pytest.mark.parametrize("offset", [0.0, 0.58], ids=["identical-centers", "slight-overlap"])
def test_initial_overlap_recovers_deterministically_without_new_orders(offset: float) -> None:
    simulations = [_contact_queue(3), _contact_queue(3)]
    for simulation in simulations:
        for i, entity in enumerate(simulation.entities.values()):
            entity.position = Point(8.5 + i * offset, 5.5)
            simulation.occupancy.move(
                entity.entity_id, entity.occupied_cells, frozenset(simulation.entities)
            )
        for _ in range(60):
            before = {k: e.position for k, e in simulation.entities.items()}
            simulation.advance()
            units = list(simulation.entities.values())
            for i, entity in enumerate(units):
                assert (
                    entity.position.distance_to(before[entity.entity_id])
                    <= entity.speed * Simulation.TICK_SECONDS + 1e-8
                )
                for other in units[i + 1 :]:
                    initial = before[entity.entity_id].distance_to(before[other.entity_id])
                    assert entity.position.distance_to(other.position) >= min(0.6, initial) - 1e-8
        assert (
            min(
                a.position.distance_to(b.position)
                for i, a in enumerate(units)
                for b in units[i + 1 :]
            )
            >= 0.6 - 1e-8
        )
        assert not any(
            event.event_type in {EventType.MOVEMENT_STARTED, EventType.MOVEMENT_COMPLETED}
            for event in simulation.events.events
        )
    assert simulations[0].export_state() == simulations[1].export_state()


def test_defender_yields_without_losing_station_or_assignment() -> None:
    simulation = hold_contact_simulation()
    result = simulation.execute(
        CreateDefendCommand(("holder",), PointTarget(simulation.entities["holder"].position))
    )
    assert result.accepted
    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    stations = dict(automation.parameters.stations)
    simulation.advance(40)
    anchor = stations["holder"]
    assert simulation.entities["holder"].position.distance_to(anchor) <= 0.05
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 5.5))).accepted
    displaced = 0.0
    for _ in range(160):
        _advance_with_clearance(simulation)
        holder = simulation.entities["holder"]
        displaced = max(displaced, holder.position.distance_to(anchor))
        assert simulation.assignments["holder"] == automation.automation_id
        assert automation.parameters.stations == stations
    assert displaced > 0.05
    assert holder.position.distance_to(anchor) <= 0.05
    settled = holder.position
    _advance_with_clearance(simulation, 20)
    assert holder.position == settled


def test_hold_unreachable_anchor_sleeps_until_static_obstacles_change() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "blocked_anchor",
                "name": "Blocked anchor",
                "width": 20,
                "height": 12,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [8, 0, 2, 12, "rock"],
                        [8, 4, 2, 4, "grass"],
                    ],
                },
                "entities": [
                    {"id": "holder", "kind": "scout", "position": [5.5, 5.5]},
                    {"id": "factory", "kind": "factory", "position": [8, 4]},
                ],
            }
        )
    )
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    # A legal body and anchor in disconnected components isolate failed-route
    # recovery (e.g. a loaded anchor after a bridge entrance was built over).
    _displace_holder_for_recovery(simulation, Point(12.5, 5.5))
    _advance_with_clearance(simulation, 100)
    holder = simulation.entities["holder"]
    assert holder.position == Point(12.5, 5.5)
    assert holder.hold_anchor == Point(5.5, 5.5)
    assert holder.recovery_blocked_cells is not None
    builds = simulation.navigation_field_build_count
    retry_tick = holder.recovery_retry_tick
    _advance_with_clearance(simulation, 100)
    assert simulation.navigation_field_build_count == builds
    assert holder.recovery_retry_tick == retry_tick
    assert simulation.remove_entity("factory", "OPEN_BRIDGE").accepted
    _advance_with_clearance(simulation, 100)
    assert holder.position.distance_to(holder.hold_anchor) <= 0.05
    assert holder.recovery_blocked_cells is None


def test_closed_throat_keeps_the_queue_safe_without_forcing_passage() -> None:
    simulation = _contact_queue(throat=True, closed=True)
    # The last stationary body occupies the goal; the one-cell throat cannot
    # fit passing discs, so the order can wait instead of exchanging identities.
    assert simulation.execute(MoveCommand(("unit_0",), Point(6.5, 5.5))).accepted
    _advance_with_clearance(simulation, 80)
    assert all(key in simulation.entities for key in ("unit_0", "unit_1", "unit_2", "unit_3"))
    assert not any(entity.hold_anchor for entity in simulation.entities.values())
    assert simulation.entities["unit_0"].path
    assert [simulation.entities[f"unit_{i}"].position.x for i in range(4)] == sorted(
        simulation.entities[f"unit_{i}"].position.x for i in range(4)
    )


def test_hold_recovery_retains_opportunistic_fire_without_enemy_pursuit() -> None:
    simulation = _contact_queue(2)
    enemy = simulation.entities["unit_1"]
    enemy.owner_id = "enemy"
    enemy.position = Point(9.5, 5.5)
    simulation.occupancy.move(enemy.entity_id, enemy.occupied_cells)
    holder = simulation.entities["unit_0"]
    anchor = holder.position
    assert simulation.execute(HoldPositionCommand((holder.entity_id,))).accepted
    holder.position = Point(5.5, 5.5)
    simulation.occupancy.move(holder.entity_id, holder.occupied_cells)
    _advance_with_clearance(simulation, 20)
    assert holder.hold_anchor == anchor
    assert holder.state is UnitState.HOLDING and not holder.pursue_target and not holder.path
    assert holder.position.distance_to(anchor) <= 0.05
    assert any(
        event.subject_id == holder.entity_id and event.event_type is EventType.PROJECTILE_LAUNCHED
        for event in simulation.events.events
    )


def test_rejected_control_replacement_preserves_hold_recovery() -> None:
    simulation = hold_contact_simulation()
    anchor = simulation.entities["holder"].position
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    _displace_holder_for_recovery(simulation, Point(7.5, 5.5))
    assert not simulation.execute(MoveCommand(("holder",), Point(1.5, 1.5))).accepted

    _advance_with_clearance(simulation, 60)

    assert simulation.entities["holder"].state is UnitState.HOLDING
    assert simulation.entities["holder"].position.distance_to(anchor) <= 0.05


def test_occupied_hold_anchor_waits_safely_then_recovers_when_cleared() -> None:
    simulation = hold_contact_simulation()
    anchor = simulation.entities["holder"].position
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    _displace_holder_for_recovery(simulation, Point(7.5, 5.5))
    mover = simulation.entities["mover"]
    mover.position = anchor
    simulation.occupancy.move("mover", mover.occupied_cells, frozenset(simulation.entities))
    # The anchors now conflict; neither identity gets guaranteed priority over safety.
    assert simulation.execute(HoldPositionCommand(("mover",))).accepted

    _advance_with_clearance(simulation, 60)
    assert simulation.navigation_field_build_count <= 1

    assert all(entity.state is UnitState.HOLDING for entity in simulation.entities.values())
    assert (
        max(entity.position.distance_to(anchor) for entity in simulation.entities.values()) >= 0.3
    )
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 9.5))).accepted
    _advance_with_clearance(simulation, 100)
    assert simulation.entities["holder"].position.distance_to(anchor) <= 0.05


@pytest.mark.parametrize("target", [Point(8.5, 8.5), Point(11.5, 11.5)], ids=["open", "edge"])
def test_group_route_metadata_ends_at_each_assigned_destination(
    make_map: Callable[[int], GameMap],
    target: Point,
) -> None:
    simulation = Simulation(make_map(3))
    ids = tuple(simulation.entities)
    assert simulation.execute(MoveCommand(ids, target)).accepted
    events = {
        event.subject_id: event
        for event in simulation.events.events
        if event.event_type.value == "path_computed"
    }
    translations = set()
    for key in ids:
        entity = simulation.entities[key]
        assert entity.move_target is not None
        translations.add(
            (
                round(entity.move_target.x - entity.position.x, 9),
                round(entity.move_target.y - entity.position.y, 9),
            )
        )
        assert 0 < entity.move_target.x < simulation.game_map.width
        assert 0 < entity.move_target.y < simulation.game_map.height
        assert entity.path[-1] == entity.move_target
        assert events[key].details["cells"][-1] == list(
            simulation.game_map.cell_for(entity.move_target)
        )

    assert len(translations) == len(ids)


def test_arrival_rounding_cannot_snap_through_a_held_collider(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    holder = simulation.entities["unit_02"]
    mover = simulation.entities["unit_01"]
    assert simulation.execute(HoldPositionCommand((holder.entity_id,))).accepted
    target = Point(holder.position.x, holder.position.y - 0.68 + 0.0000005)
    assert simulation.execute(MoveCommand((mover.entity_id,), target)).accepted
    _advance_with_clearance(simulation, 10)
    assert mover.position.distance_to(holder.position) >= 0.68 - 1e-9
    assert mover.path or mover.position.distance_to(target) <= 1e-9
