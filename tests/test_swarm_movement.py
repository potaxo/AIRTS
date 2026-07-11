from __future__ import annotations

from itertools import combinations

from airts.automations import AutomationStatus
from airts.commands import CreatePatrolCommand, MoveCommand
from airts.events import EventType
from airts.geometry import Point, PolylineTarget, rectangle_region
from airts.map_model import EntityKind, load_map_data
from airts.movement import collision_radius, steering_candidates
from airts.simulation import Simulation


def _swarm_simulation(positions: dict[str, tuple[float, float]]) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "swarm",
                "name": "Swarm Movement",
                "width": 20,
                "height": 20,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": list(position),
                    }
                    for entity_id, position in sorted(positions.items())
                ],
            }
        ),
        random_seed=23,
    )


def test_head_on_agents_choose_opposite_world_space_passing_sides() -> None:
    eastbound = steering_candidates(Point(4, 5), Point(10, 5), 0.5, (Point(5, 5),))[0]
    westbound = steering_candidates(Point(5, 5), Point(0, 5), 0.5, (Point(4, 5),))[0]

    assert eastbound.y > 5
    assert westbound.y < 5


def test_units_traveling_in_opposite_directions_pass_without_deadlock() -> None:
    simulation = _swarm_simulation({"east": (2.5, 8.5), "west": (12.5, 8.5)})
    assert simulation.execute(MoveCommand(("east",), Point(14.5, 8.5))).accepted
    assert simulation.execute(MoveCommand(("west",), Point(0.5, 8.5))).accepted

    minimum_separation = float("inf")
    for _ in range(100):
        simulation.advance()
        minimum_separation = min(
            minimum_separation,
            simulation.entities["east"].position.distance_to(simulation.entities["west"].position),
        )

    assert not simulation.entities["east"].path
    assert not simulation.entities["west"].path
    assert simulation.entities["east"].position == Point(14.5, 8.5)
    assert simulation.entities["west"].position == Point(0.5, 8.5)
    assert minimum_separation >= collision_radius(EntityKind.SCOUT) * 2 - 1e-6


def test_perpendicular_groups_cross_and_all_reach_their_destinations() -> None:
    simulation = _swarm_simulation(
        {
            "east_1": (2.5, 9.5),
            "east_2": (2.5, 11.5),
            "north_1": (9.5, 16.5),
            "north_2": (11.5, 16.5),
        }
    )
    commands = (
        MoveCommand(("east_1",), Point(17.5, 9.5)),
        MoveCommand(("east_2",), Point(17.5, 11.5)),
        MoveCommand(("north_1",), Point(9.5, 2.5)),
        MoveCommand(("north_2",), Point(11.5, 2.5)),
    )
    for command in commands:
        assert simulation.execute(command).accepted

    minimum_separation = float("inf")
    for _ in range(160):
        simulation.advance()
        minimum_separation = min(
            minimum_separation,
            *(
                first.position.distance_to(second.position)
                for first, second in combinations(simulation.entities.values(), 2)
            ),
        )

    assert all(not entity.path for entity in simulation.entities.values())
    assert [simulation.entities[command.entity_ids[0]].position for command in commands] == [
        command.target for command in commands
    ]
    assert minimum_separation >= collision_radius(EntityKind.SCOUT) * 2 - 1e-6


def test_dense_small_area_patrol_keeps_separation_and_makes_progress() -> None:
    positions = {
        "unit_1": (2.5, 2.5),
        "unit_2": (4.5, 2.5),
        "unit_3": (6.5, 2.5),
        "unit_4": (2.5, 4.5),
        "unit_5": (4.5, 4.5),
        "unit_6": (6.5, 4.5),
    }
    simulation = _swarm_simulation(positions)
    result = simulation.execute(
        CreatePatrolCommand(tuple(sorted(positions)), rectangle_region(Point(8, 8), Point(12, 12)))
    )
    assert result.accepted

    minimum_separation = float("inf")
    for _ in range(240):
        simulation.advance()
        minimum_separation = min(
            minimum_separation,
            *(
                first.position.distance_to(second.position)
                for first, second in combinations(simulation.entities.values(), 2)
            ),
        )

    automation = simulation.automations[result.automation_id or ""]
    assert automation.status is AutomationStatus.ACTIVE
    assert all(
        entity.position != Point(*positions[entity_id])
        for entity_id, entity in simulation.entities.items()
    )
    assert minimum_separation >= collision_radius(EntityKind.SCOUT) * 2 - 1e-6
    blocked = simulation.events.query(event_types=frozenset({EventType.MOVEMENT_BLOCKED}))
    assert len(blocked) < 12


def test_line_patrol_group_flows_from_first_vertex_to_last_without_head_on_jam() -> None:
    positions = {f"unit_{index}": (3.5 + index * 1.2, 1.5) for index in range(6)}
    simulation = _swarm_simulation(positions)
    result = simulation.execute(
        CreatePatrolCommand(
            tuple(sorted(positions)),
            PolylineTarget((Point(10.5, 2.5), Point(10.5, 17.5))),
        )
    )
    maximum_y = {entity_id: position[1] for entity_id, position in positions.items()}

    for _ in range(240):
        simulation.advance()
        for entity_id in maximum_y:
            maximum_y[entity_id] = max(
                maximum_y[entity_id], simulation.entities[entity_id].position.y
            )

    assert result.accepted
    assert all(value >= 15.0 for value in maximum_y.values())
    assert simulation.automations[result.automation_id or ""].status is AutomationStatus.ACTIVE


def test_swarm_movement_is_identical_for_the_same_seed_and_commands() -> None:
    positions = {f"unit_{index}": (2.5 + index * 2, 3.5) for index in range(5)}
    first = _swarm_simulation(positions)
    second = _swarm_simulation(positions)
    commands = tuple(
        MoveCommand((entity_id,), Point(15.5 - index, 14.5))
        for index, entity_id in enumerate(sorted(positions))
    )
    for simulation in (first, second):
        for command in commands:
            assert simulation.execute(command).accepted
        simulation.advance(180)

    assert first.snapshot() == second.snapshot()
    assert [event.to_dict() for event in first.events.events] == [
        event.to_dict() for event in second.events.events
    ]


def test_units_finish_the_last_waypoint_without_stopping_midway_or_shaking() -> None:
    simulation = _swarm_simulation({"unit": (2.5, 2.5)})
    destination = Point(15.5, 2.5)
    assert simulation.execute(MoveCommand(("unit",), destination)).accepted

    simulation.advance()
    assert simulation.entities["unit"].path
    assert simulation.entities["unit"].position != destination

    simulation.advance(40)
    settled = simulation.entities["unit"].position
    assert settled == destination
    assert not simulation.entities["unit"].path

    simulation.advance(30)
    assert simulation.entities["unit"].position == settled


def test_slow_unit_making_forward_progress_does_not_time_out_midway() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "slow_progress",
                "name": "Slow Progress",
                "width": 20,
                "height": 5,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "heavy",
                        "kind": "heavy_tank",
                        "owner": "player",
                        "position": [1.5, 2.5],
                    }
                ],
            }
        )
    )
    destination = Point(18.5, 2.5)
    assert simulation.execute(MoveCommand(("heavy",), destination)).accepted

    simulation.advance(Simulation.NO_PROGRESS_YIELD_TICKS + 10)

    heavy = simulation.entities["heavy"]
    assert heavy.path
    assert not heavy.congestion_stopped
    assert not simulation.events.query(
        event_types=frozenset({EventType.MOVEMENT_YIELDED}), subject_id="heavy"
    )

    simulation.advance(40)
    assert heavy.position == destination
    assert not heavy.path


def test_separate_move_commands_reserve_distinct_destinations_and_settle() -> None:
    positions = {
        "unit_1": (2.5, 3.5),
        "unit_2": (2.5, 5.5),
        "unit_3": (2.5, 7.5),
        "unit_4": (2.5, 9.5),
        "unit_5": (2.5, 11.5),
        "unit_6": (2.5, 13.5),
    }
    simulation = _swarm_simulation(positions)
    target = Point(15.5, 8.5)

    for entity_id in sorted(positions):
        assert simulation.execute(MoveCommand((entity_id,), target)).accepted

    reservations = {simulation.entities[entity_id].move_target for entity_id in sorted(positions)}
    assert len(reservations) == len(positions)

    simulation.advance(180)
    settled = {entity_id: entity.position for entity_id, entity in simulation.entities.items()}
    assert all(not entity.path for entity in simulation.entities.values())
    assert len(set(settled.values())) == len(positions)

    simulation.advance(40)
    assert {
        entity_id: entity.position for entity_id, entity in simulation.entities.items()
    } == settled


def test_convoy_fills_far_formation_slots_before_near_units_block_the_approach() -> None:
    positions = {f"unit_{index}": (2.5 + index % 3, 5.5 + (index // 3) * 2) for index in range(9)}
    simulation = _swarm_simulation(positions)
    entity_ids = tuple(sorted(positions))
    assert simulation.execute(MoveCommand(entity_ids, Point(15.5, 8.5))).accepted

    front_ids = sorted(entity_ids, key=lambda item: positions[item][0], reverse=True)[:3]
    rear_ids = sorted(entity_ids, key=lambda item: positions[item][0])[:3]
    assert min(simulation.entities[item].move_target.x for item in front_ids) >= max(
        simulation.entities[item].move_target.x for item in rear_ids
    )

    simulation.advance(200)
    settled = tuple(entity.position for entity in simulation.entities.values())
    assert all(not entity.path for entity in simulation.entities.values())
    assert (
        min(first.distance_to(second) for first, second in combinations(settled, 2))
        >= collision_radius(EntityKind.SCOUT) * 2 - 1e-6
    )

    simulation.advance(40)
    assert tuple(entity.position for entity in simulation.entities.values()) == settled
