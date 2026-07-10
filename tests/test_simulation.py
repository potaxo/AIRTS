from __future__ import annotations

from collections.abc import Callable

from airts.commands import CreatePatrolCommand, MoveCommand
from airts.events import EventType
from airts.geometry import Point, PolylineTarget
from airts.map_model import GameMap, load_map_data
from airts.simulation import Simulation


def test_manual_movement_reaches_target_on_fixed_ticks(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))

    result = simulation.execute(MoveCommand(("unit_01",), Point(6.5, 1.5)))
    simulation.advance(10)

    assert result.accepted
    assert simulation.entities["unit_01"].position == Point(6.5, 1.5)
    assert any(
        event.event_type is EventType.MOVEMENT_COMPLETED for event in simulation.events.events
    )


def test_same_initial_state_and_commands_are_deterministic(
    make_map: Callable[[int], GameMap],
) -> None:
    target = PolylineTarget((Point(2, 2), Point(8, 2), Point(8, 8)))
    first = Simulation(make_map(2))
    second = Simulation(make_map(2))

    for simulation in (first, second):
        simulation.execute(CreatePatrolCommand(("unit_01", "unit_02"), target))
        simulation.advance(80)

    assert first.snapshot() == second.snapshot()
    assert [event.to_dict() for event in first.events.events] == [
        event.to_dict() for event in second.events.events
    ]


def test_direct_movement_stops_and_logs_when_it_reaches_an_obstacle() -> None:
    game_map = load_map_data(
        {
            "id": "obstacle",
            "name": "Obstacle",
            "width": 8,
            "height": 6,
            "terrain": {"default": "grass", "rectangles": [[3, 0, 1, 6, "water"]]},
            "entities": [{"id": "unit", "kind": "scout", "position": [1.5, 2.5]}],
        }
    )
    simulation = Simulation(game_map)

    result = simulation.execute(MoveCommand(("unit",), Point(5.5, 2.5)))
    simulation.advance(20)

    assert result.accepted
    assert simulation.entities["unit"].position.x < 3
    assert any(event.event_type is EventType.MOVEMENT_FAILED for event in simulation.events.events)


def test_invalid_command_does_not_mutate_world_state(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    before = simulation.snapshot()

    result = simulation.execute(MoveCommand(("missing",), Point(4, 4)))

    assert not result.accepted
    assert result.reason == "UNKNOWN_ENTITY:missing"
    assert simulation.snapshot() == before
    assert simulation.events.events[-1].event_type is EventType.COMMAND_REJECTED
