"""Continued reinvestment and large-army control without empire-size ceilings."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from tests.integration.opponent_scenarios import established_empire, opponent_scenario

from airts.contracts.automations import ProductionParameters
from airts.contracts.commands import CreateConstructionCommand, SetAIIncomeMultiplierCommand
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems import economy, opponent_ai
from airts.world.map import load_map_data
from airts.world.opponents import Squad, SquadPhase
from airts.world.units.catalog import EntityKind


@pytest.mark.parametrize("multiplier,ticks,cash", [(1, 1000, 3000), (5, 360, 6000)])
def test_established_empire_keeps_investing_without_a_fixed_size_ceiling(
    multiplier: int,
    ticks: int,
    cash: int,
    record_property: Callable[[str, object], None],
) -> None:
    simulation = established_empire()
    assert simulation.execute(SetAIIncomeMultiplierCommand(multiplier)).accepted
    simulation.resources["ai"] = cash
    opening = set(simulation.entities)
    for _ in range(ticks // 10):
        simulation.advance(10)
        assert simulation._opponent_command_count <= opponent_ai.MAX_ORDERS
        assert simulation.resources["ai"] >= 0
    own = [unit for unit in simulation.entities.values() if unit.owner_id == "ai"]
    added_income = sum(
        unit.entity_id not in opening and unit.profile.income_per_second > 0 and unit.is_complete
        for unit in own
    )
    workers = sum(unit.profile.can_build for unit in own)
    produced = sum(
        job.parameters.produced_count
        for job in simulation.automations.values()
        if isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is not EntityKind.BUILDER
    )
    batches = [
        job.parameters.target_count
        for job in simulation.automations.values()
        if isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is not EntityKind.BUILDER
    ]
    record_property("added_income", added_income)
    record_property("workers", workers)
    record_property("produced_soldiers", produced)
    record_property("largest_batch", max(batches, default=0))
    assert added_income >= (1 if multiplier == 1 else 4)
    assert produced >= 20
    if multiplier == 1:
        assert workers > 32
    else:
        assert max(batches) > 8


def test_large_local_army_uses_its_whole_force_and_dispatches_a_mass_attack() -> None:
    own = tuple((EntityKind.LIGHT_TANK, 40.5 + i // 15, 40.5 + i % 15) for i in range(300))
    enemies = tuple((EntityKind.LIGHT_TANK, 25.5 + i % 15, 42.5 + i // 15) for i in range(150))
    simulation = opponent_scenario(own, enemies)
    simulation.opponents["ai"].squads = [
        Squad(
            entity_ids=[f"army_{i:04d}" for i in range(start, min(start + 32, 300))],
            phase=SquadPhase.ADVANCING,
            target_id="player_factory",
        )
        for start in range(0, 300, 32)
    ]
    simulation.advance(10)
    groups = simulation.opponents["ai"].squads
    assert not any(squad.phase is SquadPhase.RETREATING for squad in groups)
    fighting = [
        unit
        for unit in simulation.entities.values()
        if unit.owner_id == "ai" and unit.pursue_target
    ]
    assert len(fighting) >= 128
    assert simulation._opponent_command_count <= opponent_ai.MAX_ORDERS


def test_rear_reinforcements_receive_orders_while_the_front_keeps_marching() -> None:
    army = tuple(
        (EntityKind.LIGHT_TANK, x + i % 8, y + i // 8)
        for x, y in ((20.5, 24.5), (38.5, 24.5), (58.5, 38.5))
        for i in range(32)
    )
    simulation = opponent_scenario(army)
    simulation.opponents["ai"].squads = [
        Squad(
            entity_ids=[f"army_{i:04d}" for i in range(start, start + 32)],
            phase=SquadPhase.GATHERING if start == 64 else SquadPhase.ADVANCING,
            target_id=None if start == 64 else "player_factory",
        )
        for start in (0, 32, 64)
    ]
    simulation.advance(20)
    rear = [simulation.entities[f"army_{i:04d}"] for i in range(64, 96)]
    assert all(unit.move_target is not None and unit.move_target.x < 40 for unit in rear)


def test_a_local_raid_does_not_pin_an_unneeded_field_army_at_home() -> None:
    army = tuple(
        (EntityKind.LIGHT_TANK, x + i % 8, y + i // 8)
        for x, y in ((50.5, 60.5), (20.5, 28.5), (20.5, 34.5))
        for i in range(32)
    )
    enemies = tuple((EntityKind.LIGHT_TANK, 70.5 + i % 4, 60.5 + i // 4) for i in range(8))
    simulation = opponent_scenario(army, enemies)
    simulation.opponents["ai"].squads = [
        Squad(
            entity_ids=[f"army_{i:04d}" for i in range(start, start + 32)],
            phase=SquadPhase.ADVANCING,
            target_id="player_factory",
        )
        for start in (0, 32, 64)
    ]
    simulation.advance(10)
    assert simulation.opponents["ai"].squads[0].phase is SquadPhase.DEFENDING
    assert (
        sum(
            simulation.entities[f"army_{i:04d}"].move_target is not None
            or simulation.entities[f"army_{i:04d}"].pursue_target
            for i in range(32, 96)
        )
        >= 48
    )


def test_owned_remote_economy_receives_forts_and_production_despite_home_coverage() -> None:
    original = opponent_scenario(
        resource_sites=((66, 66), (14, 46)),
        outposts=(
            (EntityKind.RESOURCE_GENERATOR, 14, 46),
            (EntityKind.BUILDER, 20.5, 46.5),
            *((EntityKind.TURRET, x, y) for x in (50, 55, 60, 65, 70) for y in (50, 55)),
        ),
    )
    data = original.game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[9, 0, 1, 80, "rock"]]}
    simulation = Simulation(load_map_data(data))
    simulation.resources["ai"] = 20000
    for entity in simulation.entities.values():
        if entity.owner_id == "ai" and entity.kind.upgrades:
            entity.upgrade.tier = 3
    economy.rebuild_income(simulation)
    simulation.advance(400)
    near = {
        unit.kind
        for unit in simulation.entities.values()
        if unit.owner_id == "ai"
        and unit.is_complete
        and unit.selection_position.distance_to(Point(15, 47)) <= 18
    }
    assert {EntityKind.FACTORY, EntityKind.TURRET}.issubset(near)


def test_funded_income_upgrades_can_run_in_parallel_while_builders_are_travelling() -> None:
    simulation = opponent_scenario(
        resource_sites=((66, 66), (52, 66)),
        outposts=((EntityKind.RESOURCE_GENERATOR, 52, 66),),
    )
    simulation.resources["ai"] = 1500
    assert simulation.execute(
        CreateConstructionCommand("ai_builder", EntityKind.TURRET, Point(70, 10), owner_id="ai")
    ).accepted
    simulation.advance(20)
    assert sum(unit.upgrade.remaining_ticks > 0 for unit in simulation.entities.values()) >= 2


def test_developed_sectors_remain_eligible_when_they_still_have_physical_space() -> None:
    entities: list[dict[str, object]] = [
        {"id": "human", "kind": "factory", "position": [4, 52], "owner": "player"},
        {"id": "factory", "kind": "factory", "position": [52, 52], "owner": "ai"},
        {"id": "center", "kind": "command_center", "position": [40, 52], "owner": "ai"},
    ]
    for sector in range(4):
        entities.extend(
            {
                "id": f"income_{sector}_{i}",
                "kind": "matter_synthesizer",
                "position": [sector % 2 * 32 + i % 8 * 4, sector // 2 * 32 + i // 8 * 4],
                "owner": "ai",
            }
            for i in range(22)
        )
    entities.extend(
        {"id": f"worker_{i}", "kind": "builder", "position": [48.5 + i, 59.5], "owner": "ai"}
        for i in range(12)
    )
    simulation = Simulation(
        load_map_data(
            {
                "id": "dense-sectors",
                "name": "Dense sectors",
                "width": 64,
                "height": 64,
                "terrain": {"default": "grass"},
                "entities": entities,
                "resource_sites": [],
                "match_slots": [
                    {"owner": "player", "controller": "player", "team": 1},
                    {"owner": "ai", "controller": "ai", "team": 2},
                ],
            }
        )
    )
    simulation.resources["ai"] = 50000
    for unit in simulation.entities.values():
        if unit.kind.upgrades:
            unit.upgrade.tier = 3
    economy.rebuild_income(simulation)
    opening = set(simulation.entities)
    simulation.advance(160)
    assert any(
        not unit.is_movable and unit.entity_id not in opening and unit.is_complete
        for unit in simulation.entities.values()
    )
