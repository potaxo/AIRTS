"""Fresh scenario constructors shared by integration contracts."""

from __future__ import annotations

from dataclasses import replace
from math import ceil, sqrt

from airts.contracts.commands import CreateProductionCommand
from airts.contracts.spatial import Point, rectangle_region
from airts.simulation import Simulation
from airts.world.map import EntityKind, EntitySpec, GameMap, load_example_map, load_map_data


def gated_attack_simulation(
    blocked_count: int = 16, reachable_count: int = 1
) -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    blocked = tuple(f"blocked_{index:03d}" for index in range(blocked_count))
    reachable = tuple(f"reachable_{index:03d}" for index in range(reachable_count))
    entities = [
        {"id": key, "kind": "scout", "position": [2.5 + index, 1.5]}
        for index, key in enumerate(blocked)
    ] + [
        {"id": key, "kind": "scout", "position": [24.5 + index % 3, 1.5 + index // 3]}
        for index, key in enumerate(reachable)
    ]
    return (
        Simulation(
            load_map_data(
                {
                    "id": "gated_attack",
                    "name": "Gated attack",
                    "width": 40,
                    "height": 20,
                    "terrain": {
                        "default": "grass",
                        "rectangles": [[20, 0, 1, 8, "water"], [20, 12, 1, 8, "water"]],
                    },
                    "entities": entities
                    + [
                        {"id": "gate", "kind": "factory", "position": [20, 8]},
                        {"id": "target", "kind": "factory", "owner": "enemy", "position": [32, 8]},
                    ],
                }
            )
        ),
        blocked,
        reachable,
    )


def injected_overlap_simulation(
    unit_count: int = 100,
    spacing_ratio: float = 0.5,
    *,
    selected_count: int = 9,
) -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    """Inject a dense body lattice after legal map creation, only for recovery tests.

    The map retained by save/replay starts with separated scouts. Injection updates
    authoritative body positions and occupancy; it is deliberately not a replayable
    player operation or evidence of a normally produced overlap.
    """
    columns = ceil(sqrt(unit_count))
    rows = ceil(unit_count / columns)
    entity_ids = tuple(f"scout_{index:04d}" for index in range(unit_count))
    simulation = Simulation(
        load_map_data(
            {
                "id": f"injected_overlap_{unit_count}_{spacing_ratio}",
                "name": "Injected overlap recovery",
                "width": 96,
                "height": 96,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "position": [3.5 + index % columns, 3.5 + index // columns],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=191,
    )
    center = Point(48, 48)
    spacing = 0.6 * spacing_ratio
    allowed_conflicts = frozenset(entity_ids)
    for index, entity_id in enumerate(entity_ids):
        entity = simulation.entities[entity_id]
        entity.position = Point(
            center.x + (index % columns - (columns - 1) / 2) * spacing,
            center.y + (index // columns - (rows - 1) / 2) * spacing,
        )
        simulation.occupancy.move(entity_id, entity.occupied_cells, allowed_conflicts)
    selected = tuple(
        sorted(
            entity_ids,
            key=lambda entity_id: (
                simulation.entities[entity_id].position.distance_to(center),
                entity_id,
            ),
        )[:selected_count]
    )
    return simulation, entity_ids, selected


def hold_contact_simulation() -> Simulation:
    """A single-file throat opens into enough space for contact yielding and return."""
    return Simulation(
        load_map_data(
            {
                "id": "hold_contact",
                "name": "Hold contact and recovery",
                "width": 20,
                "height": 12,
                "terrain": {
                    "default": "grass",
                    "rectangles": [[0, 0, 7, 5, "rock"], [0, 6, 7, 6, "rock"]],
                },
                "entities": [
                    {"id": "holder", "kind": "scout", "position": [5.5, 5.5]},
                    {"id": "mover", "kind": "scout", "position": [4.5, 5.5]},
                ],
            }
        ),
        random_seed=113,
    )


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
