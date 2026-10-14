"""Flat, small match fixtures for opponent decisions and workload acceptance."""

from __future__ import annotations

from airts.simulation import Simulation
from airts.world.map import load_map_data
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
