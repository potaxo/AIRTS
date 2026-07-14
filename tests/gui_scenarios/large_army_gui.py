r"""Live-window counterparts for visual large-army simulation workloads.

Normal pytest discovery ignores this file because its name does not start with ``test_``. Run it
explicitly, optionally with ``-k`` to select one scenario:

    .\.venv\Scripts\python -m pytest -s tests\gui_scenarios\large_army_gui.py

Close a window to finish that scenario.
"""

from __future__ import annotations

from airts.commands import (
    AttackCommand,
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
)
from airts.geometry import Point, PolygonRegion, PolylineTarget, rectangle_region
from airts.map_model import load_map_data
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation


def _show(
    simulation: Simulation,
    selected_ids: tuple[str, ...],
    notice: str,
    *,
    inspected_entity_id: str | None = None,
) -> None:
    app = AirtsApp(simulation)
    app.selected_entities.update(selected_ids)
    app.inspected_entity_id = inspected_entity_id
    app.notice = f"{notice} Close the window to finish."
    app.run()


def _large_simulation(
    unit_count: int,
    *,
    with_repair_hub: bool = False,
) -> tuple[Simulation, tuple[str, ...]]:
    entity_ids = tuple(f"unit_{index:04d}" for index in range(unit_count))
    entities: list[dict[str, object]] = [
        {
            "id": entity_id,
            "kind": "light_tank",
            "owner": "player",
            "position": [index % 50 + 0.5, index // 50 + 0.5],
        }
        for index, entity_id in enumerate(entity_ids)
    ]
    if with_repair_hub:
        entities.append(
            {
                "id": "repair",
                "kind": "repair_hub",
                "owner": "player",
                "position": [60, 40],
            }
        )
    simulation = Simulation(
        load_map_data(
            {
                "id": f"large_army_gui_{unit_count}",
                "name": "Large Army GUI",
                "width": 80,
                "height": 60,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": entities,
            }
        )
    )
    return simulation, entity_ids


def _assembled_simulation(
    unit_count: int,
) -> tuple[Simulation, tuple[str, ...], PolygonRegion]:
    width = 50
    height = 50
    center = Point(width / 2, height / 2)
    cells = sorted(
        ((x, y) for y in range(height) for x in range(width)),
        key=lambda cell: (
            (cell[0] + 0.5 - center.x) ** 2 + (cell[1] + 0.5 - center.y) ** 2,
            cell[1],
            cell[0],
        ),
    )[:unit_count]
    entity_ids = tuple(f"unit_{index:04d}" for index in range(unit_count))
    simulation = Simulation(
        load_map_data(
            {
                "id": f"assembled_gui_{unit_count}",
                "name": "Large Gathering Point GUI",
                "width": width,
                "height": height,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [cell[0] + 0.5, cell[1] + 0.5],
                    }
                    for entity_id, cell in zip(entity_ids, cells, strict=True)
                ],
            }
        )
    )
    return simulation, entity_ids, rectangle_region(Point(24, 24), Point(26, 26))


def _choke_simulation(unit_count: int = 500) -> tuple[Simulation, tuple[str, ...]]:
    columns = 25
    entity_ids = tuple(f"unit_{index:04d}" for index in range(unit_count))
    simulation = Simulation(
        load_map_data(
            {
                "id": f"mass_choke_gui_{unit_count}",
                "name": "Mass Choke GUI",
                "width": 80,
                "height": 40,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [40, 0, 10, 19, "water"],
                        [40, 22, 10, 18, "water"],
                    ],
                },
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [15.5 + index % columns, 10.5 + index // columns],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=41,
    )
    return simulation, entity_ids


def _large_enemy_building_simulation() -> tuple[Simulation, tuple[str, ...]]:
    entity_ids = tuple(f"unit_{index:04d}" for index in range(999))
    simulation = Simulation(
        load_map_data(
            {
                "id": "large_enemy_building_gui",
                "name": "Large Enemy Building Focus GUI",
                "width": 80,
                "height": 60,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [index % 50 + 0.5, index // 50 + 0.5],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ]
                + [
                    {
                        "id": "enemy_factory",
                        "kind": "factory",
                        "owner": "enemy",
                        "position": [70, 45],
                    }
                ],
            }
        )
    )
    simulation.entities["enemy_factory"].health = 1_000_000
    return simulation, entity_ids


def _head_on_armies(
    per_group: int = 150,
) -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    eastbound = tuple(f"east_{index:03d}" for index in range(per_group))
    westbound = tuple(f"west_{index:03d}" for index in range(per_group))
    entities = [
        {
            "id": entity_id,
            "kind": "light_tank",
            "owner": "player",
            "position": [20.5 + index % 10, 5.5 + index // 10],
        }
        for index, entity_id in enumerate(eastbound)
    ] + [
        {
            "id": entity_id,
            "kind": "light_tank",
            "owner": "player",
            "position": [50.5 + index % 10, 5.5 + index // 10],
        }
        for index, entity_id in enumerate(westbound)
    ]
    simulation = Simulation(
        load_map_data(
            {
                "id": "large_head_on_armies_gui",
                "name": "Large Head-On Armies GUI",
                "width": 80,
                "height": 40,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": entities,
            }
        )
    )
    return simulation, eastbound, westbound


def _large_line_simulation(
    unit_count: int = 500,
) -> tuple[Simulation, tuple[str, ...], PolylineTarget]:
    entity_ids = tuple(f"line_{index:04d}" for index in range(unit_count))
    simulation = Simulation(
        load_map_data(
            {
                "id": "large_line_gui",
                "name": "Large Line Automation GUI",
                "width": 520,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [index + 10.5, 3.5],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        )
    )
    return (
        simulation,
        entity_ids,
        PolylineTarget((Point(10.5, 10.5), Point(509.5, 10.5))),
    )


def _delayed_mover_simulation() -> Simulation:
    blockers = [
        {
            "id": f"blocker_{index:03d}",
            "kind": "heavy_tank",
            "owner": "player",
            "position": [20.5 + index % 25, 5.5 + index // 25],
        }
        for index in range(499)
    ]
    return Simulation(
        load_map_data(
            {
                "id": "large_stalled_repath_gui",
                "name": "Large Stalled Repath GUI",
                "width": 60,
                "height": 32,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "mover",
                        "kind": "scout",
                        "owner": "player",
                        "position": [2.5, 15.5],
                    },
                    *blockers,
                ],
            }
        )
    )


def test_thousand_unit_repair_selection_in_gui() -> None:
    simulation, entity_ids = _large_simulation(1_000, with_repair_hub=True)
    for entity_id in entity_ids[::100]:
        simulation.entities[entity_id].health = 17
    assert simulation.execute(CreateRepairAndReturnCommand(entity_ids)).accepted
    _show(simulation, entity_ids, "Only ten damaged tanks travel for repair.")


def test_thousand_unit_patrol_in_gui() -> None:
    simulation, entity_ids = _large_simulation(1_000)
    target = rectangle_region(Point(60, 5), Point(75, 25))
    assert simulation.execute(CreatePatrolCommand(entity_ids, target)).accepted
    _show(simulation, entity_ids, "1,000 tanks patrol through shared bounded routes.")


def test_thousand_unit_move_cluster_in_gui() -> None:
    simulation, entity_ids = _large_simulation(1_000)
    assert simulation.execute(MoveCommand(entity_ids, Point(70.5, 50.5))).accepted
    _show(simulation, entity_ids, "1,000 tanks move into distinct clustered destinations.")


def test_thousand_unit_gathering_point_in_gui() -> None:
    simulation, entity_ids, target = _assembled_simulation(1_000)
    assert simulation.execute(
        CreateDefendCommand(entity_ids, target, gathering_point=True)
    ).accepted
    _show(simulation, entity_ids, "1,000 tanks settle into an expanded gathering point.")


def test_thousand_unit_gathering_radius_contraction_in_gui() -> None:
    simulation, entity_ids, target = _assembled_simulation(1_000)
    assert simulation.execute(
        CreateDefendCommand(entity_ids, target, gathering_point=True)
    ).accepted
    assert simulation.execute(
        CreateDefendCommand(
            entity_ids[::2],
            rectangle_region(Point(4, 4), Point(6, 6)),
            gathering_point=True,
        )
    ).accepted
    _show(simulation, entity_ids, "Half the army is reassigned and both radii contract.")


def test_five_hundred_unit_line_defense_in_gui() -> None:
    simulation, entity_ids, target = _large_line_simulation()
    assert simulation.execute(CreateDefendCommand(entity_ids, target)).accepted
    _show(simulation, entity_ids, "500 tanks distribute evenly across a long defense line.")


def test_five_hundred_unit_line_patrol_in_gui() -> None:
    simulation, entity_ids, target = _large_line_simulation()
    assert simulation.execute(CreatePatrolCommand(entity_ids, target)).accepted
    _show(simulation, entity_ids, "500 tanks patrol across a long shared line.")


def test_five_hundred_unit_repair_travel_in_gui() -> None:
    simulation, entity_ids = _large_simulation(500, with_repair_hub=True)
    for entity_id in entity_ids:
        simulation.entities[entity_id].health = 17
    assert simulation.execute(CreateRepairAndReturnCommand(entity_ids)).accepted
    _show(simulation, entity_ids, "500 damaged tanks share bounded repair routing.")


def test_delayed_unit_behind_large_blocker_group_in_gui() -> None:
    simulation = _delayed_mover_simulation()
    assert simulation.execute(MoveCommand(("mover",), Point(55.5, 15.5))).accepted
    _show(
        simulation,
        ("mover",),
        "One scout routes through 499 heavy-tank blockers without a repath storm.",
    )


def test_thousand_unit_enemy_focus_attack_in_gui() -> None:
    simulation, entity_ids = _large_simulation(1_000)
    enemy_id = entity_ids[-1]
    simulation.entities[enemy_id].owner_id = "enemy"
    attackers = entity_ids[:-1]
    simulation.entities[enemy_id].health = 1_000_000
    assert simulation.execute(AttackCommand(attackers, enemy_id)).accepted
    _show(
        simulation,
        attackers,
        "999 tanks focus one durable enemy with a generous visible hit area.",
        inspected_entity_id=enemy_id,
    )


def test_five_hundred_unit_choke_in_gui() -> None:
    simulation, entity_ids = _choke_simulation()
    assert simulation.execute(MoveCommand(entity_ids, Point(70.5, 20.5))).accepted
    _show(simulation, entity_ids, "500 tanks maintain flow through a narrow choke.")


def test_deterministic_hundred_unit_choke_in_gui() -> None:
    simulation, entity_ids = _choke_simulation(100)
    assert simulation.execute(MoveCommand(entity_ids, Point(70.5, 20.5))).accepted
    _show(simulation, entity_ids, "Seeded 100-tank choke run for deterministic inspection.")


def test_thousand_unit_enemy_factory_focus_attack_in_gui() -> None:
    simulation, entity_ids = _large_enemy_building_simulation()
    assert simulation.execute(AttackCommand(entity_ids, "enemy_factory")).accepted
    _show(
        simulation,
        entity_ids,
        "999 tanks focus the durable enemy factory footprint.",
        inspected_entity_id="enemy_factory",
    )


def test_two_large_head_on_armies_in_gui() -> None:
    simulation, eastbound, westbound = _head_on_armies()
    assert simulation.execute(MoveCommand(eastbound, Point(70.5, 20.5))).accepted
    assert simulation.execute(MoveCommand(westbound, Point(9.5, 20.5))).accepted
    _show(
        simulation,
        eastbound + westbound,
        "Two 150-tank armies pass head-on without a global freeze.",
    )
