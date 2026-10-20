"""Bounded AI decisions alongside a thousand-unit headless match."""

from __future__ import annotations

from collections.abc import Callable
from math import ceil
from time import perf_counter

import pytest
from tests.integration.opponent_scenarios import established_empire, opponent_scenario

from airts.simulation import Simulation
from airts.systems import economy
from airts.systems.opponent_ai import MAX_ORDERS, MAX_SQUAD_SIZE, MAX_TARGET_CANDIDATES
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


@pytest.mark.parametrize(
    "funds,size,builders,developed",
    [(1000, 80, 1, False), (100000, 320, 32, False), (100000, 320, 32, True)],
    ids=["opening-budget", "surplus-budget", "developed-budget"],
)
def test_thousand_unit_opponent_workload(
    funds: int,
    size: int,
    builders: int,
    developed: bool,
    check_timing: bool,
    record_property: Callable[[str, object], None],
) -> None:
    kinds = (EntityKind.LIGHT_TANK, EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK, EntityKind.SCOUT)
    own = tuple(
        (kinds[index % len(kinds)], x + 0.5, y + 0.5)
        for index, (x, y) in enumerate(
            (x, y)
            for y in range(44, 64)
            for x in range(44, 74)
            if not (60 <= x < 65 and 60 <= y < 65)
        )
    )[:500]
    enemy = tuple(
        (kinds[index % len(kinds)], 12.5 + index % 25, 12.5 + index // 25) for index in range(500)
    )
    simulation = opponent_scenario(own, enemy)
    if size != simulation.game_map.width:
        data = simulation.game_map.to_dict()
        data["width"] = data["height"] = size
        data["terrain"] = {"default": "grass"}
        data["entities"].extend(
            {"id": f"worker_{i}", "kind": "builder", "owner": "ai", "position": [90.5 + i, 90.5]}
            for i in range(builders - 1)
        )
        if developed:
            data["entities"].extend(
                {
                    "id": unit.entity_id,
                    "kind": unit.kind.value,
                    "owner": unit.owner_id,
                    "position": [unit.position.x, unit.position.y],
                }
                for unit in established_empire().entities.values()
                if unit.owner_id == "ai" and not unit.is_movable
            )
            data["resource_sites"].append([244, 212])
        simulation = Simulation(load_map_data(data))
        if developed:
            for unit in simulation.entities.values():
                if unit.owner_id == "ai" and unit.kind.upgrades:
                    unit.upgrade.tier = 3
            economy.rebuild_income(simulation)
    simulation.resources["ai"] = funds
    samples: list[float] = []
    decisions = 0
    commands = 0
    for _ in range(60):
        started = perf_counter()
        simulation.advance()
        samples.append(perf_counter() - started)
        assert simulation._opponent_command_count <= MAX_ORDERS
        assert simulation._opponent_candidate_count <= 2 * MAX_TARGET_CANDIDATES
        assert simulation._opponent_forecast_count <= 16
        commands += simulation._opponent_command_count
        decisions += int(simulation._opponent_forecast_count > 0)
    squads = simulation.opponents["ai"].squads
    assert len(squads) >= 15
    assert all(len(squad.entity_ids) <= MAX_SQUAD_SIZE for squad in squads)
    members = [
        key for squad in (simulation.opponents["ai"].reserve, *squads) for key in squad.entity_ids
    ]
    assert len(members) == len(set(members))
    assert decisions == 6
    assert commands > 0
    assert not simulation.command_history
    p95_ms = sorted(samples)[ceil(len(samples) * 0.95) - 1] * 1000
    record_property("opponent_tick_p95_ms", p95_ms)
    record_property("opening_funds", funds)
    record_property("map_size", size)
    record_property("builders", builders)
    record_property("developed", developed)
    record_property("opponent_decisions", decisions)
    record_property("opponent_commands", commands)
    if check_timing:
        assert p95_ms < 100, f"thousand-unit opponent tick p95 was {p95_ms:.2f} ms"
