"""Physical guarantees of the replacement controller."""

from collections.abc import Callable

import pytest

from airts.contracts.commands import HoldPositionCommand, MoveCommand
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.world.map import GameMap, load_map_data


@pytest.mark.parametrize("vertical", [False, True], ids=["horizontal", "vertical"])
def test_opposing_steps_cannot_tunnel_or_exceed_speed(vertical: bool) -> None:
    def coordinates(x: float, y: float) -> list[float]:
        return [y, x] if vertical else [x, y]

    simulation = Simulation(
        load_map_data(
            {
                "id": "crossing",
                "name": "Crossing",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "a", "kind": "scout", "position": coordinates(4.5, 5.5)},
                    {"id": "b", "kind": "scout", "position": coordinates(5.8, 5.5)},
                ],
            }
        )
    )
    assert simulation.execute(MoveCommand(("a",), Point(*coordinates(9.5, 5.5)))).accepted
    assert simulation.execute(MoveCommand(("b",), Point(*coordinates(1.5, 5.5)))).accepted
    for _ in range(40):
        before = {key: e.position for key, e in simulation.entities.items()}
        simulation.advance()
        a, b = simulation.entities["a"], simulation.entities["b"]
        dx, dy = before["a"].x - before["b"].x, before["a"].y - before["b"].y
        vx = a.position.x - before["a"].x - b.position.x + before["b"].x
        vy = a.position.y - before["a"].y - b.position.y + before["b"].y
        time = min(1.0, max(0.0, -(dx * vx + dy * vy) / (vx * vx + vy * vy))) if vx or vy else 0.0
        assert (dx + vx * time) ** 2 + (dy + vy * time) ** 2 >= 0.6**2 - 1e-8
        assert all(
            e.position.distance_to(before[key]) <= e.speed * 0.1 + 1e-8
            for key, e in simulation.entities.items()
        )


def test_held_unit_stays_fixed_while_another_routes_past(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    held = simulation.entities["unit_02"].position
    assert simulation.execute(HoldPositionCommand(("unit_02",))).accepted
    assert simulation.execute(MoveCommand(("unit_01",), Point(1.5, 7.5))).accepted
    for _ in range(60):
        simulation.advance()
        assert simulation.entities["unit_02"].position == held
        assert simulation.entities["unit_01"].position.distance_to(held) >= 0.68 - 1e-8
    assert simulation.entities["unit_01"].position == Point(1.5, 7.5)


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

    assert len(translations) == 1


def test_arrival_rounding_cannot_snap_through_a_held_collider(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    holder = simulation.entities["unit_02"]
    mover = simulation.entities["unit_01"]
    assert simulation.execute(HoldPositionCommand((holder.entity_id,))).accepted
    target = Point(holder.position.x, holder.position.y - 0.68 + 0.0000005)
    assert simulation.execute(MoveCommand((mover.entity_id,), target)).accepted
    for _ in range(10):
        simulation.advance()
        assert mover.position.distance_to(holder.position) >= 0.68 - 1e-9
    assert mover.path
