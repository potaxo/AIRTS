"""Bounded AI decisions alongside a thousand-unit headless match."""

from __future__ import annotations

from collections.abc import Callable
from math import ceil
from time import perf_counter
from unittest.mock import patch

import pytest
from tests.integration.opponent_scenarios import (
    established_empire,
    fragmented_army,
    opponent_scenario,
)

from airts.contracts.automations import ConstructionParameters, ProductionParameters
from airts.contracts.commands import AttackCommand, Command, MoveCommand
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems import economy, opponent_ai
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
    commands = 0
    with patch.object(opponent_ai, "_drive_army", wraps=opponent_ai._drive_army) as planning:
        for _ in range(60):
            started = perf_counter()
            simulation.advance()
            samples.append(perf_counter() - started)
            assert simulation._opponent_command_count <= MAX_ORDERS
            assert simulation._opponent_candidate_count <= 2 * MAX_TARGET_CANDIDATES
            assert simulation._opponent_forecast_count <= 16
            commands += simulation._opponent_command_count
    decisions = planning.call_count
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


def test_fragmented_mass_army_mobilizes_and_makes_progress(
    monkeypatch: pytest.MonkeyPatch,
    check_timing: bool,
    record_property: Callable[[str, object], None],
) -> None:
    simulation = fragmented_army()
    issued: list[Command] = []
    submit = Simulation._execute_command

    def capture(self: Simulation, command: Command, *, record: bool = True):
        if not record and isinstance(command, (AttackCommand, MoveCommand)):
            issued.append(command)
        return submit(self, command, record=record)

    monkeypatch.setattr(Simulation, "_execute_command", capture)
    # Old rear commands face away from the front; the mass order must replace them.
    rear = tuple(f"army_{i:04d}" for i in range(4, 600))
    assert simulation.execute(MoveCommand(rear, Point(142, 100), owner_id="ai")).accepted
    starts = {key: simulation.entities[key].position for key in rear}
    target = simulation.entities["player_factory"].selection_position
    samples = []
    for _ in range(120):
        started = perf_counter()
        simulation.advance()
        samples.append((perf_counter() - started) * 1000)
        assert simulation._opponent_command_count <= MAX_ORDERS
        assert simulation._routes.combat_route_count <= simulation._routes.combat_budget
    offensive = [
        c for c in issued if isinstance(c, AttackCommand) and c.target_entity_id == "player_factory"
    ]
    assert offensive and len(offensive[0].entity_ids) >= 500
    # Existing pursuit is not replaced every second, and no mass gathers on a tiny leader.
    assert sum(len(c.entity_ids) for c in offensive) < 800
    assert not any(isinstance(c, MoveCommand) and len(c.entity_ids) >= 500 for c in issued)
    progress = [
        starts[key].distance_to(target) - simulation.entities[key].position.distance_to(target)
        for key in rear
        if key in simulation.entities
    ]
    assert sum(distance > 15 for distance in progress) >= len(progress) * 0.85
    groups = simulation.opponents["ai"].squads
    assert len(groups) < 30
    assert {squad.target_id for squad in groups} == {"player_factory"}
    p95_ms = sorted(samples)[ceil(len(samples) * 0.95) - 1]
    record_property("mass_army_tick_p95_ms", p95_ms)
    record_property("first_mass_order_size", len(offensive[0].entity_ids))
    record_property("rear_progress_15", sum(distance > 15 for distance in progress))
    if check_timing:
        assert p95_ms < 100, f"mass mobilization tick p95 was {p95_ms:.2f} ms"


def test_rich_industry_fills_factories_and_uses_more_than_twenty_four_builders(
    check_timing: bool,
    record_property: Callable[[str, object], None],
) -> None:
    simulation = established_empire(extra_builders=64)
    simulation.resources["ai"] = 200000
    opening = {
        unit.entity_id
        for unit in simulation.entities.values()
        if unit.owner_id == "ai" and unit.kind is EntityKind.FACTORY
    }
    peak_sites = 0
    samples = []
    for _ in range(150):
        started = perf_counter()
        simulation.advance()
        samples.append((perf_counter() - started) * 1000)
        assert simulation._opponent_command_count <= MAX_ORDERS
        assert simulation.resources["ai"] >= 0
        sites = [
            job.parameters
            for job in simulation.live_automations
            if isinstance(job.parameters, ConstructionParameters)
        ]
        builders = [key for site in sites for key in site.builder_ids]
        assert len(builders) == len(set(builders))
        peak_sites = max(peak_sites, len(sites))
    working = {
        job.parameters.factory_id
        for job in simulation.live_automations
        if isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is not EntityKind.BUILDER
        and job.parameters.continuous
    }
    assert opening <= working
    assert peak_sites > 24
    military = [
        job.parameters
        for job in simulation.automations.values()
        if isinstance(job.parameters, ProductionParameters) and job.parameters.factory_id in opening
    ]
    assert sum(job.produced_count for job in military) >= 50
    assert len({job.unit_kind for job in military}) >= 4
    assert not simulation.command_history
    p95_ms = sorted(samples)[ceil(len(samples) * 0.95) - 1]
    record_property("industrial_tick_p95_ms", p95_ms)
    record_property("working_initial_factories", len(opening & working))
    record_property("peak_construction_sites", peak_sites)
    if check_timing:
        assert p95_ms < 100, f"parallel industrial tick p95 was {p95_ms:.2f} ms"
