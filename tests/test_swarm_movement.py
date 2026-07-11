from __future__ import annotations

from itertools import combinations

from airts.automations import AutomationStatus
from airts.commands import CreatePatrolCommand, MoveCommand
from airts.events import EventType
from airts.geometry import Point, rectangle_region
from airts.map_model import load_map_data
from airts.movement import steering_candidates
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
    assert minimum_separation >= 0.62


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
    assert minimum_separation >= 0.62


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
    assert minimum_separation >= 0.62
    blocked = simulation.events.query(event_types=frozenset({EventType.MOVEMENT_BLOCKED}))
    assert len(blocked) < 12


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
