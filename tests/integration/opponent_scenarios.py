"""Flat, small match fixtures for opponent decisions and workload acceptance."""

from __future__ import annotations

from airts.simulation import Simulation
from airts.systems import economy
from airts.world.map import load_map_data
from airts.world.opponents import Squad, SquadPhase
from airts.world.units.catalog import EntityKind


def opponent_scenario(
    army: tuple[tuple[EntityKind, float, float], ...] = (),
    enemies: tuple[tuple[EntityKind, float, float], ...] = (),
    *,
    resource_sites: tuple[tuple[int, int], ...] = ((66, 66), (52, 66), (32, 66), (10, 12)),
    outposts: tuple[tuple[EntityKind, float, float], ...] = (),
) -> Simulation:
    entities: list[dict[str, object]] = [
        {"id": "player_factory", "kind": "factory", "owner": "player", "position": [4, 4]},
        {"id": "ai_factory", "kind": "factory", "owner": "ai", "position": [60, 60]},
        {"id": "ai_builder", "kind": "builder", "owner": "ai", "position": [58.5, 64.5]},
        {"id": "ai_income", "kind": "resource_generator", "owner": "ai", "position": [66, 66]},
    ]
    for prefix, owner, units in (
        ("army", "ai", army),
        ("enemy", "player", enemies),
        ("outpost", "ai", outposts),
    ):
        entities.extend(
            {"id": f"{prefix}_{index:04d}", "kind": kind.value, "owner": owner, "position": [x, y]}
            for index, (kind, x, y) in enumerate(units)
        )
    return Simulation(
        load_map_data(
            {
                "id": "opponent-decisions",
                "version": 1,
                "name": "Opponent decisions",
                "width": 80,
                "height": 80,
                "terrain": {"default": "grass"},
                "entities": entities,
                "resource_sites": [list(site) for site in resource_sites],
                "match_slots": [
                    {"owner": "player", "controller": "player", "team": 1},
                    {"owner": "ai", "controller": "ai", "team": 2},
                ],
            }
        )
    )


def opening_army(count: int = 6) -> tuple[tuple[EntityKind, float, float], ...]:
    return tuple(
        (EntityKind.LIGHT_TANK, 55.5 + index % 3, 58.5 + index // 3) for index in range(count)
    )


def fragmented_army(count: int = 600) -> Simulation:
    """An old front contact, opposing rear orders and a large disconnected command chain."""
    army = tuple(
        (EntityKind.LIGHT_TANK, 56.5 + i % 2, 52.5 + i // 2)
        if i < 4
        else (EntityKind.LIGHT_TANK, 98.5 + i % 20 * 1.2, 85.5 + i // 20 * 1.2)
        for i in range(count)
    )
    data = opponent_scenario(enemies=((EntityKind.LIGHT_TANK, 45.5, 50.5),)).game_map.to_dict()
    data["width"] = data["height"] = 160
    data["terrain"] = {"default": "grass"}
    for entity in data["entities"]:
        if entity["id"] == "ai_factory":
            entity["position"] = [140, 140]
    data["entities"].extend(
        {"id": f"army_{i:04d}", "kind": kind.value, "owner": "ai", "position": [x, y]}
        for i, (kind, x, y) in enumerate(army)
    )
    simulation = Simulation(load_map_data(data), record_history=False)
    simulation.opponents["ai"].squads = [
        Squad(
            entity_ids=[f"army_{i:04d}" for i in range(4)],
            phase=SquadPhase.ATTACKING,
            target_id="enemy_0000",
        ),
        *(
            Squad(entity_ids=[f"army_{i:04d}" for i in range(start, min(start + 32, count))])
            for start in range(4, count, 32)
        ),
    ]
    return simulation


def established_empire(*, extra_builders: int = 0) -> Simulation:
    entities: list[dict[str, object]] = [
        {"id": "human", "kind": "factory", "position": [4, 4], "owner": "player"},
        {"id": "center", "kind": "command_center", "position": [232, 208], "owner": "ai"},
        {"id": "income", "kind": "resource_generator", "position": [244, 212], "owner": "ai"},
    ]
    for kind, count, columns, x, y, stride in (
        (EntityKind.FACTORY, 50, 12, 148, 128, 8),
        (EntityKind.MATTER_SYNTHESIZER, 40, 10, 148, 176, 8),
        (EntityKind.TURRET, 36, 6, 148, 124, 18),
        (EntityKind.REPAIR_HUB, 7, 7, 148, 212, 8),
    ):
        entities.extend(
            {
                "id": f"{kind.value}_{i}",
                "kind": kind.value,
                "position": [
                    x + i % columns * stride,
                    (124, 142, 172, 188, 204, 220)[i // columns]
                    if kind is EntityKind.TURRET
                    else y + i // columns * stride,
                ],
                "owner": "ai",
            }
            for i in range(count)
        )
    entities.extend(
        {"id": f"worker_{i}", "kind": "builder", "position": [150.5 + i * 2, 206.5], "owner": "ai"}
        for i in range(12)
    )
    entities.extend(
        {
            "id": f"expansion_worker_{i}",
            "kind": "builder",
            "position": [180.5 + i % 20 * 2, 85.5 + i // 20 * 2],
            "owner": "ai",
        }
        for i in range(extra_builders)
    )
    entities.extend(
        {
            "id": f"army_{i}",
            "kind": "light_tank",
            "position": [160.5 + i % 10, 110.5 + i // 10],
            "owner": "ai",
        }
        for i in range(40)
    )
    simulation = Simulation(
        load_map_data(
            {
                "id": "established-empire",
                "name": "Established empire",
                "width": 256,
                "height": 224,
                "terrain": {"default": "grass", "rectangles": [[127, 0, 1, 224, "rock"]]},
                "entities": entities,
                "resource_sites": [[244, 212]],
                "match_slots": [
                    {"owner": "player", "controller": "player", "team": 1},
                    {"owner": "ai", "controller": "ai", "team": 2},
                ],
            }
        )
    )
    for entity in simulation.entities.values():
        if entity.owner_id == "ai" and entity.kind.upgrades:
            entity.upgrade.tier = 3
    economy.rebuild_income(simulation)
    return simulation
