"""Cold search admission preserves FIFO intent, cancellation and current-save restoration."""

import pytest

from airts.adapters.persistence import capture_save, load_simulation_data
from airts.contracts.commands import StopCommand
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems import unit_orders
from airts.world.entities import OrderKind, UnitOrder
from airts.world.map import load_map_data


def test_deferred_queued_moves_resume_without_losing_fifo_or_restored_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(unit_orders, "QUEUED_SEARCH_CELL_BUDGET", 1)
    ids = tuple(f"unit_{index}" for index in range(8))
    simulation = Simulation(
        load_map_data(
            {
                "id": "queued_cold_searches",
                "name": "Queued cold searches",
                "width": 80,
                "height": 40,
                "terrain": {"default": "forest"},
                "entities": [
                    {"id": key, "kind": "scout", "position": [2.5, 5.5 + index * 2]}
                    for index, key in enumerate(ids)
                ],
            }
        )
    )
    # Combat support leaves this exact kind of pending leg: distinct final slots
    # without a group anchor. Keep a second leg to verify the FIFO tail survives.
    first = {key: Point(70.5, 5.5 + index * 2) for index, key in enumerate(ids)}
    tail = UnitOrder(OrderKind.MOVE, Point(75.5, 30.5))
    for key in ids:
        simulation.entities[key].order_queue = [UnitOrder(OrderKind.MOVE, first[key]), tail]
    simulation.advance()
    assert simulation.navigation_field_build_count == 1
    assert sum(bool(simulation.entities[key].path) for key in ids) == 1
    assert simulation.automation_route_count == 1
    pending = tuple(key for key in ids if not simulation.entities[key].path)
    assert all(len(simulation.entities[key].order_queue) == 2 for key in pending)
    saved = capture_save(simulation)
    loaded = load_simulation_data(
        {
            "schema": saved.schema,
            "map": saved.game_map,
            "state": saved.state | {"events": [event.to_dict() for event in saved.events]},
        }
    )
    cancelled = pending[-1]
    for world in (simulation, loaded):
        assert world.execute(StopCommand((cancelled,))).accepted
        world.advance(len(ids))
        for key in ids:
            entity = world.entities[key]
            if key == cancelled:
                assert not entity.path and not entity.order_queue
            else:
                assert entity.path and entity.move_target == first[key]
                assert entity.order_queue == [tail]
        assert not world.events.query(event_types=frozenset({EventType.PATHFINDING_FAILED}))
