"""Factory deployment survives empty queues, construction, and current-format restoration."""

from pathlib import Path

import pygame
import pytest
from tests.integration.scenarios import builder_factory_simulation

from airts.adapters.persistence import (
    SAVE_SCHEMA,
    PersistenceError,
    load_simulation,
    load_simulation_data,
    save_simulation,
)
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import (
    ConstructionParameters,
    DefendParameters,
    ProductionParameters,
)
from airts.contracts.commands import (
    CancelAutomationCommand,
    CreateConstructionCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    ModifyAutomationCommand,
    PauseAutomationCommand,
    SetDeveloperModeCommand,
    SetProductionDefenseCommand,
)
from airts.contracts.spatial import Point, PointTarget, PolylineTarget, rectangle_region
from airts.presentation.app import AirtsApp
from airts.presentation.ui import selection
from airts.simulation import Simulation
from airts.world.units.catalog import EntityKind


@pytest.fixture
def unfinished_factory_world() -> tuple[Simulation, str]:
    world = Simulation(builder_factory_simulation().game_map)
    assert world.execute(SetDeveloperModeCommand(True)).accepted
    world.advance(20)
    assert world.execute(SetDeveloperModeCommand(False)).accepted
    result = world.execute(CreateConstructionCommand("builder", EntityKind.FACTORY, Point(12, 10)))
    assert result.accepted
    parameters = world.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters)
    for _ in range(80):
        if parameters.site_entity_id is not None:
            break
        world.advance()
    factory_id = parameters.site_entity_id
    assert factory_id is not None
    assert not world.entities[factory_id].is_complete
    return world, factory_id


def test_double_click_mixed_factories_can_save_deployment_without_changing_production(
    unfinished_factory_world: tuple[Simulation, str],
) -> None:
    world, unfinished = unfinished_factory_world
    loop = world.execute(
        CreateProductionCommand("factory", EntityKind.ARTILLERY, 1, continuous=True)
    )
    assert loop.accepted
    app = AirtsApp(world)
    point = world.entities["factory"].selection_position
    app._select_entities(point, point)
    app._select_entities(point, point)
    assert app.selected_entities == {"factory", "factory_2", unfinished}
    app.active_target = rectangle_region(Point(3, 15), Point(9, 21))
    app.automation_panel.new_area_auto_repair = True
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 18)
    surface = pygame.Surface(app.window_size)
    app._draw_command_bar(surface)
    button = next(rect for rect, action in app._command_buttons if action == "produce")
    balance = world.resources["player"]
    jobs = {key: world.production_queue(key) for key in app.selected_entities}
    app._handle_command_click(button.center)

    assert "3 factories" in app.notice
    assert world.resources["player"] == balance
    for key in app.selected_entities:
        deployment = world.entities[key].production_deployment
        assert deployment is not None
        assert deployment.target == app.active_target and deployment.auto_repair
        assert world.production_queue(key) == jobs[key]
    parameters = world.automations[loop.automation_id or ""].parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.unit_kind is EntityKind.ARTILLERY and parameters.progress_ticks == 0
    assert parameters.defend_target == app.active_target
    selection.draw_selection(app, surface)
    assert any(action == "clear_production_defense" for _, action in app._command_buttons)


@pytest.mark.parametrize("line", [False, True])
def test_unfinished_and_idle_factories_restore_deployment_and_inherit_it_in_later_queues(
    unfinished_factory_world: tuple[Simulation, str], tmp_path: Path, line: bool
) -> None:
    world, unfinished = unfinished_factory_world
    target = (
        PolylineTarget((Point(3, 17), Point(20, 17)))
        if line
        else rectangle_region(Point(3, 15), Point(9, 21))
    )
    assert world.execute(
        SetProductionDefenseCommand(("factory_2", unfinished), target, auto_repair=True)
    ).accepted
    path = tmp_path / "pending-deployment.json"
    save_simulation(world, path)
    restored = load_simulation(path)
    assert restored.export_state() == world.export_state()
    for sim in (world, restored):
        sim.advance(100)
        assert sim.entities[unfinished].is_complete
        assert not sim.production_queue(unfinished)
        batch = sim.execute(
            CreateProductionBatchCommand(
                "factory_2", ((EntityKind.SCOUT, 1), (EntityKind.BUILDER, 1))
            )
        )
        loop = sim.execute(
            CreateProductionCommand(unfinished, EntityKind.LIGHT_TANK, 1, continuous=True)
        )
        assert batch.accepted and loop.accepted
        sim.advance(65)
        for result in (batch, loop):
            parameters = sim.automations[result.automation_id or ""].parameters
            assert isinstance(parameters, ProductionParameters)
            assert parameters.produced_entity_ids
            assert parameters.defend_target == target and parameters.auto_repair
            defense = sim.automations[parameters.defend_automation_id or ""]
            assert isinstance(defense.parameters, DefendParameters)
            assert defense.parameters.target == target
            assert all(
                sim.assignments[entity_id] == defense.automation_id
                for entity_id in parameters.produced_entity_ids
            )
        # Finishing a finite queue does not erase the factory's deployment intent.
        again = sim.execute(CreateProductionCommand("factory_2", EntityKind.HEAVY_TANK, 1))
        parameters = sim.automations[again.automation_id or ""].parameters
        assert isinstance(parameters, ProductionParameters)
        assert parameters.defend_target == target
    assert restored.export_state() == world.export_state()
    save_simulation(world, path)
    assert load_simulation(path).export_state() == world.export_state()
    assert run_replay(capture_replay(world)).export_state() == world.export_state()


def test_shared_controller_edits_pause_and_cancellation_govern_idle_factory_defaults() -> None:
    world = builder_factory_simulation()
    first = rectangle_region(Point(3, 14), Point(9, 20))
    second = rectangle_region(Point(12, 14), Point(19, 20))
    assert world.execute(SetProductionDefenseCommand(("factory", "factory_2"), first)).accepted
    job = world.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    world.advance(EntityKind.SCOUT.profile.production_ticks)
    parameters = world.automations[job.automation_id or ""].parameters
    assert isinstance(parameters, ProductionParameters)
    defense_id = parameters.defend_automation_id or ""
    assert world.execute(
        ModifyAutomationCommand(defense_id, target=second, auto_repair=True)
    ).accepted
    assert world.execute(PauseAutomationCommand(defense_id)).accepted
    job = world.execute(CreateProductionCommand("factory_2", EntityKind.SCOUT, 1))
    world.advance(EntityKind.SCOUT.profile.production_ticks)
    parameters = world.automations[job.automation_id or ""].parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.defend_automation_id == defense_id
    assert parameters.defend_target == second and parameters.auto_repair
    for key in ("factory", "factory_2"):
        deployment = world.entities[key].production_deployment
        assert deployment is not None and deployment.target == second and deployment.auto_repair
    assert world.execute(CancelAutomationCommand(defense_id)).accepted
    assert all(
        world.entities[key].production_deployment is None for key in ("factory", "factory_2")
    )
    job = world.execute(CreateProductionCommand("factory_2", EntityKind.SCOUT, 1))
    parameters = world.automations[job.automation_id or ""].parameters
    assert isinstance(parameters, ProductionParameters) and parameters.defend_target is None


def test_clear_deployment_preserves_existing_defenders_and_queued_production_progress() -> None:
    world = builder_factory_simulation()
    target = rectangle_region(Point(3, 14), Point(9, 20))
    assert world.execute(SetProductionDefenseCommand(("factory",), target)).accepted
    job = world.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1, continuous=True))
    world.advance(EntityKind.SCOUT.profile.production_ticks + 3)
    parameters = world.automations[job.automation_id or ""].parameters
    assert isinstance(parameters, ProductionParameters)
    old_defense = parameters.defend_automation_id
    existing = parameters.produced_entity_ids[0]
    balance = world.resources["player"]
    assert world.execute(SetProductionDefenseCommand(("factory",), None)).accepted
    assert parameters.progress_ticks == 3 and parameters.cost_paid
    assert world.resources["player"] == balance
    assert world.assignments[existing] == old_defense
    world.advance(EntityKind.SCOUT.profile.production_ticks - 3)
    assert parameters.produced_count == 2
    assert parameters.produced_entity_ids[-1] not in world.assignments


@pytest.mark.parametrize("case", ["missing", "unowned", "not_factory", "point", "outside"])
def test_invalid_deployment_rejects_atomically_without_changing_existing_defaults(
    case: str,
) -> None:
    world = builder_factory_simulation()
    target = rectangle_region(Point(3, 14), Point(9, 20))
    assert world.execute(SetProductionDefenseCommand(("factory",), target)).accepted
    keys = ("factory", "missing" if case == "missing" else "builder")
    owner = "enemy" if case == "unowned" else "player"
    if case not in {"missing", "not_factory"}:
        keys = ("factory",)
    invalid_target = (
        PointTarget(Point(6, 6))
        if case == "point"
        else rectangle_region(Point(23, 23), Point(28, 28))
        if case == "outside"
        else target
    )
    before = {key: entity.to_dict() for key, entity in world.entities.items()}
    assert not world.execute(SetProductionDefenseCommand(keys, invalid_target, owner)).accepted
    assert {key: entity.to_dict() for key, entity in world.entities.items()} == before


@pytest.mark.parametrize("case", ["missing", "target", "kind", "auto_repair", "link"])
def test_saved_deployments_reject_malformed_state(case: str) -> None:
    world = builder_factory_simulation()
    assert world.execute(
        SetProductionDefenseCommand(("factory",), rectangle_region(Point(3, 14), Point(9, 20)))
    ).accepted
    state = world.export_state()
    entity = state["entities"]["factory"]  # type: ignore[index]
    deployment = entity["production_deployment"]
    if case == "missing":
        del entity["production_deployment"]
    elif case == "target":
        deployment["target"] = {"kind": "point", "point": [3, 3]}
    elif case == "kind":
        state["entities"]["builder"]["production_deployment"] = deployment  # type: ignore[index]
    elif case == "auto_repair":
        deployment["auto_repair"] = 1
    else:
        deployment["defend_automation_id"] = "missing"
    with pytest.raises(PersistenceError, match="production|deployment"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": world.game_map.to_dict(), "state": state}
        )
