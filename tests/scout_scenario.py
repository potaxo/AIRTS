"""Shared 1,000-scout workload; return fresh state for each caller."""

from airts.simulation import Simulation
from airts.world.map_model import load_map_data

UNIT_COUNT = 1_000
GROUP_SIZE = UNIT_COUNT // 2


def head_on_scout_simulation() -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    eastbound = tuple(f"east_{index:04d}" for index in range(GROUP_SIZE))
    westbound = tuple(f"west_{index:04d}" for index in range(GROUP_SIZE))
    simulation = Simulation(
        load_map_data(
            {
                "id": "four_k_thousand_scout_head_on",
                "name": "4K Thousand Scout Head-On Collision",
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
