"""Opponent behavior through the public facade, including save/replay memory."""

from __future__ import annotations

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
    CreateConstructionCommand,
    CreateProductionCommand,
    PauseAutomationCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems import opponent_ai
from airts.systems.opponent_ai import engagement_margin
from airts.systems.production import find_spawn_point
from airts.systems.skirmish import owner_status
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
    simulation.entities["ai_income"].upgrade.tier = 2
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
    simulation.entities["ai_income"].upgrade.tier = 3
    # Synthesis is the fallback after safe sites and existing income upgrades run out.
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


@pytest.mark.usefixtures("established_economy")
def test_low_cash_economy_grows_before_continuous_production_consumes_the_income() -> None:
    simulation = opponent_scenario(
        opening_army()
        + ((EntityKind.SCOUT, 55.5, 63.5),)
        + tuple((EntityKind.HEAVY_TANK, 53.5, 62.5 + 2 * i) for i in range(2))
        + ((EntityKind.ARTILLERY, 51.5, 62.5),),
        resource_sites=((66, 66),),
    )
    data = simulation.game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[40, 0, 1, 80, "rock"]]}
    simulation = Simulation(load_map_data(data))
    simulation.resources["ai"] = 300
    result = simulation.execute(
        CreateProductionCommand(
            "ai_factory", EntityKind.LIGHT_TANK, 1, continuous=True, owner_id="ai"
        )
    )
    simulation.advance(20)
    assert any(
        isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.MATTER_SYNTHESIZER
        for job in simulation.live_automations
    )
    document = {
        "schema": MATCH_SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": simulation.export_state(),
    }
    restored = load_simulation_data(document)
    simulation.advance(1000)
    restored.advance(1000)
    assert restored.export_state() == simulation.export_state()
    assert any(
        unit.owner_id == "ai" and unit.kind is EntityKind.MATTER_SYNTHESIZER and unit.is_complete
        for unit in simulation.entities.values()
    )
    production = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(production, ProductionParameters)
    assert production.produced_count > 0


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
    simulation = opponent_scenario(resource_sites=((66, 66),))
    simulation.entities["ai_income"].upgrade.tier = 3
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


def test_new_production_reinforces_an_existing_advancing_squad() -> None:
    simulation = opponent_scenario(opening_army())
    simulation.advance(10)
    original = simulation.opponents["ai"].squads[0]
    assert original.phase is SquadPhase.ADVANCING
    simulation.advance(150)
    assert len(simulation.opponents["ai"].squads) == 1
    assert any(
        key not in {spec.entity_id for spec in simulation.game_map.entities}
        for key in original.entity_ids
    )


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
    target = simulation.entities[
        "ai_income" if kind is EntityKind.RESOURCE_GENERATOR else "outpost_0000"
    ]
    for unit in simulation.entities.values():
        if unit.kind.upgrades:
            unit.upgrade.tier = 3
    target.upgrade.tier = tier
    definition = target.next_upgrade
    assert definition is not None
    simulation.advance(20)
    assert target.upgrade.remaining_ticks == definition.duration_ticks
    assert not simulation.command_history
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


@pytest.mark.parametrize("cost, upgrade_preferred", [(120, True), (1000, False)])
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
    expanded = any(
        isinstance(job.parameters, ConstructionParameters)
        and job.parameters.building_kind is EntityKind.RESOURCE_GENERATOR
        for job in simulation.live_automations
    )
    assert expanded is not upgrade_preferred


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
    simulation.advance(700)
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
        current.advance(680)
        assert current.entities["ai_income"].upgrade.remaining_ticks > 0
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
