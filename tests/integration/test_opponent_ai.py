"""Opponent behavior through the public facade, including save/replay memory."""

from __future__ import annotations

from copy import deepcopy

import pytest
from tests.integration.opponent_scenarios import opening_army, opponent_scenario

from airts.adapters.persistence import MATCH_SAVE_SCHEMA, PersistenceError, load_simulation_data
from airts.adapters.replay import capture_replay, load_replay_data, run_replay
from airts.contracts.automations import (
    AutomationStatus,
    ConstructionParameters,
    ProductionParameters,
    RepairParameters,
)
from airts.contracts.commands import (
    CreateConstructionCommand,
    CreateProductionCommand,
    PauseAutomationCommand,
)
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems.opponent_ai import engagement_margin
from airts.systems.skirmish import owner_status
from airts.world.map import load_map_data
from airts.world.opponents import SquadPhase
from airts.world.units.catalog import EntityKind


def test_force_prediction_distinguishes_health_and_unit_quality() -> None:
    simulation = opponent_scenario(
        tuple((EntityKind.SCOUT, 40.5 + i, 40.5) for i in range(6)),
        tuple((EntityKind.HEAVY_TANK, 40.5 + i, 45.5) for i in range(3)),
    )
    own = [unit for unit in simulation.entities.values() if unit.entity_id.startswith("army_")]
    enemies = [unit for unit in simulation.entities.values() if unit.entity_id.startswith("enemy_")]
    assert engagement_margin(own, enemies) < -0.2
    for enemy in enemies:
        enemy.health = 1
    assert engagement_margin(own, enemies) > 0.15


def test_stragglers_gather_before_attacking_and_ready_squad_keeps_target() -> None:
    scattered = opponent_scenario(
        opening_army(3) + tuple((EntityKind.LIGHT_TANK, 40.5 + i, 40.5) for i in range(3))
    )
    scattered.advance(10)
    assert scattered.opponents["ai"].squads[0].phase is SquadPhase.GATHERING
    assert not any(
        unit.pursue_target for unit in scattered.entities.values() if unit.owner_id == "ai"
    )
    assert any(
        unit.move_target is not None
        for unit in scattered.entities.values()
        if unit.owner_id == "ai"
    )
    ready = opponent_scenario(opening_army())
    ready.advance(10)
    squad = ready.opponents["ai"].squads[0]
    assert squad.phase is SquadPhase.ADVANCING
    assert squad.target_id == "player_factory"
    issued = squad.last_order_tick
    ready.advance(10)
    assert squad.target_id == "player_factory"
    assert squad.last_order_tick == issued
    assert len(ready.opponents["ai"].reserve.entity_ids) > 0


def test_outmatched_squad_withdraws_and_new_units_join_its_regroup() -> None:
    simulation = opponent_scenario(
        opening_army(), tuple((EntityKind.HEAVY_TANK, 51.5 + i, 53.5) for i in range(8))
    )
    simulation.advance(10)
    memory = simulation.opponents["ai"]
    squad = memory.squads[0]
    assert squad.phase is SquadPhase.RETREATING
    assert not any(simulation.entities[key].pursue_target for key in squad.entity_ids)
    assert any(simulation.entities[key].move_target is not None for key in squad.entity_ids)
    assert simulation.command_history == ()
    document = {
        "schema": MATCH_SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": simulation.export_state(),
    }
    restored = load_simulation_data(document)
    simulation.advance(20)
    restored.advance(20)
    assert len(memory.squads) == 1
    assert memory.squads[0].phase is SquadPhase.RETREATING
    assert simulation.export_state() == restored.export_state()
    replay = run_replay(load_replay_data(capture_replay(simulation).to_dict()))
    assert replay.export_state() == simulation.export_state()


def test_reserve_intercepts_a_small_raid_without_recalling_the_main_force() -> None:
    simulation = opponent_scenario(opening_army(12), ((EntityKind.SCOUT, 68.5, 62.5),))
    simulation.advance(10)
    memory = simulation.opponents["ai"]
    assert memory.reserve.phase is SquadPhase.DEFENDING
    assert all(
        simulation.entities[key].attack_target_id == "enemy_0000"
        for key in memory.reserve.entity_ids
    )
    assert memory.squads[0].phase is not SquadPhase.DEFENDING


def test_main_force_returns_for_a_raid_that_overwhelms_the_reserve() -> None:
    simulation = opponent_scenario(
        tuple((EntityKind.LIGHT_TANK, 25.5 + i, 35.5) for i in range(12)),
        tuple((EntityKind.HEAVY_TANK, 57.5 + i, 70.5) for i in range(3)),
    )
    simulation.advance(10)
    squad = simulation.opponents["ai"].squads[0]
    assert squad.phase is SquadPhase.DEFENDING
    assert squad.target_id is not None and squad.target_id.startswith("enemy_")
    assert all(simulation.entities[key].pursue_target for key in squad.entity_ids)


def test_lost_factory_and_income_rebuilds_income_before_unaffordable_factory() -> None:
    simulation = opponent_scenario()
    simulation.remove_entity("ai_factory")
    simulation.remove_entity("ai_income")
    simulation.resources["ai"] = 250
    simulation.advance(20)
    assert any(
        job.owner_id == "ai"
        and isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.RESOURCE_GENERATOR
        for job in simulation.live_automations
    )


def test_expansion_avoids_enemy_control_and_does_not_duplicate_blueprints() -> None:
    simulation = opponent_scenario(enemies=((EntityKind.HEAVY_TANK, 33.5, 65.5),))
    simulation.advance(20)
    jobs = [
        job.parameters
        for job in simulation.live_automations
        if isinstance(job.parameters, ConstructionParameters)
    ]
    assert jobs and jobs[0].position == Point(52, 66)
    simulation.advance(20)
    jobs = [
        job.parameters
        for job in simulation.live_automations
        if isinstance(job.parameters, ConstructionParameters)
    ]
    assert len({job.position for job in jobs}) == len(jobs)
    assert all(job.position != Point(32, 66) for job in jobs)


def test_production_mixes_units_and_respects_a_manual_pause() -> None:
    simulation = opponent_scenario(opening_army())
    simulation.advance(25)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.SCOUT
        for job in simulation.automations.values()
    )
    simulation.advance(60)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.HEAVY_TANK
        for job in simulation.automations.values()
    )
    paused = opponent_scenario(opening_army())
    result = paused.execute(
        CreateProductionCommand(
            "ai_factory", EntityKind.LIGHT_TANK, 1, owner_id="ai", continuous=True
        )
    )
    key = result.automation_id or ""
    assert paused.execute(PauseAutomationCommand(key, owner_id="ai")).accepted
    paused.advance(40)
    assert paused.automations[key].status.value == "paused"
    assert all(
        unit.entity_id in {spec.entity_id for spec in paused.game_map.entities}
        for unit in paused.entities.values()
        if unit.owner_id == "ai" and unit.is_movable
    )


def test_construction_budget_pauses_then_resumes_production_and_survives_save() -> None:
    simulation = opponent_scenario(
        opening_army()
        + ((EntityKind.SCOUT, 52.5, 60.5),)
        + tuple((EntityKind.HEAVY_TANK, 52.5 + i, 62.5) for i in range(3)),
        resource_sites=((66, 66), (32, 66)),
    )
    production = simulation.execute(
        CreateProductionCommand(
            "ai_factory", EntityKind.LIGHT_TANK, 1, owner_id="ai", continuous=True
        )
    )
    assert production.accepted
    assert simulation.execute(
        CreateConstructionCommand(
            "ai_builder", EntityKind.RESOURCE_GENERATOR, Point(32, 66), owner_id="ai"
        )
    ).accepted
    simulation.resources["ai"] = 200
    simulation.advance(20)
    assert simulation.opponents["ai"].budget_paused_ids
    document = {
        "schema": MATCH_SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": simulation.export_state(),
    }
    restored = load_simulation_data(document)
    simulation.advance(130)
    restored.advance(130)
    assert simulation.export_state() == restored.export_state()
    assert any(
        transition.previous is AutomationStatus.PAUSED
        and transition.current in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}
        and transition.tick > 20
        for transition in simulation.automations[production.automation_id or ""].transition_history
    )
    assert any(
        unit.kind is EntityKind.RESOURCE_GENERATOR
        and unit.position == Point(32, 66)
        and unit.is_complete
        for unit in simulation.entities.values()
    )


def test_wounded_units_use_repair_and_are_excluded_from_attack_orders() -> None:
    simulation = opponent_scenario(opening_army())
    unit = simulation.entities["army_0000"]
    unit.health = 20
    simulation.advance(10)
    assignment = simulation.automations[simulation.assignments[unit.entity_id]]
    assert isinstance(assignment.parameters, RepairParameters)
    assert all(
        unit.entity_id not in squad.entity_ids for squad in simulation.opponents["ai"].squads
    )
    assert not unit.pursue_target


def test_remote_extraction_gets_a_turret_and_forward_factory() -> None:
    simulation = opponent_scenario(
        resource_sites=((66, 66), (32, 66)),
        outposts=((EntityKind.RESOURCE_GENERATOR, 32, 66),),
    )
    simulation.resources["ai"] = 2500
    simulation.advance(20)
    jobs = [
        job.parameters
        for job in simulation.live_automations
        if isinstance(job.parameters, ConstructionParameters)
    ]
    assert any(
        job.building_kind is EntityKind.TURRET and job.position.distance_to(Point(32, 66)) < 14
        for job in jobs
    )
    simulation.advance(140)
    assert any(
        unit.kind is EntityKind.TURRET
        and unit.is_complete
        and unit.position.distance_to(Point(32, 66)) < 14
        for unit in simulation.entities.values()
    )
    assert any(
        isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.FACTORY
        and job.parameters.position.distance_to(Point(32, 66)) < 14
        for job in simulation.automations.values()
    )


def test_exhausted_resource_sites_reserve_funds_for_synthesis() -> None:
    simulation = opponent_scenario(resource_sites=((66, 66),))
    simulation.advance(160)
    assert any(
        unit.kind is EntityKind.MATTER_SYNTHESIZER and unit.is_complete
        for unit in simulation.entities.values()
    )
    assert any(
        job.title == "AI expansion"
        and isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.MATTER_SYNTHESIZER
        for job in simulation.automations.values()
    )


def test_factory_search_reaches_beyond_an_extractor_and_its_defense() -> None:
    simulation = opponent_scenario(
        resource_sites=((66, 66), (32, 66)),
        outposts=((EntityKind.RESOURCE_GENERATOR, 32, 66), (EntityKind.TURRET, 33, 61)),
    )
    simulation.resources["ai"] = 2500
    simulation.advance(20)
    assert any(
        isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.FACTORY
        and job.parameters.position.distance_to(Point(32, 66)) < 14
        for job in simulation.live_automations
    )


def test_passive_remnants_on_opposing_teams_draw() -> None:
    simulation = opponent_scenario(
        enemies=((EntityKind.RESOURCE_GENERATOR, 10, 12),),
    )
    for key in ("ai_factory", "ai_builder", "player_factory"):
        simulation.remove_entity(key)
    simulation.advance()
    assert simulation.match_result == 0


def test_attack_selection_skips_a_valuable_but_unreachable_factory() -> None:
    data = opponent_scenario(
        opening_army(),
        ((EntityKind.FACTORY, 32, 50), (EntityKind.MATTER_SYNTHESIZER, 70, 30)),
    ).game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    simulation = Simulation(load_map_data(data))
    simulation.advance(10)
    squad = simulation.opponents["ai"].squads[0]
    assert squad.target_id == "enemy_0001"
    assert all(simulation.entities[key].pursue_target for key in squad.entity_ids)


def test_command_center_replaces_a_builder_and_rebuilds_a_lost_factory() -> None:
    simulation = opponent_scenario(outposts=((EntityKind.COMMAND_CENTER, 70, 40),))
    simulation.remove_entity("ai_factory")
    simulation.remove_entity("ai_builder")
    simulation.advance(180)
    assert any(
        unit.owner_id == "ai" and unit.kind is EntityKind.BUILDER
        for unit in simulation.entities.values()
    )
    assert any(
        unit.owner_id == "ai" and unit.kind is EntityKind.FACTORY and unit.is_complete
        for unit in simulation.entities.values()
    )


def test_siege_production_adds_artillery_against_fixed_defenses() -> None:
    simulation = opponent_scenario(opening_army(), ((EntityKind.TURRET, 30, 30),))
    simulation.advance(100)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.ARTILLERY
        for job in simulation.automations.values()
    )


def test_squad_retargets_after_a_kill_without_returning_home() -> None:
    simulation = opponent_scenario(
        opening_army(),
        ((EntityKind.RESOURCE_GENERATOR, 32, 30),),
        resource_sites=((66, 66), (32, 30)),
    )
    simulation.advance(10)
    squad = simulation.opponents["ai"].squads[0]
    assert squad.target_id == "enemy_0000"
    simulation.remove_entity("enemy_0000")
    simulation.advance(20)
    assert squad.phase is SquadPhase.ADVANCING
    assert squad.target_id == "player_factory"
    assert all(simulation.entities[key].pursue_target for key in squad.entity_ids)


def test_last_scout_clears_remnants_and_wins_without_production() -> None:
    simulation = opponent_scenario(
        ((EntityKind.SCOUT, 30.5, 35.5),), ((EntityKind.MATTER_SYNTHESIZER, 30, 30),)
    )
    for key in ("ai_factory", "ai_builder", "ai_income", "player_factory"):
        simulation.remove_entity(key)
    assert owner_status(simulation, "ai") == "eliminated"
    simulation.advance(10)
    assert simulation.match_result is None
    assert simulation.entities["army_0000"].pursue_target
    assert not simulation.opponents["ai"].reserve.entity_ids
    simulation.advance(850)
    assert "enemy_0000" not in simulation.entities
    assert simulation.match_result == 2


@pytest.mark.parametrize(
    "corruption", ["owner", "duplicate", "target", "phase", "future", "destination", "missing"]
)
def test_opponent_memory_rejects_malformed_saves(corruption: str) -> None:
    simulation = opponent_scenario(opening_army())
    simulation.advance(10)
    state = deepcopy(simulation.export_state())
    memory = state["opponents"]["ai"]
    squad = memory["squads"][0]
    if corruption == "owner":
        squad["entity_ids"] = ["ai_builder"]
    elif corruption == "duplicate":
        memory["reserve"]["entity_ids"] = [squad["entity_ids"][0]]
    elif corruption == "target":
        squad["target_id"] = "ai_factory"
    elif corruption == "phase":
        squad["phase"] = "teleport"
    elif corruption == "future":
        squad["phase_tick"] = 1000
    elif corruption == "destination":
        squad["destination"] = [999, 999]
    else:
        state.pop("opponents")
    with pytest.raises(PersistenceError):
        load_simulation_data(
            {"schema": MATCH_SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )
