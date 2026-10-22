"""Continued reinvestment and large-army control without empire-size ceilings."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from tests.integration.opponent_scenarios import (
    established_empire,
    fragmented_army,
    opponent_scenario,
)

from airts.adapters.persistence import MATCH_SAVE_SCHEMA, load_simulation_data
from airts.contracts.automations import AutomationStatus, ProductionParameters
from airts.contracts.commands import (
    CreateConstructionCommand,
    CreateProductionCommand,
    PauseAutomationCommand,
    SetAIIncomeMultiplierCommand,
)
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
    standing = [
        job.parameters.factory_id
        for job in simulation.automations.values()
        if isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is not EntityKind.BUILDER
        and job.parameters.continuous
    ]
    record_property("added_income", added_income)
    record_property("workers", workers)
    record_property("produced_soldiers", produced)
    record_property("standing_military_sources", len(set(standing)))
    assert added_income >= (1 if multiplier == 1 else 4)
    assert produced >= 20
    if multiplier == 1:
        assert workers > 32
    else:
        assert standing


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


def test_committed_mass_army_survives_losses_and_load_without_regrouping() -> None:
    simulation = fragmented_army()
    simulation.advance(10)
    assert all(
        squad.target_id == "player_factory" and squad.phase is SquadPhase.ADVANCING
        for squad in simulation.opponents["ai"].squads
    )
    for index in range(400, 600):
        simulation.remove_entity(f"army_{index:04d}")
    restored = load_simulation_data(
        {
            "schema": MATCH_SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        },
        record_history=False,
    )
    restored.advance(30)
    groups = restored.opponents["ai"].squads
    assert 250 <= sum(len(squad.entity_ids) for squad in groups) < 500
    assert all(squad.phase is SquadPhase.ADVANCING for squad in groups)
    assert {squad.target_id for squad in groups} == {"player_factory"}
    assert all(restored.entities[key].pursue_target for squad in groups for key in squad.entity_ids)


def test_rich_background_production_respects_manual_pauses_and_saved_standing_orders() -> None:
    simulation = established_empire()
    simulation.resources["ai"] = 200000
    manual = simulation.execute(
        CreateProductionCommand(
            "factory_0", EntityKind.LIGHT_TANK, 1, continuous=True, owner_id="ai"
        )
    )
    key = manual.automation_id or ""
    assert simulation.execute(PauseAutomationCommand(key, owner_id="ai")).accepted
    simulation.advance(100)
    assert simulation.automations[key].status is AutomationStatus.PAUSED
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.continuous
        and job.parameters.factory_id != "factory_0"
        for job in simulation.live_automations
    )
    restored = load_simulation_data(
        {
            "schema": MATCH_SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    simulation.advance(20)
    restored.advance(20)
    assert restored.export_state() == simulation.export_state()
    assert restored.automations[key].status is AutomationStatus.PAUSED


def test_front_contact_does_not_starve_ready_rear_cohorts() -> None:
    army = tuple(
        (EntityKind.LIGHT_TANK, x + i % 4, y + i // 4)
        for x, y in ((35.5, 40.5), (65.5, 3.5), (65.5, 13.5))
        for i in range(24)
    )
    simulation = opponent_scenario(army, ((EntityKind.SCOUT, 22.5, 40.5),))
    simulation.opponents["ai"].squads = [
        Squad(
            entity_ids=[f"army_{i:04d}" for i in range(start, start + 24)],
            phase=SquadPhase.ATTACKING if start == 0 else SquadPhase.GATHERING,
            target_id="enemy_0000" if start == 0 else None,
        )
        for start in (0, 24, 48)
    ]
    simulation.advance(20)
    assert all(simulation.entities[f"army_{i:04d}"].pursue_target for i in range(24, 72))


def test_large_army_still_rescues_a_base_without_recalling_the_whole_offensive() -> None:
    original = fragmented_army()
    data = original.game_map.to_dict()
    data["entities"].extend(
        {
            "id": f"raid_{i}",
            "kind": "heavy_tank",
            "owner": "player",
            "position": [149.5 + i % 4, 140.5 + i // 4],
        }
        for i in range(8)
    )
    simulation = Simulation(load_map_data(data), record_history=False)
    simulation.advance(20)
    groups = simulation.opponents["ai"].squads
    defending = [squad for squad in groups if squad.phase is SquadPhase.DEFENDING]
    advancing = [squad for squad in groups if squad.phase is SquadPhase.ADVANCING]
    assert defending
    assert all((squad.target_id or "").startswith("raid_") for squad in defending)
    assert sum(len(squad.entity_ids) for squad in advancing) >= 500
    assert {squad.target_id for squad in advancing} == {"player_factory"}


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
