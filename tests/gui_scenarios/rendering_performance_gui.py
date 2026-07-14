r"""Live-window counterparts for visual rendering performance workloads.

Normal pytest discovery ignores this file because its name does not start with ``test_``. Run all
windows explicitly with:

    .\.venv\Scripts\python -m pytest -s tests\gui_scenarios\rendering_performance_gui.py

Use ``-k`` to select one scenario. Close a window to finish that scenario.
"""

from __future__ import annotations

from airts.commands import CreateDefendCommand, CreatePatrolCommand, MoveCommand
from airts.geometry import Point, PolylineTarget, rectangle_region
from airts.map_model import EntityKind, load_map_data
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation

DISPLAY_SIZE = (3840, 2160)
UNIT_COUNT = 1_000
GROUP_SIZE = UNIT_COUNT // 2
UNITS_PER_OWNER = 500


def _show(
    simulation: Simulation,
    selected_ids: tuple[str, ...],
    notice: str,
    *,
    display_size: tuple[int, int] | None = None,
    inspected_entity_id: str | None = None,
    active_target: PolylineTarget | None = None,
) -> None:
    app = AirtsApp(simulation)
    if display_size is not None:
        app.resize_layout(display_size)
    app.selected_entities.update(selected_ids)
    app.inspected_entity_id = inspected_entity_id
    app.active_target = active_target
    app.notice = f"{notice} Close the window to finish."
    app.run()


def _thousand_tank_simulation() -> tuple[Simulation, tuple[str, ...]]:
    entity_ids = tuple(f"unit_{index:04d}" for index in range(UNIT_COUNT))
    simulation = Simulation(
        load_map_data(
            {
                "id": "thousand_unit_gui",
                "name": "Thousand Unit GUI",
                "width": 80,
                "height": 60,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [0, 23, 80, 4, "road"],
                        [24, 30, 14, 14, "forest"],
                        [48, 42, 20, 4, "road"],
                    ],
                },
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [index % 50 + 0.5, index // 50 + 0.5],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=73,
    )
    return simulation, entity_ids


def _head_on_scout_simulation() -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    eastbound = tuple(f"east_{index:04d}" for index in range(GROUP_SIZE))
    westbound = tuple(f"west_{index:04d}" for index in range(GROUP_SIZE))
    simulation = Simulation(
        load_map_data(
            {
                "id": "native_four_k_opengl_gui",
                "name": "Native 4K OpenGL Thousand Scouts GUI",
                "width": 80,
                "height": 60,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [0, 14, 80, 4, "road"],
                        [0, 42, 80, 4, "road"],
                        [31, 20, 3, 20, "forest"],
                    ],
                },
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": [5.5 + index % 25, 20.5 + index // 25],
                    }
                    for index, entity_id in enumerate(eastbound)
                ]
                + [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": [35.5 + index % 25, 20.5 + index // 25],
                    }
                    for index, entity_id in enumerate(westbound)
                ]
                + [
                    {
                        "id": "factory",
                        "kind": "factory",
                        "owner": "player",
                        "position": [68, 4],
                    },
                    {
                        "id": "repair_hub",
                        "kind": "repair_hub",
                        "owner": "player",
                        "position": [68, 12],
                    },
                    {
                        "id": "command_center",
                        "kind": "command_center",
                        "owner": "player",
                        "position": [68, 48],
                    },
                    {
                        "id": "resource_generator",
                        "kind": "resource_generator",
                        "owner": "player",
                        "position": [4, 48],
                    },
                ],
            }
        ),
        random_seed=97,
    )
    return simulation, eastbound, westbound


def _unit_kind(index: int) -> str:
    remainder = index % 10
    if remainder < 7:
        return EntityKind.SCOUT.value
    if remainder < 9:
        return EntityKind.LIGHT_TANK.value
    return EntityKind.HEAVY_TANK.value


def _battle_simulation() -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    player_ids = tuple(f"player_{index:04d}" for index in range(UNITS_PER_OWNER))
    enemy_ids = tuple(f"enemy_{index:04d}" for index in range(UNITS_PER_OWNER))
    units = [
        {
            "id": entity_id,
            "kind": _unit_kind(index),
            "owner": "player",
            "position": [8.5 + index % 25, 20.5 + index // 25],
        }
        for index, entity_id in enumerate(player_ids)
    ] + [
        {
            "id": entity_id,
            "kind": _unit_kind(index),
            "owner": "enemy",
            "position": [47.5 + index % 25, 20.5 + index // 25],
        }
        for index, entity_id in enumerate(enemy_ids)
    ]
    simulation = Simulation(
        load_map_data(
            {
                "id": "sustained_complex_battle_gui",
                "name": "Sustained Complex Battle GUI",
                "width": 80,
                "height": 60,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [0, 14, 80, 4, "road"],
                        [0, 42, 80, 4, "road"],
                        [37, 0, 6, 18, "forest"],
                        [37, 42, 6, 18, "forest"],
                    ],
                },
                "entities": units
                + [
                    {
                        "id": "player_factory",
                        "kind": "factory",
                        "owner": "player",
                        "position": [2, 4],
                    },
                    {
                        "id": "player_repair_hub",
                        "kind": "repair_hub",
                        "owner": "player",
                        "position": [2, 50],
                    },
                    {
                        "id": "enemy_factory",
                        "kind": "factory",
                        "owner": "enemy",
                        "position": [74, 4],
                    },
                    {
                        "id": "enemy_repair_hub",
                        "kind": "repair_hub",
                        "owner": "enemy",
                        "position": [74, 50],
                    },
                ],
            }
        ),
        random_seed=211,
    )
    return simulation, player_ids, enemy_ids


def test_thousand_selected_units_move_in_gui() -> None:
    simulation, entity_ids = _thousand_tank_simulation()
    assert simulation.execute(MoveCommand(entity_ids, Point(70.5, 50.5))).accepted
    _show(simulation, entity_ids, "1,000 selected tanks execute one move order.")


def test_thousand_selected_units_patrol_in_gui() -> None:
    simulation, entity_ids = _thousand_tank_simulation()
    target = rectangle_region(Point(56, 5), Point(76, 55))
    assert simulation.execute(CreatePatrolCommand(entity_ids, target)).accepted
    _show(simulation, entity_ids, "1,000 selected tanks patrol a large region.")


def test_thousand_selected_units_defend_in_gui() -> None:
    simulation, entity_ids = _thousand_tank_simulation()
    target = rectangle_region(Point(56, 5), Point(76, 55))
    assert simulation.execute(CreateDefendCommand(entity_ids, target)).accepted
    _show(simulation, entity_ids, "1,000 selected tanks defend a large region.")


def test_native_four_k_thousand_scout_head_on_collision_in_gui() -> None:
    simulation, eastbound, westbound = _head_on_scout_simulation()
    assert simulation.execute(MoveCommand(eastbound, Point(70.5, 30.5))).accepted
    assert simulation.execute(MoveCommand(westbound, Point(9.5, 30.5))).accepted
    _show(
        simulation,
        eastbound + westbound,
        "Native-4K OpenGL head-on movement with 1,000 scouts.",
        display_size=DISPLAY_SIZE,
    )


def test_opengl_detailed_health_and_selection_feedback_in_gui() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "opengl_detail_gui",
                "name": "OpenGL Detail GUI",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "selected_scout",
                        "kind": "scout",
                        "owner": "player",
                        "position": [3.5, 3.5],
                    },
                    {
                        "id": "enemy_scout",
                        "kind": "scout",
                        "owner": "enemy",
                        "position": [7.5, 7.5],
                    },
                    {
                        "id": "factory",
                        "kind": "factory",
                        "owner": "player",
                        "position": [8, 2],
                    },
                ],
            }
        )
    )
    _show(
        simulation,
        ("selected_scout",),
        "Inspect health bars and the selected-unit outline.",
        display_size=DISPLAY_SIZE,
        inspected_entity_id="enemy_scout",
    )


def test_opengl_projectile_feedback_in_gui() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "opengl_projectile_gui",
                "name": "OpenGL Projectile GUI",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "player_scout",
                        "kind": "scout",
                        "owner": "player",
                        "position": [3.5, 3.5],
                    },
                    {
                        "id": "enemy_scout",
                        "kind": "scout",
                        "owner": "enemy",
                        "position": [6.5, 3.5],
                    },
                ],
            }
        )
    )
    _show(
        simulation,
        ("player_scout",),
        "Two scouts exchange live GPU-batched projectiles.",
        display_size=DISPLAY_SIZE,
        inspected_entity_id="enemy_scout",
    )


def test_gpu_interpolated_scout_motion_in_gui() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "gpu_interpolation_gui",
                "name": "GPU Interpolation GUI",
                "width": 20,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "scout",
                        "kind": "scout",
                        "owner": "player",
                        "position": [2.5, 5.5],
                    }
                ],
            }
        )
    )
    assert simulation.execute(MoveCommand(("scout",), Point(17.5, 5.5))).accepted
    _show(
        simulation,
        ("scout",),
        "Inspect smooth GPU interpolation between fixed simulation ticks.",
        display_size=DISPLAY_SIZE,
    )


def test_sustained_mixed_thousand_unit_battle_in_gui() -> None:
    simulation, player_ids, enemy_ids = _battle_simulation()
    target = PolylineTarget((Point(36, 18), Point(44, 42)))
    defended = simulation.execute(
        CreateDefendCommand(
            player_ids,
            target,
            title="Hold the center against the enemy army",
            original_instruction="Defend this line with the selected army.",
        )
    )
    assert defended.accepted
    assert simulation.execute(MoveCommand(enemy_ids, Point(17.5, 30.5), "enemy")).accepted
    _show(
        simulation,
        player_ids,
        "Sustained 500-vs-500 mixed-unit battle.",
        display_size=DISPLAY_SIZE,
        inspected_entity_id="enemy_0000",
        active_target=target,
    )
