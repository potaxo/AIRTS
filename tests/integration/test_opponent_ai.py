"""Opponent behavior through the public facade, including save/replay memory."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace

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
    AttackCommand,
    CreateConstructionCommand,
    CreateProductionCommand,
    MoveCommand,
    PauseAutomationCommand,
    SetAIIncomeMultiplierCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems import economy, opponent_ai
from airts.systems.opponent_ai import engagement_margin
from airts.systems.skirmish import owner_status
from airts.systems.spawning import find_spawn_point
from airts.world.map import footprint_cells, load_map_data
from airts.world.opponents import Squad, SquadPhase
from airts.world.units import catalog
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
    ready = opponent_scenario(opening_army(24))
    ready.advance(10)
    squad = ready.opponents["ai"].squads[0]
    assert squad.phase is SquadPhase.ADVANCING
    assert squad.target_id == "player_factory"
    issued = squad.last_order_tick
    ready.advance(10)
    assert squad.target_id == "player_factory"
    assert squad.last_order_tick == issued
    assert len(squad.entity_ids) >= opponent_ai.ATTACK_WAVE_SIZE
    ready.advance(400)
    assert "player_factory" not in ready.entities
    assert ready.match_result == 2


def test_base_rescue_overrides_an_unfavorable_forecast_and_survives_save() -> None:
    simulation = opponent_scenario(
        opening_army(), tuple((EntityKind.HEAVY_TANK, 51.5 + i, 53.5) for i in range(8))
    )
    simulation.advance(10)
    memory = simulation.opponents["ai"]
    squad = memory.squads[0]
    assert squad.phase is SquadPhase.DEFENDING
    assert any(simulation.entities[key].pursue_target for key in squad.entity_ids)
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
    assert memory.squads[0].phase is SquadPhase.DEFENDING
    assert simulation.export_state() == restored.export_state()
    replay = run_replay(load_replay_data(capture_replay(simulation).to_dict()))
    assert replay.export_state() == simulation.export_state()


def test_reserve_intercepts_a_small_raid_without_recalling_the_main_force() -> None:
    simulation = opponent_scenario(opening_army(48), ((EntityKind.SCOUT, 68.5, 62.5),))
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
    simulation.entities["ai_income"].upgrade.tier = 2
    economy.rebuild_income(simulation)  # Restore derived totals after injecting a tier.
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
    simulation = opponent_scenario(opening_army(8))
    simulation.resources["ai"] = 5000
    simulation.advance(120)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.SCOUT
        for job in simulation.automations.values()
    )
    simulation.advance(180)
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


def composition_scenario(*, opening: bool = False) -> Simulation:
    original = opponent_scenario(
        () if opening else opening_army(8) + ((EntityKind.SCOUT, 53.5, 58.5),),
        outposts=(
            (EntityKind.BUILDER, 68.5, 64.5),
            (EntityKind.BUILDER, 70.5, 64.5),
        ),
    )
    data = original.game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    return Simulation(load_map_data(data))


def test_opening_uses_cheap_frontline_before_mixed_production() -> None:
    simulation = composition_scenario(opening=True)
    simulation.advance(260)
    combat = [
        unit
        for unit in simulation.entities.values()
        if unit.owner_id == "ai" and unit.is_movable and unit.profile.combat_support
    ]
    assert len(combat) >= 2
    assert all(unit.kind is EntityKind.LIGHT_TANK for unit in combat)


def test_surplus_builds_all_combat_roles_and_shifts_investment_to_expensive_units(
    record_property: Callable[[str, object], None],
) -> None:
    simulation = composition_scenario(opening=True)
    simulation.resources["ai"] = 10000
    simulation.advance(1800)
    combat = [
        unit
        for unit in simulation.entities.values()
        if unit.owner_id == "ai" and unit.is_movable and unit.profile.combat_support
    ]
    kinds = {unit.kind for unit in combat}
    for kind in sorted(kinds):
        record_property(kind.value, sum(unit.kind is kind for unit in combat))
    assert kinds == {
        EntityKind.SCOUT,
        EntityKind.LIGHT_TANK,
        EntityKind.HEAVY_TANK,
        EntityKind.LIGHT_ARTILLERY,
        EntityKind.TANK_DESTROYER,
        EntityKind.ARTILLERY,
    }
    light_value = sum(
        unit.profile.production_cost for unit in combat if unit.kind is EntityKind.LIGHT_TANK
    )
    expensive_value = sum(
        unit.profile.production_cost
        for unit in combat
        if unit.profile.production_cost > EntityKind.LIGHT_TANK.profile.production_cost
    )
    assert expensive_value > 2 * light_value
    assert sum(unit.kind is EntityKind.HEAVY_TANK for unit in combat) >= 6
    assert any(
        unit.owner_id == "ai" and unit.kind is EntityKind.TURRET and unit.is_complete
        for unit in simulation.entities.values()
    )
    assert simulation.resources["ai"] < 3000
    restored = load_simulation_data(
        {
            "schema": MATCH_SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    simulation.advance(100)
    restored.advance(100)
    assert restored.export_state() == simulation.export_state()


@pytest.mark.parametrize("continuous", [False, True])
def test_missing_specialist_saves_military_cash_instead_of_always_buying_light_tanks(
    continuous: bool,
) -> None:
    simulation = composition_scenario()
    simulation.resources["ai"] = 200
    loop_id = ""
    if continuous:
        result = simulation.execute(
            CreateProductionCommand(
                "ai_factory", EntityKind.LIGHT_TANK, 1, continuous=True, owner_id="ai"
            )
        )
        assert result.accepted
        loop_id = result.automation_id or ""
    simulation.advance(20)
    assert not any(
        isinstance(job.parameters, ProductionParameters)
        and not job.parameters.continuous
        and job.parameters.unit_kind is EntityKind.LIGHT_TANK
        for job in simulation.automations.values()
    )
    if continuous:
        assert simulation.automations[loop_id].status is AutomationStatus.PAUSED
    simulation.advance(600)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.HEAVY_TANK
        for job in simulation.automations.values()
    )
    assert (
        len(
            [
                unit
                for unit in simulation.entities.values()
                if unit.owner_id == "ai" and unit.profile.combat_support and unit.is_movable
            ]
        )
        > 9
    )


def test_no_income_uses_remaining_cash_instead_of_waiting_forever_for_a_specialist() -> None:
    simulation = composition_scenario()
    simulation.remove_entity("ai_income")
    simulation.resources["ai"] = EntityKind.LIGHT_TANK.profile.production_cost
    simulation.advance(20)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.LIGHT_TANK
        for job in simulation.live_automations
    )


def test_mixed_production_from_normal_starting_funds_replays() -> None:
    simulation = composition_scenario()
    simulation.advance(1200)
    initial_ids = {spec.entity_id for spec in simulation.game_map.entities}
    produced = {
        unit.kind
        for unit in simulation.entities.values()
        if unit.owner_id == "ai" and unit.entity_id not in initial_ids
    }
    assert EntityKind.HEAVY_TANK in produced
    assert EntityKind.ARTILLERY in produced or EntityKind.LIGHT_ARTILLERY in produced
    run_replay(capture_replay(simulation))


@pytest.mark.parametrize(
    "funds, expected", [(1000, EntityKind.ARTILLERY), (10000, EntityKind.HEAVY_TANK)]
)
def test_available_military_budget_changes_the_next_composition_investment(
    funds: int, expected: EntityKind
) -> None:
    simulation = opponent_scenario(
        opening_army(8)
        + (
            (EntityKind.SCOUT, 53.5, 58.5),
            (EntityKind.HEAVY_TANK, 52.5, 56.5),
            (EntityKind.HEAVY_TANK, 54.5, 56.5),
            (EntityKind.HEAVY_TANK, 56.5, 56.5),
            (EntityKind.LIGHT_ARTILLERY, 52.5, 54.5),
            (EntityKind.LIGHT_ARTILLERY, 54.5, 54.5),
            (EntityKind.TANK_DESTROYER, 56.5, 54.5),
            (EntityKind.ARTILLERY, 58.5, 54.5),
        ),
        outposts=(
            (EntityKind.BUILDER, 68.5, 64.5),
            (EntityKind.BUILDER, 70.5, 64.5),
            (EntityKind.BUILDER, 72.5, 64.5),
        ),
    )
    simulation.resources["ai"] = funds
    simulation.advance(20)
    orders = [
        job.parameters
        for job in simulation.automations.values()
        if isinstance(job.parameters, ProductionParameters)
    ]
    assert orders and orders[0].unit_kind is expected


@pytest.mark.parametrize(
    "funds, expected", [(100, EntityKind.LIGHT_TANK), (5000, EntityKind.HEAVY_TANK)]
)
def test_base_threat_adapts_production_to_affordability(funds: int, expected: EntityKind) -> None:
    simulation = opponent_scenario(
        opening_army(8) + ((EntityKind.SCOUT, 53.5, 58.5),),
        ((EntityKind.HEAVY_TANK, 68.5, 62.5),),
    )
    simulation.resources["ai"] = funds
    simulation.advance(20)
    assert any(
        isinstance(job.parameters, ProductionParameters) and job.parameters.unit_kind is expected
        for job in simulation.live_automations
    )


@pytest.mark.usefixtures("established_economy")
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


@pytest.mark.usefixtures("established_economy")
def test_remote_extraction_gets_a_turret_and_forward_factory() -> None:
    simulation = opponent_scenario(
        resource_sites=((66, 66), (32, 66)),
        outposts=((EntityKind.RESOURCE_GENERATOR, 32, 66),),
    )
    simulation.resources["ai"] = 2500
    simulation.advance(600)
    assert any(
        unit.kind is EntityKind.TURRET
        and unit.is_complete
        and unit.position.distance_to(Point(32, 66)) < 14
        for unit in simulation.entities.values()
    )
    assert any(
        unit.kind is EntityKind.FACTORY
        and unit.is_complete
        and unit.position.distance_to(Point(32, 66)) < 14
        for unit in simulation.entities.values()
    )
    assert (
        sum(
            unit.profile.can_build for unit in simulation.entities.values() if unit.owner_id == "ai"
        )
        >= 3
    )


def test_exhausted_resource_sites_reserve_funds_for_synthesis() -> None:
    original = opponent_scenario(resource_sites=((66, 66),))
    data = original.game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    simulation = Simulation(load_map_data(data))
    simulation.entities["ai_income"].upgrade.tier = 3
    economy.rebuild_income(simulation)
    simulation.resources["ai"] = 5000
    simulation.advance(1200)
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


@pytest.mark.usefixtures("established_economy")
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
        opening_army(24),
        ((EntityKind.FACTORY, 32, 50), (EntityKind.MATTER_SYNTHESIZER, 70, 30)),
    ).game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    simulation = Simulation(load_map_data(data))
    simulation.advance(10)
    squad = simulation.opponents["ai"].squads[0]
    assert squad.target_id == "enemy_0001"
    assert all(simulation.entities[key].move_target is not None for key in squad.entity_ids)


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
    simulation = opponent_scenario(
        opening_army(8) + ((EntityKind.SCOUT, 52.5, 60.5),), ((EntityKind.TURRET, 30, 30),)
    )
    simulation.resources["ai"] = 5000
    simulation.advance(200)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.ARTILLERY
        for job in simulation.automations.values()
    )


def test_squad_retargets_after_a_kill_without_returning_home() -> None:
    simulation = opponent_scenario(
        opening_army(24),
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
    assert all(simulation.entities[key].move_target is not None for key in squad.entity_ids)


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


def test_unfinished_enemy_factory_does_not_keep_a_cleanup_force_waiting_for_a_wave() -> None:
    simulation = opponent_scenario(
        ((EntityKind.SCOUT, 30.5, 35.5),),
        ((EntityKind.FACTORY, 30, 30),),
    )
    enemy = simulation.entities["enemy_0000"]
    enemy.construction_progress = 0.5
    enemy.health = enemy.profile.max_health // 2
    for key in ("ai_builder", "ai_income", "player_factory"):
        simulation.remove_entity(key)
    simulation.resources["ai"] = 0
    simulation.advance(600)
    assert enemy.entity_id not in simulation.entities
    assert simulation.match_result == 2


@pytest.mark.usefixtures("established_economy")
def test_low_cash_economy_grows_before_continuous_production_consumes_the_income() -> None:
    original = opponent_scenario(opening_army(8), resource_sites=((66, 66),))
    data = original.game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    simulation = Simulation(load_map_data(data))
    simulation.resources["ai"] = 300
    result = simulation.execute(
        CreateProductionCommand(
            "ai_factory",
            EntityKind.LIGHT_TANK,
            1,
            continuous=True,
            owner_id="ai",
        )
    )
    simulation.advance(20)
    assert simulation.opponents["ai"].growth_credit > 0
    restored = load_simulation_data(
        {
            "schema": MATCH_SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    for current in (simulation, restored):
        current.advance(3000)
        assert any(
            unit.kind is EntityKind.MATTER_SYNTHESIZER and unit.is_complete
            for unit in current.entities.values()
        )
        production = current.automations[result.automation_id or ""].parameters
        assert isinstance(production, ProductionParameters)
        assert production.produced_count > 0
    assert restored.export_state() == simulation.export_state()


def test_gathered_army_clears_factory_exits_when_it_cannot_attack() -> None:
    ring = [(x, y) for y in (59, 64) for x in range(60, 64)] + [
        (x, y) for x in (59, 64) for y in range(60, 64)
    ]
    simulation = opponent_scenario(
        tuple((EntityKind.LIGHT_TANK, x + 0.5, y + 0.5) for x, y in ring),
        tuple((EntityKind.HEAVY_TANK, 25.5 + i % 6, 44.5 + i // 6) for i in range(36)),
        resource_sites=((66, 66),),
    )
    factory = simulation.entities["player_factory"]
    factory.position = Point(32, 52)
    simulation.occupancy.move(factory.entity_id, factory.occupied_cells)
    simulation.resources["ai"] = 3000
    result = simulation.execute(
        CreateProductionCommand(
            "ai_factory", EntityKind.LIGHT_TANK, 1, continuous=True, owner_id="ai"
        )
    )
    simulation.advance(8)
    # Certify the initial obstruction using the same physical spawn validator.
    assert (
        find_spawn_point(simulation, simulation.entities["ai_factory"], EntityKind.LIGHT_TANK)
        is None
    )
    initial = {
        key: unit.position for key, unit in simulation.entities.items() if key.startswith("army_")
    }
    simulation.advance(240)
    assert any(simulation.entities[key].position != position for key, position in initial.items())
    production = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(production, ProductionParameters)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.factory_id == "ai_factory"
        and job.parameters.produced_count > 0
        for job in simulation.automations.values()
    )
    assert not any(
        job.reason_code == "SPAWN_BLOCKED" for job in simulation.production_queue("ai_factory")
    )
    assert production.rally_point is None  # Existing caller intent is retained.
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.title == "AI production"
        and job.parameters.rally_point is not None
        for job in simulation.automations.values()
    )


@pytest.mark.parametrize(
    "production_ticks, expected", [(40, EntityKind.MATTER_SYNTHESIZER), (250, EntityKind.FACTORY)]
)
def test_factory_capacity_planning_reads_the_current_unit_production_time(
    monkeypatch: pytest.MonkeyPatch, production_ticks: int, expected: EntityKind
) -> None:
    monkeypatch.setitem(
        catalog._ENTITY_PROFILES,
        EntityKind.LIGHT_TANK,
        replace(EntityKind.LIGHT_TANK.profile, production_ticks=production_ticks),
    )
    simulation = opponent_scenario(
        resource_sites=((66, 66),),
        outposts=(
            (EntityKind.FACTORY, 50, 50),
            (EntityKind.TURRET, 46, 56),
            (EntityKind.TURRET, 62, 66),
        ),
    )
    # Isolate ordinary income/capacity planning from the surplus land-grab policy.
    simulation.resources["ai"] = 3000
    simulation.entities["ai_income"].upgrade.tier = 3
    economy.rebuild_income(simulation)
    simulation.advance(20)
    plans = [
        job.parameters
        for job in simulation.live_automations
        if isinstance(job.parameters, ConstructionParameters)
    ]
    assert plans and plans[0].building_kind is expected
    for plan in plans:
        cells = footprint_cells(plan.building_kind, plan.position)
        assert not cells.intersection({(x, y) for y in range(59, 65) for x in range(59, 65)})
        assert plan.building_kind is EntityKind.RESOURCE_GENERATOR or not cells.intersection(
            footprint_cells(EntityKind.RESOURCE_GENERATOR, Point(66, 66))
        )


@pytest.mark.parametrize("nearby", [True, False])
def test_coordinated_fight_counts_only_reinforcements_already_nearby(nearby: bool) -> None:
    front = tuple((EntityKind.LIGHT_TANK, 50.5 + i % 3, 55.5 + i // 3) for i in range(6))
    backup = tuple(
        (EntityKind.LIGHT_TANK, 54.5 + i % 3, (55.5 if nearby else 25.5) + i // 3) for i in range(6)
    )
    simulation = opponent_scenario(
        front + backup, tuple((EntityKind.HEAVY_TANK, 40.5 + i, 54.5) for i in range(5))
    )
    for key in ("ai_factory", "ai_builder", "ai_income"):
        simulation.remove_entity(key)
    memory = simulation.opponents["ai"]
    memory.squads = [
        Squad(entity_ids=[f"army_{i:04d}" for i in range(6)]),
        Squad(entity_ids=[f"army_{i:04d}" for i in range(6, 12)]),
    ]
    own = [simulation.entities[key] for key in memory.squads[0].entity_ids]
    enemies = [unit for key, unit in simulation.entities.items() if key.startswith("enemy_")]
    assert engagement_margin(own, enemies) < -0.2
    simulation.advance(10)
    assert (memory.squads[0].phase is SquadPhase.RETREATING) is not nearby


def test_new_units_wait_as_a_reinforcement_batch_instead_of_trickling_into_the_army() -> None:
    simulation = opponent_scenario(opening_army(24))
    simulation.advance(10)
    original = simulation.opponents["ai"].squads[0]
    original_ids = set(original.entity_ids)
    assert original.phase is SquadPhase.ADVANCING
    simulation.advance(180)
    assert set(original.entity_ids) == original_ids
    reinforcements = [
        squad
        for squad in simulation.opponents["ai"].squads
        if any(key not in original_ids for key in squad.entity_ids)
    ]
    assert reinforcements
    for squad in reinforcements:
        if len(squad.entity_ids) < opponent_ai.REINFORCEMENT_SIZE:
            assert squad.phase is SquadPhase.GATHERING
            assert all(
                simulation.entities[key].position.distance_to(Point(62, 62)) < 24
                for key in squad.entity_ids
            )


def test_distant_factory_recruits_form_local_cohorts_without_blocking_the_larger_main_wave() -> (
    None
):
    simulation = opponent_scenario(
        opening_army(24)
        + tuple((EntityKind.LIGHT_TANK, 12.5 + i % 3, 43.5 + i // 3) for i in range(6)),
        outposts=((EntityKind.FACTORY, 12, 34),),
    )
    simulation.advance(10)
    groups = simulation.opponents["ai"].squads
    main = next(squad for squad in groups if "army_0000" in squad.entity_ids)
    distant = next(squad for squad in groups if "army_0024" in squad.entity_ids)
    assert main is not distant
    assert len(main.entity_ids) == 24
    assert len(distant.entity_ids) == 6
    assert main.phase is SquadPhase.ADVANCING
    assert main.target_id == "player_factory"
    assert distant.phase is SquadPhase.GATHERING


def test_budget_pause_survives_queued_orders_and_resumes_when_funded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    simulation = opponent_scenario(opening_army(), resource_sites=((66, 66),))
    simulation.remove_entity("ai_builder")
    loop = simulation.execute(
        CreateProductionCommand(
            "ai_factory", EntityKind.LIGHT_TANK, 1, continuous=True, owner_id="ai"
        )
    )
    key = loop.automation_id or ""
    assert simulation.execute(
        CreateProductionCommand("ai_factory", EntityKind.HEAVY_TANK, 1, owner_id="ai")
    ).accepted
    assert simulation.execute(PauseAutomationCommand(key, owner_id="ai")).accepted
    simulation.opponents["ai"].budget_paused_ids.append(key)
    with monkeypatch.context() as setup:
        setup.setattr(opponent_ai, "drive_opponents", lambda simulation: None)
        simulation.advance(EntityKind.HEAVY_TANK.profile.production_ticks + 5)
    simulation.resources["ai"] = 100
    simulation.advance(10)
    assert simulation.automations[key].status is AutomationStatus.PAUSED
    assert key in simulation.opponents["ai"].budget_paused_ids
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.BUILDER
        for job in simulation.live_automations
    )
    simulation.resources["ai"] = 2500
    simulation.advance(180)
    assert simulation.automations[key].status is not AutomationStatus.PAUSED
    assert key not in simulation.opponents["ai"].budget_paused_ids


def test_small_army_waits_and_a_timed_wave_still_has_a_minimum_size() -> None:
    simulation = opponent_scenario(opening_army(12))
    simulation.remove_entity("ai_builder")
    created = simulation.execute(
        CreateProductionCommand(
            "ai_factory",
            EntityKind.LIGHT_TANK,
            1,
            continuous=True,
            owner_id="ai",
        )
    )
    assert simulation.execute(
        PauseAutomationCommand(created.automation_id or "", owner_id="ai")
    ).accepted
    simulation.advance(880)
    assert all(squad.phase is SquadPhase.GATHERING for squad in simulation.opponents["ai"].squads)
    simulation.advance(40)
    squad = simulation.opponents["ai"].squads[0]
    assert squad.phase is SquadPhase.ADVANCING
    assert len(squad.entity_ids) == 12
    assert squad.target_id == "player_factory"


def test_multiple_cohorts_share_one_army_objective() -> None:
    army = tuple((EntityKind.LIGHT_TANK, 44.5 + i % 8, 40.5 + i // 8) for i in range(48))
    simulation = opponent_scenario(army)
    simulation.advance(10)
    groups = simulation.opponents["ai"].squads
    assert len(groups) >= 2
    assert all(squad.phase is SquadPhase.ADVANCING for squad in groups)
    assert {squad.target_id for squad in groups} == {"player_factory"}
    assert all(
        simulation.entities[key].move_target is not None
        for squad in groups
        for key in squad.entity_ids
    )


def test_two_individually_weaker_groups_both_rescue_a_threatened_base() -> None:
    own = tuple((EntityKind.LIGHT_TANK, 52.5 + i % 4, 60.5 + i // 4) for i in range(16))
    enemy = tuple((EntityKind.LIGHT_TANK, 68.5 + i % 5, 60.5 + i // 5) for i in range(10))
    simulation = opponent_scenario(own, enemy)
    memory = simulation.opponents["ai"]
    memory.squads = [
        Squad(
            entity_ids=[f"army_{i:04d}" for i in range(start, start + 8)],
            phase=SquadPhase.ADVANCING,
            target_id="player_factory",
        )
        for start in (0, 8)
    ]
    enemies = [simulation.entities[f"enemy_{i:04d}"] for i in range(10)]
    for squad in memory.squads:
        assert (
            engagement_margin([simulation.entities[key] for key in squad.entity_ids], enemies) < 0
        )
    simulation.advance(10)
    assert len(memory.squads) == 2
    assert all(squad.phase is SquadPhase.DEFENDING for squad in memory.squads)
    assert len({squad.target_id for squad in memory.squads}) == 1
    assert all(
        simulation.entities[key].pursue_target
        for squad in memory.squads
        for key in squad.entity_ids
    )


def test_leading_cohort_stops_its_old_march_to_wait_for_distant_followers() -> None:
    own = tuple(
        (EntityKind.LIGHT_TANK, x + i % 4, 40.5 + i // 4) for x in (28.5, 68.5) for i in range(20)
    )
    simulation = opponent_scenario(own)
    simulation.opponents["ai"].squads = [
        Squad(
            entity_ids=[f"army_{i:04d}" for i in range(start, start + 20)],
            phase=SquadPhase.ADVANCING,
            target_id="player_factory",
        )
        for start in (0, 20)
    ]
    for squad in simulation.opponents["ai"].squads:
        assert simulation.execute(
            MoveCommand(tuple(squad.entity_ids), Point(7, 7), owner_id="ai")
        ).accepted
    simulation.advance(10)
    leader = simulation.opponents["ai"].squads[0]
    front = [simulation.entities[key] for key in leader.entity_ids]
    center = Point(
        sum(unit.position.x for unit in front) / len(front),
        sum(unit.position.y for unit in front) / len(front),
    )
    assert all(
        unit.move_target is not None and unit.move_target.distance_to(center) < 14 for unit in front
    )
    assert leader.target_id == "player_factory"


@pytest.mark.parametrize("outmatched", [False, True])
def test_marching_army_handles_contacts_before_its_distant_objective(outmatched: bool) -> None:
    count = 6 if outmatched else 24
    own = tuple((EntityKind.LIGHT_TANK, 35.5 + i % 4, 40.5 + i // 4) for i in range(count))
    kind = EntityKind.HEAVY_TANK if outmatched else EntityKind.SCOUT
    enemy = tuple((kind, 22.5 + i, 40.5) for i in range(8 if outmatched else 2))
    simulation = opponent_scenario(own, enemy)
    simulation.opponents["ai"].squads = [
        Squad(
            entity_ids=[f"army_{i:04d}" for i in range(count)],
            phase=SquadPhase.ADVANCING,
            target_id="player_factory",
        )
    ]
    simulation.advance(10)
    squad = simulation.opponents["ai"].squads[0]
    if outmatched:
        assert squad.phase is SquadPhase.RETREATING
        assert not any(simulation.entities[key].pursue_target for key in squad.entity_ids)
    else:
        assert squad.phase is SquadPhase.ATTACKING
        assert squad.target_id is not None and squad.target_id.startswith("enemy_")
        assert all(simulation.entities[key].pursue_target for key in squad.entity_ids)


def test_funded_ai_spends_on_workers_territory_and_an_army_together() -> None:
    original = opponent_scenario()
    data = original.game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    simulation = Simulation(load_map_data(data))
    simulation.resources["ai"] = 5000
    assert simulation.execute(
        CreateConstructionCommand(
            "ai_builder",
            EntityKind.TURRET,
            Point(50, 30),
            owner_id="ai",
        )
    ).accepted
    simultaneous = 0
    for _ in range(60):
        simulation.advance(10)
        simultaneous = max(
            simultaneous,
            sum(
                isinstance(job.parameters, ConstructionParameters)
                for job in simulation.live_automations
            ),
        )
    own = [unit for unit in simulation.entities.values() if unit.owner_id == "ai"]
    assert sum(unit.profile.can_build for unit in own) >= 3
    assert sum(unit.kind is EntityKind.FACTORY and unit.is_complete for unit in own) >= 2
    assert any(unit.kind is EntityKind.TURRET and unit.is_complete for unit in own)
    assert sum(unit.is_movable and unit.profile.attack_damage > 0 for unit in own) >= 8
    assert simultaneous >= 2
    assert simulation.resources["ai"] < 2500


def wealthy_empire_scenario() -> Simulation:
    entities: list[dict[str, object]] = [
        {"id": "player_factory", "kind": "factory", "position": [4, 4], "owner": "player"},
        {"id": "center", "kind": "command_center", "position": [228, 196], "owner": "ai"},
        {"id": "income", "kind": "resource_generator", "position": [240, 196], "owner": "ai"},
    ]
    for i in range(10):
        x = 188 + i % 5 * 12
        entities.extend(
            (
                {
                    "id": f"factory_{i}",
                    "kind": "factory",
                    "position": [x, 164 + i // 5 * 12],
                    "owner": "ai",
                },
                {
                    "id": f"synth_{i}",
                    "kind": "matter_synthesizer",
                    "position": [x, 144 + i // 5 * 10],
                    "owner": "ai",
                },
            )
        )
    entities.extend(
        {"id": f"worker_{i}", "kind": "builder", "position": [185.5 + 2 * i, 190.5], "owner": "ai"}
        for i in range(8)
    )
    entities.extend(
        {
            "id": f"army_{i}",
            "kind": "light_tank",
            "position": [200.5 + i % 6 * 2, 194.5 + i // 6 * 2],
            "owner": "ai",
        }
        for i in range(24)
    )
    return Simulation(
        load_map_data(
            {
                "id": "wealthy-empire",
                "name": "Wealthy empire",
                "width": 256,
                "height": 224,
                "terrain": {"default": "grass", "rectangles": [[127, 0, 1, 224, "rock"]]},
                "resource_sites": [[240, 196], [180, 168], [148, 80]],
                "entities": entities,
                "match_slots": [
                    {"owner": "player", "controller": "player", "team": 1},
                    {"owner": "ai", "controller": "ai", "team": 2},
                ],
            }
        )
    )


def test_large_cash_surplus_densifies_and_expands_beyond_old_building_caps(
    record_property: Callable[[str, object], None],
) -> None:
    simulation = wealthy_empire_scenario()
    opening_regions = {
        (int(unit.position.x) // 32, int(unit.position.y) // 32)
        for unit in simulation.entities.values()
        if unit.owner_id == "ai" and not unit.is_movable
    }
    simulation.resources["ai"] = 100000
    assert simulation.execute(SetAIIncomeMultiplierCommand(5)).accepted
    for unit in simulation.entities.values():
        if unit.owner_id == "ai" and unit.kind.upgrades:
            unit.upgrade.tier = 3
    economy.rebuild_income(simulation)
    simultaneous = 0
    for _ in range(180):
        simulation.advance(10)
        assert simulation._opponent_command_count <= opponent_ai.MAX_ORDERS
        jobs = [
            job.parameters
            for job in simulation.live_automations
            if isinstance(job.parameters, ConstructionParameters)
        ]
        simultaneous = max(simultaneous, len(jobs))
        assert len(jobs) <= opponent_ai.MAX_CONSTRUCTION_SITES
        assert len({key for job in jobs for key in job.builder_ids}) == len(jobs)
        assert all(job.position.x >= 128 for job in jobs)
        assert simulation.resources["ai"] >= 0
    own = [unit for unit in simulation.entities.values() if unit.owner_id == "ai"]
    complete = [unit for unit in own if not unit.is_movable and unit.is_complete]
    for kind in (EntityKind.FACTORY, EntityKind.MATTER_SYNTHESIZER, EntityKind.TURRET):
        count = sum(unit.kind is kind for unit in complete)
        record_property(kind.value, count)
        assert count >= (6 if kind is EntityKind.TURRET else 12)
    workers = sum(unit.profile.can_build for unit in own)
    soldiers = sum(unit.profile.combat_support and unit.is_movable for unit in own)
    regions = {(int(unit.position.x) // 32, int(unit.position.y) // 32) for unit in complete}
    record_property("builders", workers)
    record_property("soldiers", soldiers)
    record_property("regions", len(regions))
    record_property("parallel_construction", simultaneous)
    record_property("cash", simulation.resources["ai"])
    assert workers >= 16
    assert soldiers >= 64
    assert len(regions) >= len(opening_regions) + 4
    assert len(complete) >= 3 * len(regions)
    assert any(unit.position.y <= 80 for unit in complete)
    assert any(unit.position.x < 180 for unit in complete)
    assert simultaneous > 6
    supplied = {
        job.parameters.factory_id
        for job in simulation.automations.values()
        if isinstance(job.parameters, ProductionParameters) and job.parameters.produced_count
    }
    assert len(supplied) >= 8
    document = {
        "schema": MATCH_SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": simulation.export_state(),
    }
    restored = load_simulation_data(document)
    assert restored.export_state() == simulation.export_state()
    restored.advance(60)
    assert restored.resources["ai"] >= 0
    assert any(
        isinstance(job.parameters, ConstructionParameters) and job.modified_tick >= simulation.tick
        for job in restored.automations.values()
    )


@pytest.mark.usefixtures("established_economy")
def test_income_funded_surplus_expansion_replays_without_extra_saved_memory() -> None:
    simulation = opponent_scenario(
        resource_sites=((66, 66), (52, 66), (32, 66), (10, 12), (52, 42), (68, 36)),
        outposts=(
            (EntityKind.RESOURCE_GENERATOR, 52, 66),
            (EntityKind.RESOURCE_GENERATOR, 32, 66),
            (EntityKind.COMMAND_CENTER, 68, 52),
            (EntityKind.RESOURCE_GENERATOR, 52, 42),
            (EntityKind.RESOURCE_GENERATOR, 68, 36),
        ),
    )
    data = simulation.game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    simulation = Simulation(load_map_data(data))
    assert simulation.execute(SetAIIncomeMultiplierCommand(5)).accepted
    simulation.advance(600)
    assert (
        sum(
            unit.profile.can_build for unit in simulation.entities.values() if unit.owner_id == "ai"
        )
        > 8
    )
    assert any(
        isinstance(job.parameters, ProductionParameters) and job.parameters.target_count > 3
        for job in simulation.automations.values()
    )
    run_replay(capture_replay(simulation))


def test_frontier_raid_does_not_stop_funded_workers_and_safe_rear_expansion() -> None:
    data = wealthy_empire_scenario().game_map.to_dict()
    data["entities"].extend(
        (
            {
                "id": "frontier_income",
                "kind": "resource_generator",
                "position": [148, 80],
                "owner": "ai",
            },
            {"id": "raider", "kind": "heavy_tank", "position": [139.5, 84.5], "owner": "player"},
        )
    )
    simulation = Simulation(load_map_data(data))
    simulation.resources["ai"] = 100000
    simulation.advance(40)
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.factory_id == "center"
        and job.parameters.unit_kind is EntityKind.BUILDER
        for job in simulation.live_automations
    )
    plans = [
        job.parameters
        for job in simulation.automations.values()
        if isinstance(job.parameters, ConstructionParameters)
    ]
    assert plans
    assert all(
        plan.position.x >= 128 and plan.position.distance_to(Point(139.5, 84.5)) >= 12
        for plan in plans
    )


def test_surplus_expansion_uses_extractors_when_the_map_has_unrestricted_resources() -> None:
    data = wealthy_empire_scenario().game_map.to_dict()
    data.pop("resource_sites")
    simulation = Simulation(load_map_data(data))
    simulation.resources["ai"] = 100000
    simulation.advance(200)
    assert any(
        isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.RESOURCE_GENERATOR
        and job.parameters.position not in {Point(240, 196), Point(180, 168), Point(148, 80)}
        for job in simulation.automations.values()
    )
    assert not any(
        isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.MATTER_SYNTHESIZER
        for job in simulation.automations.values()
    )


def test_visible_enemy_buildup_prompts_a_core_turret_before_more_expansion() -> None:
    simulation = opponent_scenario(
        enemies=tuple((EntityKind.LIGHT_TANK, 20.5 + i % 4, 20.5 + i // 4) for i in range(8))
    )
    simulation.advance(20)
    assert any(
        isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.TURRET
        and job.parameters.position.distance_to(Point(62, 62)) <= 14
        for job in simulation.live_automations
    )
    simulation.advance(100)
    assert any(
        unit.kind is EntityKind.TURRET and unit.is_complete for unit in simulation.entities.values()
    )


def test_defenders_assemble_under_turret_cover_before_contact() -> None:
    simulation = opponent_scenario(
        opening_army(20),
        tuple((EntityKind.LIGHT_TANK, 45.5 + i % 4, 50.5 + i // 4) for i in range(8)),
        outposts=((EntityKind.TURRET, 57, 55),),
    )
    simulation.advance(10)
    squad = simulation.opponents["ai"].squads[0]
    assert squad.phase is SquadPhase.DEFENDING
    assert not any(simulation.entities[key].pursue_target for key in squad.entity_ids)
    assert any(simulation.entities[key].move_target is not None for key in squad.entity_ids)


def test_opening_economy_can_repel_twelve_light_tanks_and_replace_a_lost_factory() -> None:
    data = opponent_scenario().game_map.to_dict()
    data["entities"].append(
        {
            "id": "player_income",
            "kind": "resource_generator",
            "owner": "player",
            "position": [10, 12],
        }
    )
    simulation = Simulation(load_map_data(data))
    assert simulation.execute(
        CreateProductionCommand("player_factory", EntityKind.LIGHT_TANK, 12)
    ).accepted
    simulation.advance(1100)
    attackers = tuple(
        unit.entity_id
        for unit in simulation.entities.values()
        if unit.owner_id == "player" and unit.is_movable
    )
    assert len(attackers) == 12
    assert simulation.execute(AttackCommand(attackers, "ai_factory")).accepted
    simulation.advance(800)
    assert not any(
        unit.owner_id == "player" and unit.is_movable for unit in simulation.entities.values()
    )
    assert any(
        unit.owner_id == "ai" and unit.kind is EntityKind.FACTORY and unit.is_complete
        for unit in simulation.entities.values()
    )


def income_upgrade_scenario(*, synthesizer: bool = False) -> Simulation:
    simulation = opponent_scenario(
        resource_sites=((66, 66),),
        outposts=((EntityKind.MATTER_SYNTHESIZER, 50, 68),) if synthesizer else (),
    )
    data = simulation.game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    return Simulation(load_map_data(data))


@pytest.mark.parametrize("kind", [EntityKind.RESOURCE_GENERATOR, EntityKind.MATTER_SYNTHESIZER])
@pytest.mark.parametrize("tier", [1, 2])
def test_ai_upgrades_income_buildings_through_configured_tiers(kind: EntityKind, tier: int) -> None:
    simulation = income_upgrade_scenario(synthesizer=True)
    # Keep the builder busy so higher income cannot prefer a new factory first.
    assert simulation.execute(
        CreateConstructionCommand("ai_builder", EntityKind.TURRET, Point(50, 30), owner_id="ai")
    ).accepted
    commands = simulation.command_history
    simulation.resources["ai"] = 2000
    target = simulation.entities[
        "ai_income" if kind is EntityKind.RESOURCE_GENERATOR else "outpost_0000"
    ]
    for unit in simulation.entities.values():
        if unit.kind.upgrades:
            unit.upgrade.tier = 3
    target.upgrade.tier = tier
    economy.rebuild_income(simulation)
    definition = target.next_upgrade
    assert definition is not None
    simulation.advance(20)
    assert target.upgrade.remaining_ticks == definition.duration_ticks
    assert simulation.command_history == commands
    simulation.advance(20)
    assert (
        len(
            simulation.events.query(
                event_types=frozenset({EventType.UPGRADE_STARTED}), subject_id=target.entity_id
            )
        )
        == 1
    )
    simulation.advance(definition.duration_ticks - 20)
    assert target.upgrade.tier == tier + 1
    assert target.profile.income_per_second == definition.profile.income_per_second


@pytest.mark.parametrize("cost, upgrade_preferred", [(40, True), (1000, False)])
def test_income_investments_compare_upgrade_payback_with_expansion(
    monkeypatch: pytest.MonkeyPatch, cost: int, upgrade_preferred: bool
) -> None:
    first, second = EntityKind.RESOURCE_GENERATOR.upgrades
    monkeypatch.setitem(
        catalog._ENTITY_UPGRADES,
        EntityKind.RESOURCE_GENERATOR,
        (replace(first, cost=cost), second),
    )
    simulation = opponent_scenario()
    simulation.advance(20)
    assert bool(simulation.entities["ai_income"].upgrade.remaining_ticks) is upgrade_preferred
    investments = [
        event
        for event in simulation.events.events
        if event.event_type is EventType.UPGRADE_STARTED
        or event.event_type is EventType.AUTOMATION_CREATED
        and event.details.get("template") == "construction"
    ]
    assert investments
    assert (investments[0].event_type is EventType.UPGRADE_STARTED) is upgrade_preferred


def test_ai_saves_for_an_upgrade_without_spending_queued_unit_funds() -> None:
    simulation = income_upgrade_scenario()
    simulation.resources["ai"] = 350
    queued = simulation.execute(
        CreateProductionCommand("ai_factory", EntityKind.TANK_DESTROYER, 2, owner_id="ai")
    )
    assert queued.accepted
    simulation.advance(20)
    income = simulation.entities["ai_income"]
    assert simulation.resources["ai"] >= EntityKind.RESOURCE_GENERATOR.upgrades[0].cost
    assert income.upgrade.remaining_ticks == 0
    simulation.advance(1800)
    parameters = simulation.automations[queued.automation_id or ""].parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.produced_count == 2
    assert income.upgrade.remaining_ticks or income.upgrade.tier > 1


def test_upgrade_savings_pause_a_loop_survive_save_and_resume_when_funded() -> None:
    simulation = income_upgrade_scenario()
    simulation.resources["ai"] = 100
    created = simulation.execute(
        CreateProductionCommand(
            "ai_factory", EntityKind.LIGHT_TANK, 1, continuous=True, owner_id="ai"
        )
    )
    assert created.accepted
    key = created.automation_id or ""
    simulation.advance(20)
    assert simulation.automations[key].status is AutomationStatus.PAUSED
    assert key in simulation.opponents["ai"].budget_paused_ids
    assert simulation.entities["ai_income"].upgrade.remaining_ticks == 0
    restored = load_simulation_data(
        {
            "schema": MATCH_SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    for current in (simulation, restored):
        current.advance(1800)
        assert (
            current.entities["ai_income"].upgrade.remaining_ticks > 0
            or current.entities["ai_income"].upgrade.tier > 1
        )
        current.resources["ai"] = 500
        current.advance(20)
        assert current.automations[key].status is not AutomationStatus.PAUSED
        assert key not in current.opponents["ai"].budget_paused_ids
    assert restored.export_state() == simulation.export_state()


def test_income_upgrade_continuation_and_internal_commands_replay() -> None:
    simulation = income_upgrade_scenario()
    simulation.advance(150)
    assert simulation.entities["ai_income"].upgrade.remaining_ticks > 0
    restored = load_simulation_data(
        {
            "schema": MATCH_SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    for current in (simulation, restored):
        current.advance(650)
        assert current.entities["ai_income"].upgrade.tier >= 2
    assert restored.export_state() == simulation.export_state()
    assert not simulation.command_history
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


def test_ai_defends_instead_of_investing_under_local_pressure() -> None:
    simulation = opponent_scenario(
        enemies=((EntityKind.HEAVY_TANK, 57.5, 67.5),), resource_sites=((66, 66),)
    )
    simulation.resources["ai"] = 500
    simulation.advance(20)
    assert not simulation.events.query(event_types=frozenset({EventType.UPGRADE_STARTED}))
    assert any(
        isinstance(job.parameters, ProductionParameters)
        and job.parameters.unit_kind is EntityKind.LIGHT_TANK
        for job in simulation.live_automations
    )


def test_income_upgrade_skips_a_damaged_source_and_works_without_an_idle_builder() -> None:
    simulation = income_upgrade_scenario(synthesizer=True)
    simulation.entities["ai_income"].health = 100
    assert simulation.execute(
        CreateConstructionCommand("ai_builder", EntityKind.TURRET, Point(52, 64), owner_id="ai")
    ).accepted
    # The paid construction keeps the only builder busy, but upgrades need no builder.
    simulation.advance(10)
    job = next(
        job.parameters
        for job in simulation.live_automations
        if isinstance(job.parameters, ConstructionParameters)
    )
    assert job.cost_paid
    simulation.advance(10)
    assert simulation.entities["ai_income"].upgrade.remaining_ticks == 0
    assert simulation.entities["outpost_0000"].upgrade.remaining_ticks > 0


def test_ai_finishes_committed_construction_before_saving_for_an_upgrade() -> None:
    simulation = income_upgrade_scenario()
    assert simulation.execute(
        CreateConstructionCommand(
            "ai_builder", EntityKind.MATTER_SYNTHESIZER, Point(50, 68), owner_id="ai"
        )
    ).accepted
    simulation.advance(20)
    assert simulation.entities["ai_income"].upgrade.remaining_ticks == 0
    assert not simulation.events.query(event_types=frozenset({EventType.UPGRADE_STARTED}))


@pytest.mark.parametrize(
    "corruption",
    [
        "owner",
        "duplicate",
        "target",
        "phase",
        "future",
        "destination",
        "missing",
        "credit_negative",
        "credit_nan",
        "budget_future",
        "budget_missing",
    ],
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
    elif corruption == "credit_negative":
        memory["growth_credit"] = -1
    elif corruption == "credit_nan":
        memory["growth_credit"] = float("nan")
    elif corruption == "budget_future":
        memory["budget_tick"] = 1000
    elif corruption == "budget_missing":
        memory.pop("budget_tick")
    else:
        state.pop("opponents")
    with pytest.raises(PersistenceError):
        load_simulation_data(
            {"schema": MATCH_SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )
