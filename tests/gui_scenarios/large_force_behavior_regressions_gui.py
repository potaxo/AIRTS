r"""Human-inspection scenarios for the large-force behavior regressions.

This file is excluded from normal pytest discovery. Run one scenario explicitly with:

    .\.venv\Scripts\python -m pytest -s tests\gui_scenarios\large_force_behavior_regressions_gui.py -k identity

Replace ``identity`` with ``heavy``, ``defend``, ``factories``, or ``held`` for the other regressions.
Close the AIRTS window to finish a scenario.
"""

from __future__ import annotations

from airts.commands import (
    CreateDefendCommand,
    CreateProductionCommand,
    HoldPositionCommand,
    MoveCommand,
)
from airts.geometry import Point, rectangle_region
from airts.map_model import EntityKind, load_map_data
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation


def _river_terrain() -> dict[str, object]:
    return {
        "default": "grass",
        "rectangles": [
            [30, 0, 4, 30, "water"],
            [30, 34, 4, 30, "water"],
            [30, 30, 4, 4, "bridge"],
        ],
    }


def _show(simulation: Simulation, selected: tuple[str, ...], notice: str) -> None:
    app = AirtsApp(simulation)
    app.selected_entities.update(selected)
    app.notice = f"{notice} Close the window to finish."
    app.run()


def test_identity_swaps_and_stop_go_motion_in_gui() -> None:
    entity_ids = tuple(f"heavy_{index:03d}" for index in range(140))
    simulation = Simulation(
        load_map_data(
            {
                "id": "identity_swap_gui",
                "name": "Identity swap and stop-go motion",
                "width": 80,
                "height": 40,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "heavy_tank",
                        "owner": "player",
                        "position": [5.5 + index % 14, 12.5 + index // 14],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=101,
    )
    assert simulation.execute(MoveCommand(entity_ids, Point(65.5, 20.5))).accepted

    _show(
        simulation,
        entity_ids[::2],
        "Alternating selected tanks expose identity swaps; motion should not pause then jump.",
    )


def test_scouts_flow_around_stationary_enemy_heavy_in_gui() -> None:
    scout_ids = tuple(f"scout_{index:04d}" for index in range(152))
    simulation = Simulation(
        load_map_data(
            {
                "id": "stationary_heavy_anchor_gui",
                "name": "Stationary enemy heavy anchor",
                "width": 44,
                "height": 44,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [8.5 + index % 19, 26.5 + index // 19],
                        }
                        for index, entity_id in enumerate(scout_ids)
                    ),
                    {
                        "id": "stationary_heavy",
                        "kind": "heavy_tank",
                        "owner": "enemy",
                        "position": [17.5, 20.5],
                    },
                ],
            }
        ),
        random_seed=91,
    )
    simulation.entities["stationary_heavy"].health = 1_000_000
    assert simulation.execute(MoveCommand(scout_ids, Point(17.5, 4.5))).accepted

    _show(
        simulation,
        scout_ids,
        "The selected scouts should flow north while the single enemy heavy remains exact.",
    )


def test_overlapping_manual_defend_orders_in_gui() -> None:
    first_ids = tuple(f"first_{index:03d}" for index in range(80))
    second_ids = tuple(f"second_{index:03d}" for index in range(80))
    simulation = Simulation(
        load_map_data(
            {
                "id": "shared_defend_gui",
                "name": "Overlapping manual defend orders",
                "width": 64,
                "height": 64,
                "terrain": _river_terrain(),
                "entities": [
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [5.5 + index % 10, 10.5 + index // 10],
                        }
                        for index, entity_id in enumerate(first_ids)
                    ),
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [42.5 + index % 10, 22.5 + index // 10],
                        }
                        for index, entity_id in enumerate(second_ids)
                    ),
                ],
            }
        ),
        random_seed=105,
    )
    target = rectangle_region(Point(3, 8), Point(23, 27))
    assert simulation.execute(CreateDefendCommand(first_ids, target)).accepted
    assert simulation.execute(CreateDefendCommand(second_ids, target)).accepted

    _show(
        simulation,
        second_ids,
        "Two defend orders target one area; the second group must not stall on duplicate slots.",
    )


def test_multiple_factory_defenders_in_gui() -> None:
    factory_ids = tuple(f"factory_{index}" for index in range(4))
    simulation = Simulation(
        load_map_data(
            {
                "id": "factory_defense_gui",
                "name": "Multiple factory defense",
                "width": 64,
                "height": 64,
                "terrain": _river_terrain(),
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "factory",
                        "owner": "player",
                        "position": [5 + index * 6, 42],
                    }
                    for index, entity_id in enumerate(factory_ids)
                ],
            }
        ),
        random_seed=107,
    )
    simulation.resources["player"] = 1_000_000
    target = rectangle_region(Point(48, 22), Point(52, 28))
    for factory_id in factory_ids:
        assert simulation.execute(
            CreateProductionCommand(
                factory_id,
                EntityKind.SCOUT,
                33,
                defend_target=target,
            )
        ).accepted

    _show(
        simulation,
        factory_ids,
        "Four factories reinforce one area; produced units must form one group, not route lines.",
    )


def test_movers_bypass_held_group_in_gui() -> None:
    mover_ids = tuple(f"mover_{index:03d}" for index in range(140))
    holder_ids = tuple(f"holder_{index:03d}" for index in range(60))
    simulation = Simulation(
        load_map_data(
            {
                "id": "held_bypass_gui",
                "name": "Held group bypass",
                "width": 72,
                "height": 60,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [7.5 + index % 14, 24.5 + index // 14],
                        }
                        for index, entity_id in enumerate(mover_ids)
                    ),
                    *(
                        {
                            "id": entity_id,
                            "kind": "heavy_tank",
                            "owner": "player",
                            "position": [34.5 + index % 2, 15.5 + index // 2],
                        }
                        for index, entity_id in enumerate(holder_ids)
                    ),
                ],
            }
        ),
        random_seed=109,
    )
    assert simulation.execute(HoldPositionCommand(holder_ids)).accepted
    assert simulation.execute(MoveCommand(mover_ids, Point(60.5, 30.5))).accepted

    _show(
        simulation,
        mover_ids,
        "Scouts must route around the fixed tank wall while every held tank remains anchored.",
    )
