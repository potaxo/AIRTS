"""Blueprint clearance and continuation of already paid physical construction."""

import pygame
import pytest
from tests.integration.scenarios import builder_factory_simulation

from airts.adapters.persistence import SAVE_SCHEMA, load_simulation_data
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import AutomationStatus, ConstructionParameters
from airts.contracts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    CreateConstructionCommand,
    CreateDefendCommand,
    CreatePatrolCommand,
    HoldPositionCommand,
    MoveCommand,
    PauseAutomationCommand,
    RepairBuildingCommand,
)
from airts.contracts.spatial import Point, PointTarget
from airts.presentation.app import AirtsApp
from airts.presentation.visuals.work import work_sources
from airts.simulation import Simulation
from airts.systems.construction import construction_distance
from airts.world.entities import UnitState
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


def assistance_world(*, hub: bool = False) -> Simulation:
    entities: list[dict[str, object]] = [
        {"id": "builder", "kind": "builder", "position": [9.5, 10.5]},
        {"id": "helper", "kind": "builder", "position": [9.5, 12.5]},
    ]
    if hub:
        entities.append({"id": "hub", "kind": "repair_hub", "position": [18, 10]})
    return Simulation(
        load_map_data(
            {
                "id": "construction-assistance",
                "name": "Construction assistance",
                "width": 40,
                "height": 28,
                "terrain": {"default": "grass"},
                "entities": entities,
            }
        )
    )


def document(world: Simulation) -> dict[str, object]:
    return {"schema": SAVE_SCHEMA, "map": world.game_map.to_dict(), "state": world.export_state()}


def start_scaffold(world: Simulation) -> tuple[str, ConstructionParameters]:
    result = world.execute(CreateConstructionCommand("builder", EntityKind.FACTORY, Point(12, 12)))
    assert result.accepted
    world.advance(3)
    parameters = world.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters) and parameters.site_entity_id is not None
    return result.automation_id or "", parameters


@pytest.mark.parametrize("interruption", ["move", "cancel", "destroy"])
def test_right_click_resumes_orphan_scaffold_without_repaying(interruption: str) -> None:
    world = assistance_world()
    job_id, parameters = start_scaffold(world)
    site = world.entities[parameters.site_entity_id or ""]
    balance = world.resources["player"]
    progress = site.construction_progress
    if interruption == "move":
        assert world.execute(MoveCommand(("builder",), Point(30.5, 8.5))).accepted
    elif interruption == "cancel":
        assert world.execute(CancelAutomationCommand(job_id)).accepted
    else:
        world.remove_entity("builder", "DESTROYED")
    world.advance(2)
    assert site.construction_progress == progress
    app = AirtsApp(world)
    app.selected_entities = {"helper"}
    app._selection_changed()
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 24)
    try:
        app._handle_mouse_down(3, app.screen_point(site.selection_position))
    finally:
        pygame.font.quit()
    assert world.entities["helper"].repair_target_id == site.entity_id
    assert world.command_history[-1]["command"]["type"] == "repair_building"
    world.advance(2)
    restored = load_simulation_data(document(world))
    for instance in (world, restored):
        instance.advance(30)
        assert instance.entities[site.entity_id].is_complete
        assert instance.resources["player"] == balance
        assert sum(entity.kind is EntityKind.FACTORY for entity in instance.entities.values()) == 1
    assert restored.export_state() == world.export_state()


@pytest.mark.parametrize("paused", [False, True])
def test_hub_finishes_existing_site_and_synchronizes_construction_job(paused: bool) -> None:
    world = assistance_world(hub=True)
    job_id, parameters = start_scaffold(world)
    site = world.entities[parameters.site_entity_id or ""]
    if paused:
        assert world.execute(PauseAutomationCommand(job_id)).accepted
    else:
        assert world.execute(MoveCommand(("builder",), Point(30.5, 8.5))).accepted
    site.health -= 7
    balance = world.resources["player"]
    before = site.construction_progress
    world.advance(2)
    assert site.construction_progress is not None and before is not None
    assert site.construction_progress > before
    assert world.entities["hub"].repair_target_id == site.entity_id
    assert (world.entities["hub"], site) in tuple(work_sources(world))
    restored = load_simulation_data(document(world))
    for instance in (world, restored):
        for _ in range(120):
            instance.advance()
            if instance.entities[site.entity_id].is_complete:
                break
        assert instance.entities[site.entity_id].is_complete
        assert instance.entities[site.entity_id].health == site.kind.profile.max_health - 7
        assert instance.resources["player"] == balance
        if paused:
            assert instance.automations[job_id].status is AutomationStatus.COMPLETED
    assert restored.export_state() == world.export_state()


def test_hub_can_explicitly_assist_but_cannot_create_or_materialize_blueprints() -> None:
    world = assistance_world(hub=True)
    blueprint = world.execute(
        CreateConstructionCommand("builder", EntityKind.FACTORY, Point(28, 20))
    )
    assert blueprint.accepted
    assert world.execute(PauseAutomationCommand(blueprint.automation_id or "")).accepted
    balance = world.resources["player"]
    world.advance(10)
    parameters = world.automations[blueprint.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters) and parameters.site_entity_id is None
    assert world.resources["player"] == balance
    result = world.execute(CreateConstructionCommand("hub", EntityKind.FACTORY, Point(12, 12)))
    assert not result.accepted and result.reason == "ENTITY_NOT_BUILDER"
    job_id, parameters = start_scaffold(world)
    assert world.execute(CancelAutomationCommand(job_id)).accepted
    assert world.execute(RepairBuildingCommand(("hub",), parameters.site_entity_id or "")).accepted
    assert world.entities["hub"].repair_target_id == parameters.site_entity_id
    rejected = world.execute(RepairBuildingCommand(("helper",), "blueprint-is-not-an-entity"))
    assert not rejected.accepted and rejected.reason == "UNKNOWN_TARGET"


def test_builder_and_hub_contributions_complete_one_paid_job_and_resume_its_queue() -> None:
    world = assistance_world(hub=True)
    job_id, parameters = start_scaffold(world)
    next_job = world.execute(
        CreateConstructionCommand(
            "builder", EntityKind.RESOURCE_GENERATOR, Point(26, 20), queued=True
        )
    )
    assert next_job.accepted
    assert world.execute(
        RepairBuildingCommand(("helper",), parameters.site_entity_id or "")
    ).accepted
    for _ in range(30):
        world.advance()
        if world.automations[job_id].status is AutomationStatus.COMPLETED:
            break
    assert world.automations[job_id].status is AutomationStatus.COMPLETED
    assert world.assignments["builder"] == next_job.automation_id
    assert sum(entity.kind is EntityKind.FACTORY for entity in world.entities.values()) == 1
    assert run_replay(capture_replay(world)).snapshot() == world.snapshot()


def test_queued_scaffold_assistance_survives_save_and_continues_after_movement() -> None:
    world = assistance_world()
    job_id, parameters = start_scaffold(world)
    assert world.execute(CancelAutomationCommand(job_id)).accepted
    balance = world.resources["player"]
    assert world.execute(MoveCommand(("helper",), Point(9.5, 18.5))).accepted
    assert world.execute(
        RepairBuildingCommand(("helper",), parameters.site_entity_id or "", queued=True)
    ).accepted
    restored = load_simulation_data(document(world))
    for instance in (world, restored):
        instance.advance(80)
        assert instance.entities[parameters.site_entity_id or ""].is_complete
        assert not instance.entities["helper"].order_queue
        assert instance.resources["player"] == balance
    assert restored.export_state() == world.export_state()


def relocate(world: Simulation, key: str, position: Point) -> None:
    entity = world.entities[key]
    entity.position = position
    world.occupancy.move(key, entity.occupied_cells)


def test_blueprint_moves_idle_friendlies_by_paths_then_charges_only_after_disc_clearance() -> None:
    data = builder_factory_simulation().game_map.to_dict()
    entities = data["entities"]
    assert isinstance(entities, list)
    for entity in entities:
        if entity["id"] == "scout":
            entity["position"] = [6.5, 10.5]
        elif entity["id"] == "tank":
            entity["position"] = [7.5, 11.5]
    world = Simulation(load_map_data(data))
    starts = {key: world.entities[key].position for key in ("scout", "tank")}
    balance = world.resources["player"]
    result = world.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10))
    )
    assert result.accepted
    parameters = world.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters)
    assert parameters.site_entity_id is None and not parameters.cost_paid
    assert world.resources["player"] == balance
    for key, position in starts.items():
        unit = world.entities[key]
        assert unit.position == position and unit.path
        assert unit.last_player_control_tick is None
    restored = load_simulation_data(document(world))
    for _ in range(80):
        world.advance()
        restored.advance()
        if parameters.site_entity_id is None:
            assert world.resources["player"] == balance
        else:
            for unit in world.entities.values():
                if unit.is_movable:
                    assert (
                        construction_distance(unit.position, parameters)
                        >= unit.profile.collision_radius - 1e-8
                    )
            break
    assert parameters.site_entity_id is not None
    assert world.resources["player"] == balance - EntityKind.REPAIR_HUB.profile.construction_cost
    world.advance(30)
    restored.advance(30)
    assert world.entities[parameters.site_entity_id].is_complete
    assert restored.export_state() == world.export_state()
    assert run_replay(capture_replay(world)).snapshot() == world.snapshot()


@pytest.mark.parametrize("order", ["hold", "defend", "patrol", "move", "queued_attack", "enemy"])
def test_blueprint_never_replaces_an_existing_order_or_enemy_idle_unit(order: str) -> None:
    world = builder_factory_simulation()
    relocate(world, "scout", Point(6.5, 10.5))
    if order == "hold":
        command = HoldPositionCommand(("scout",))
    elif order == "defend":
        command = CreateDefendCommand(("scout",), PointTarget(Point(6.5, 10.5)))
    elif order == "patrol":
        command = CreatePatrolCommand(("scout",), PointTarget(Point(14.5, 10.5)))
    elif order == "move":
        command = MoveCommand(("scout",), Point(14.5, 10.5))
    elif order == "queued_attack":
        world.entities["factory_2"].owner_id = "enemy"
        command = AttackCommand(("scout",), "factory_2", queued=True)
    else:
        world.entities["scout"].owner_id = "enemy"
        command = None
    if command is not None:
        assert world.execute(command).accepted
    before = world.entities["scout"].to_dict()
    assert world.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10))
    ).accepted
    assert world.entities["scout"].to_dict() == before


def test_blueprint_waits_with_diagnostics_when_no_idle_unit_can_reach_an_exit() -> None:
    world = Simulation(
        load_map_data(
            {
                "id": "sealed-blueprint",
                "name": "Sealed blueprint",
                "width": 16,
                "height": 16,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [4, 4, 5, 1, "rock"],
                        [4, 8, 5, 1, "rock"],
                        [4, 5, 1, 3, "rock"],
                        [8, 5, 1, 3, "rock"],
                    ],
                },
                "entities": [
                    {"id": "builder", "kind": "builder", "position": [9.5, 9.5]},
                    {"id": "scout", "kind": "scout", "position": [6.5, 6.5]},
                ],
            }
        )
    )
    result = world.execute(CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(5, 5)))
    assert result.accepted
    world.advance(20)
    job = world.automations[result.automation_id or ""]
    assert job.reason_code == "SITE_OCCUPIED"
    assert (
        isinstance(job.parameters, ConstructionParameters) and job.parameters.site_entity_id is None
    )
    assert world.resources["player"] == 500
    assert world.entities["scout"].state is UnitState.IDLE
    failures = [
        event
        for event in world.events.events
        if event.subject_id == "scout" and event.event_type.value == "pathfinding_failed"
    ]
    assert 1 <= len(failures) <= 2
