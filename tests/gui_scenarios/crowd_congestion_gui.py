r"""Human-inspection GUI scenarios corresponding to the crowd congestion tests.

This filename intentionally does not match pytest's ``test_*.py`` discovery pattern. Normal
``pytest`` runs do not collect this module. Run it explicitly to open each scenario in sequence:

    .\.venv\Scripts\python -m pytest -s tests\gui_scenarios\crowd_congestion_gui.py

Close the AIRTS window to finish the current scenario and continue to the next selected scenario.
Use ``-k`` with part of a function name to open only one scenario.
"""

from __future__ import annotations

from airts.commands import AttackCommand, CreateDefendCommand, CreatePatrolCommand
from airts.geometry import Point, rectangle_region
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.world.map_model import load_map_data


def _crowd_simulation(
    unit_count: int,
    *,
    enemy_position: Point | None = None,
    bridge: bool = False,
) -> tuple[Simulation, tuple[str, ...]]:
    if bridge:
        columns = 20
        start_x = 8.5
        start_y = 30.5
        map_width = 120
        map_height = 80
        terrain_rectangles = [[58, 0, 5, 35, "water"], [58, 44, 5, 36, "water"]]
    else:
        columns = 40
        start_x = 2.5
        start_y = 2.5
        map_width = 80
        map_height = 60
        terrain_rectangles = []
    entity_ids = tuple(f"scout_{index:04d}" for index in range(unit_count))
    entities: list[dict[str, object]] = [
        {
            "id": entity_id,
            "kind": "scout",
            "owner": "player",
            "position": [start_x + index % columns, start_y + index // columns],
        }
        for index, entity_id in enumerate(entity_ids)
    ]
    if enemy_position is not None:
        entities.append(
            {
                "id": "focus_target",
                "kind": "scout",
                "owner": "enemy",
                "position": [enemy_position.x, enemy_position.y],
            }
        )
    simulation = Simulation(
        load_map_data(
            {
                "id": f"crowd_congestion_gui_{unit_count}",
                "name": "Crowd Congestion GUI Scenario",
                "width": map_width,
                "height": map_height,
                "terrain": {
                    "default": "grass",
                    "rectangles": terrain_rectangles,
                },
                "entities": entities,
            }
        ),
        random_seed=73,
    )
    if enemy_position is not None:
        simulation.entities["focus_target"].health = 1_000_000
    return simulation, entity_ids


def _show(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    notice: str,
) -> None:
    app = AirtsApp(simulation)
    app.selected_entities.update(entity_ids)
    app.notice = f"{notice} Close the window to finish."
    app.run()


def test_focus_attackers_hold_at_weapon_range_in_gui() -> None:
    simulation, entity_ids = _crowd_simulation(64, enemy_position=Point(46.5, 5.5))
    assert simulation.execute(AttackCommand(entity_ids, "focus_target")).accepted

    _show(simulation, entity_ids, "64 scouts focus one durable target.")


def test_tiny_defend_area_formation_in_gui() -> None:
    simulation, entity_ids = _crowd_simulation(128)
    target = rectangle_region(Point(60, 28), Point(62, 30))
    assert simulation.execute(CreateDefendCommand(entity_ids, target)).accepted

    _show(simulation, entity_ids, "128 scouts form around a tiny defend area.")


def test_tiny_patrol_area_formation_in_gui() -> None:
    simulation, entity_ids = _crowd_simulation(128)
    target = rectangle_region(Point(60, 28), Point(62, 30))
    assert simulation.execute(CreatePatrolCommand(entity_ids, target)).accepted

    _show(simulation, entity_ids, "128 scouts patrol around a tiny area.")


def test_crowded_waypoint_bridge_turn_in_gui() -> None:
    simulation, entity_ids = _crowd_simulation(8, bridge=True)
    mover = simulation.entities[entity_ids[0]]
    allowed_conflicts = frozenset(entity_ids)
    mover_position = Point(57.5, 28.5)
    simulation.occupancy.move(mover.entity_id, frozenset({(57, 28)}), allowed_conflicts)
    mover.position = mover_position
    for y, entity_id in zip(range(29, 36), entity_ids[1:], strict=True):
        blocker = simulation.entities[entity_id]
        blocker_position = Point(57.5, y + 0.5)
        simulation.occupancy.move(entity_id, frozenset({(57, y)}), allowed_conflicts)
        blocker.position = blocker_position
    mover.path = [
        *(Point(57.5, y + 0.5) for y in range(29, 36)),
        *(Point(x + 0.5, 35.5) for x in range(58, 66)),
    ]
    simulation._skip_crowded_waypoints(mover)

    _show(simulation, entity_ids, "Eight scouts preserve the crowded bridge turn.")


def test_large_tiny_defend_congestion_in_gui() -> None:
    simulation, entity_ids = _crowd_simulation(400)
    target = rectangle_region(Point(65, 28), Point(67, 30))
    assert simulation.execute(CreateDefendCommand(entity_ids, target)).accepted

    _show(simulation, entity_ids, "400 scouts settle with visible clearance.")


def test_large_bridge_queue_in_gui() -> None:
    simulation, entity_ids = _crowd_simulation(400, bridge=True)
    target = rectangle_region(Point(100, 38), Point(102, 40))
    assert simulation.execute(CreateDefendCommand(entity_ids, target)).accepted

    _show(simulation, entity_ids, "All 400 scouts must cross; throughput is not graded.")


def test_thousand_entity_focus_attack_in_gui() -> None:
    simulation, entity_ids = _crowd_simulation(999, enemy_position=Point(70.5, 30.5))
    assert simulation.execute(AttackCommand(entity_ids, "focus_target")).accepted

    _show(simulation, entity_ids, "999 scouts focus one durable target.")
