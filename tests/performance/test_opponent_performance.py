"""Bounded AI decisions alongside a thousand-unit headless match."""

from __future__ import annotations

from collections.abc import Callable
from math import ceil
from time import perf_counter

from tests.integration.opponent_scenarios import opponent_scenario

from airts.systems.opponent_ai import MAX_ORDERS, MAX_SQUAD_SIZE, MAX_TARGET_CANDIDATES
from airts.world.map import EntityKind


def test_thousand_unit_opponent_workload(
    check_timing: bool, record_property: Callable[[str, object], None]
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
    record_property("opponent_decisions", decisions)
    record_property("opponent_commands", commands)
    if check_timing:
        assert p95_ms < 100, f"thousand-unit opponent tick p95 was {p95_ms:.2f} ms"
