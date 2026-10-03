"""Fresh scenario constructors shared by integration contracts."""

from __future__ import annotations

from dataclasses import replace

from airts.contracts.commands import CreateProductionCommand
from airts.contracts.spatial import Point, rectangle_region
from airts.simulation import Simulation
from airts.world.map import EntityKind, EntitySpec, GameMap, load_example_map, load_map_data


def runtime_map() -> GameMap:
    return load_map_data(
        {
            "id": "runtime",
            "name": "Runtime",
            "width": 24,
            "height": 16,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [
                {"id": "unit_01", "kind": "scout", "position": [1.5, 2.5]},
                {"id": "unit_02", "kind": "light_tank", "position": [1.5, 4.5]},
                {"id": "unit_03", "kind": "heavy_tank", "position": [1.5, 6.5]},
                {"id": "factory", "kind": "factory", "position": [7, 8]},
                {"id": "repair", "kind": "repair_hub", "position": [4, 9]},
                {"id": "command", "kind": "command_center", "position": [15, 8]},
            ],
        }
    )


def economy_combat_simulation() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "phase5_test",
                "name": "Phase 5 Test",
                "width": 16,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "tank", "kind": "light_tank", "owner": "player", "position": [2.5, 2.5]},
                    {"id": "enemy", "kind": "scout", "owner": "enemy", "position": [4.5, 2.5]},
                    {"id": "factory", "kind": "factory", "owner": "player", "position": [2, 8]},
                    {"id": "repair", "kind": "repair_hub", "owner": "player", "position": [8, 8]},
                    {
                        "id": "generator",
                        "kind": "resource_generator",
                        "owner": "player",
                        "position": [12, 9],
                    },
                ],
            }
        ),
        random_seed=41,
    )


def builder_factory_simulation() -> Simulation:
    game_map = load_map_data(
        {
            "id": "builder_factory_milestone",
            "name": "Builder and factory test map",
            "width": 24,
            "height": 24,
            "terrain": {
                "default": "grass",
                "rectangles": [[20, 20, 2, 2, "water"]],
            },
            "entities": [
                {"id": "factory", "kind": "factory", "position": [1, 1]},
                {"id": "factory_2", "kind": "factory", "position": [17, 1]},
                {"id": "builder", "kind": "builder", "position": [8.5, 8.5]},
                {"id": "builder_2", "kind": "builder", "position": [9.5, 8.5]},
                {"id": "scout", "kind": "scout", "position": [10.5, 8.5]},
                {"id": "tank", "kind": "light_tank", "position": [11.5, 8.5]},
            ],
        }
    )
    simulation = Simulation(game_map)
    simulation.resources["player"] = 10_000
    return simulation


def interaction_simulation() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "interaction",
                "name": "Interaction",
                "width": 16,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "unit", "kind": "scout", "owner": "player", "position": [2.5, 2.5]},
                    {"id": "tank", "kind": "light_tank", "owner": "player", "position": [4.5, 2.5]},
                    {"id": "factory", "kind": "factory", "owner": "player", "position": [3, 3]},
                    {"id": "enemy", "kind": "scout", "owner": "enemy", "position": [12.5, 12.5]},
                    {
                        "id": "generator",
                        "kind": "resource_generator",
                        "owner": "player",
                        "position": [10, 3],
                    },
                ],
            }
        ),
        random_seed=7,
    )


def growing_factory_simulation(*, enemies: bool = False) -> tuple[Simulation, tuple[str, ...]]:
    game_map = load_example_map()
    game_map = replace(
        game_map,
        entities=tuple(
            entity for entity in game_map.entities if enemies or entity.owner_id == "player"
        )
        + tuple(
            EntitySpec(f"factory_{index}", EntityKind.FACTORY, "player", Point(11, 48 + index * 5))
            for index in range(3)
        ),
    )
    simulation = Simulation(game_map, ambient_enemy_spawns=enemies)
    simulation.resources["player"] = 1_000_000
    production_ids = []
    factories = tuple(
        entity for entity in simulation.entities.values() if entity.kind is EntityKind.FACTORY
    )
    for index, factory in enumerate(factories):
        result = simulation.execute(
            CreateProductionCommand(
                factory.entity_id,
                EntityKind.HEAVY_TANK if enemies and index % 2 else EntityKind.LIGHT_TANK,
                1,
                continuous=True,
                defend_target=rectangle_region(Point(46, 28), Point(52, 35)),
            )
        )
        assert result.accepted and result.automation_id is not None
        production_ids.append(result.automation_id)
    return simulation, tuple(production_ids)
