"""Factory queues, costs, loops, and linked defense through commands and UI."""

from __future__ import annotations

import pygame
import pytest
from tests.integration.scenarios import (
    builder_factory_simulation,
    economy_combat_simulation,
    interaction_simulation,
    runtime_map,
)

from airts.contracts.automations import (
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    ProductionParameters,
)
from airts.contracts.commands import (
    CancelAutomationCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    CreateSpatialReferenceCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
    StopCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PolylineTarget, rectangle_region
from airts.navigation.collision import collision_radius
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.world.map import EntityKind, load_map_data


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        pytest.param({"minimum_units": 2}, "MINIMUM_UNITS_NOT_EDITABLE", id="wrong-template"),
        pytest.param({"target_count": 0}, "TARGET_COUNT_BELOW_PRODUCED", id="invalid-count"),
    ],
)
def test_rejected_production_retarget_preserves_linked_work(
    changes: dict[str, int], reason: str
) -> None:
    simulation = Simulation(runtime_map())
    created = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.SCOUT,
            1,
            continuous=True,
            defend_target=rectangle_region(Point(14, 2), Point(18, 6)),
        )
    )
    simulation.advance(5)
    before = simulation.snapshot()

    result = simulation.execute(
        ModifyAutomationCommand(
            created.automation_id or "",
            title="Rejected",
            priority=10,
            target=rectangle_region(Point(18, 2), Point(22, 6)),
            **changes,
        )
    )

    assert not result.accepted
    assert result.reason == reason
    assert simulation.snapshot() == before
    assert not simulation.events.query(event_types=frozenset({EventType.AUTOMATION_MODIFIED}))


def _production_ids(simulation: Simulation, automation_id: str) -> list[str]:
    parameters = simulation.automations[automation_id].parameters
    assert isinstance(parameters, ProductionParameters)
    return parameters.produced_entity_ids


def test_finite_production_completes_with_deterministic_ids_and_releases_factory() -> None:
    simulation = Simulation(runtime_map())
    created = simulation.execute(
        CreateProductionCommand("factory", EntityKind.SCOUT, 2, Point(14.5, 5.5))
    )
    automation_id = created.automation_id or ""

    simulation.advance(20)

    automation = simulation.automations[automation_id]
    assert automation.status is AutomationStatus.COMPLETED
    assert isinstance(automation.parameters, ProductionParameters)
    assert automation.parameters.produced_entity_ids == ["scout_001", "scout_002"]
    assert all(entity_id in simulation.entities for entity_id in ("scout_001", "scout_002"))
    assert "factory" not in simulation.assignments


def test_continuous_factory_production_assigns_units_to_compact_area_defense() -> None:
    simulation = Simulation(runtime_map())
    simulation.resources["player"] = 10_000
    defend_target = rectangle_region(Point(14, 2), Point(22, 7))
    created = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.LIGHT_TANK,
            1,
            continuous=True,
            defend_target=defend_target,
        )
    )

    simulation.advance(16)

    production = simulation.automations[created.automation_id or ""]
    assert isinstance(production.parameters, ProductionParameters)
    parameters = production.parameters
    assert production.status is AutomationStatus.ACTIVE
    assert parameters.continuous
    assert parameters.produced_count == 3
    defend = simulation.automations[parameters.defend_automation_id or ""]
    assert defend.kind is AutomationKind.DEFEND
    assert isinstance(defend.parameters, DefendParameters)
    assert defend.parameters.gathering_point
    assert len(defend.parameters.deployment_slots) == 3
    assert len(set(defend.parameters.stations.values())) == 3
    assert defend.entity_ids == parameters.produced_entity_ids
    assert all(
        simulation.assignments[entity_id] == defend.automation_id
        for entity_id in parameters.produced_entity_ids
    )
    assert simulation.navigation_field_build_count <= len(defend.parameters.deployment_slots)

    simulation.advance(10)
    assert parameters.produced_count == 5
    assert production.status is AutomationStatus.ACTIVE

    for index, entity_id in enumerate(parameters.produced_entity_ids):
        entity = simulation.entities[entity_id]
        simulation.occupancy.move(entity_id, frozenset({(index, 14)}))
        entity.position = Point(index + 0.5, 14.5)
        entity.path.clear()
        entity.move_target = None
    field_builds = simulation.navigation_field_build_count

    simulation.advance()

    dispatched = [
        entity_id
        for entity_id in parameters.produced_entity_ids
        if simulation.entities[entity_id].path
    ]
    assert len(dispatched) == Simulation.GATHERING_PATH_BUDGET
    assert (
        simulation.navigation_field_build_count - field_builds <= Simulation.GATHERING_PATH_BUDGET
    )


def test_factory_keeps_only_latest_continuous_production_request() -> None:
    simulation = Simulation(runtime_map())
    simulation.resources["player"] = 10_000
    first_target = rectangle_region(Point(14, 2), Point(18, 6))
    second_target = rectangle_region(Point(18, 2), Point(22, 6))
    first = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.SCOUT,
            1,
            continuous=True,
            defend_target=first_target,
        )
    )
    simulation.advance(5)
    first_automation = simulation.automations[first.automation_id or ""]
    assert isinstance(first_automation.parameters, ProductionParameters)
    first_defend = simulation.automations[first_automation.parameters.defend_automation_id or ""]

    second = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.LIGHT_TANK,
            1,
            continuous=True,
            defend_target=second_target,
        )
    )

    second_id = second.automation_id or ""
    assert first_automation.status is AutomationStatus.CANCELED
    assert first_automation.reason_code == "SUPERSEDED_BY_LATEST_CONTINUOUS_PRODUCTION"
    assert first_defend.status is AutomationStatus.ACTIVE
    assert first_defend.entity_ids == ["scout_001"]
    assert [item.automation_id for item in simulation.production_queue("factory")] == [second_id]
    assert simulation.assignments["factory"] == second_id

    simulation.advance(5)
    second_parameters = simulation.automations[second_id].parameters
    assert isinstance(second_parameters, ProductionParameters)
    second_defend = simulation.automations[second_parameters.defend_automation_id or ""]
    assert isinstance(second_defend.parameters, DefendParameters)
    assert second_defend.parameters.target == second_target
    assert second_defend.parameters.gathering_point
    assert second_defend.entity_ids == ["light_tank_002"]


def test_replacing_continuous_production_preserves_older_finite_jobs() -> None:
    simulation = Simulation(runtime_map())
    finite = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 2))
    older = simulation.execute(
        CreateProductionCommand("factory", EntityKind.LIGHT_TANK, 1, continuous=True)
    )
    latest = simulation.execute(
        CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1, continuous=True)
    )

    assert simulation.automations[older.automation_id or ""].status is AutomationStatus.CANCELED
    assert [item.automation_id for item in simulation.production_queue("factory")] == [
        finite.automation_id,
        latest.automation_id,
    ]
    assert simulation.assignments["factory"] == finite.automation_id
    assert simulation.automations[latest.automation_id or ""].reason_code == "FACTORY_QUEUED"


def test_production_waits_when_no_spawn_cell_is_available() -> None:
    game_map = load_map_data(
        {
            "id": "blocked_factory",
            "name": "Blocked Factory",
            "width": 6,
            "height": 6,
            "terrain": {"default": "water", "rectangles": [[1, 1, 4, 4, "grass"]]},
            "entities": [{"id": "factory", "kind": "factory", "position": [1, 1]}],
        }
    )
    simulation = Simulation(game_map)
    created = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))

    simulation.advance(10)

    automation = simulation.automations[created.automation_id or ""]
    assert automation.status is AutomationStatus.WAITING
    assert automation.reason_code == "SPAWN_BLOCKED"


@pytest.mark.parametrize("kind", [EntityKind.SCOUT, EntityKind.HEAVY_TANK])
def test_production_preserves_disc_clearance_across_empty_neighbor_cells(kind: EntityKind) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "adjacent_factory_disc",
                "name": "Adjacent factory disc",
                "width": 20,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "factory", "kind": "factory", "position": [7, 8]},
                    {"id": "blocker", "kind": "heavy_tank", "position": [7.5, 6.99]},
                ],
            }
        )
    )
    result = simulation.execute(CreateProductionCommand("factory", kind, 1))
    assert result.accepted
    for _ in range(10):
        simulation.advance()
        units = [entity for entity in simulation.entities.values() if entity.is_movable]
        for index, first in enumerate(units):
            for second in units[index + 1 :]:
                assert first.position.distance_to(second.position) >= (
                    collision_radius(first.kind) + collision_radius(second.kind) - 1e-8
                )
    assert len(units) == 2
    assert simulation.automations[result.automation_id or ""].status is AutomationStatus.COMPLETED


def test_production_waits_for_actual_disc_capacity_then_resumes_without_new_overlap() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "disc_blocked_factory",
                "name": "Disc blocked factory",
                "width": 12,
                "height": 14,
                "terrain": {
                    "default": "water",
                    "rectangles": [[7, 8, 4, 4, "grass"], [7, 6, 1, 2, "grass"]],
                },
                "entities": [
                    {"id": "factory", "kind": "factory", "position": [7, 8]},
                    {"id": "blocker", "kind": "heavy_tank", "position": [7.5, 6.99]},
                ],
            }
        )
    )
    result = simulation.execute(CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1))
    simulation.advance(10)
    automation = simulation.automations[result.automation_id or ""]
    assert automation.status is AutomationStatus.WAITING
    assert automation.reason_code == "SPAWN_BLOCKED"
    assert len(simulation.entities) == 2
    assert simulation.execute(MoveCommand(("blocker",), Point(7.5, 6.5))).accepted
    for _ in range(10):
        simulation.advance()
        units = [entity for entity in simulation.entities.values() if entity.is_movable]
        if len(units) == 2:
            assert units[0].position.distance_to(units[1].position) >= 0.9 - 1e-8
    assert automation.status is AutomationStatus.COMPLETED
    assert len(simulation.entities) == 3


def test_production_uses_legal_space_within_an_occupied_mobile_cell() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "shared_factory_exit_cell",
                "name": "Shared factory exit cell",
                "width": 20,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "factory", "kind": "factory", "position": [7, 8]},
                    {"id": "blocker", "kind": "scout", "position": [7.01, 7.01]},
                ],
            }
        )
    )
    result = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    simulation.advance(10)
    produced = simulation.entities["scout_001"]
    blocker = simulation.entities["blocker"]
    assert produced.position == Point(7.5, 7.5)
    assert produced.position.distance_to(blocker.position) >= 0.6 - 1e-8
    assert produced.occupied_cells == blocker.occupied_cells
    assert simulation.automations[result.automation_id or ""].status is AutomationStatus.COMPLETED


def test_manual_factory_override_pauses_and_resume_reclaims_factory() -> None:
    simulation = Simulation(runtime_map())
    created = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    automation_id = created.automation_id or ""

    simulation.execute(StopCommand(("factory",)))

    assert simulation.automations[automation_id].status is AutomationStatus.PAUSED
    assert "factory" not in simulation.assignments

    resumed = simulation.execute(ResumeAutomationCommand(automation_id))
    assert resumed.accepted
    assert simulation.assignments["factory"] == automation_id
    assert simulation.automations[automation_id].status is AutomationStatus.ACTIVE


def test_factory_production_requests_run_fifo_and_paused_jobs_resume_without_conflict() -> None:
    simulation = Simulation(runtime_map())
    first = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    second = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    third = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    first_id = first.automation_id or ""
    second_id = second.automation_id or ""
    third_id = third.automation_id or ""

    assert first.accepted and second.accepted and third.accepted
    assert simulation.automations[first_id].status is AutomationStatus.ACTIVE
    assert simulation.automations[second_id].reason_code == "FACTORY_QUEUED"
    assert simulation.automations[third_id].reason_code == "FACTORY_QUEUED"
    assert [item.automation_id for item in simulation.production_queue("factory")] == [
        first_id,
        second_id,
        third_id,
    ]
    assert simulation.execute(PauseAutomationCommand(second_id)).accepted
    resumed_queued = simulation.execute(ResumeAutomationCommand(second_id))
    assert resumed_queued.accepted
    assert simulation.automations[second_id].status is AutomationStatus.WAITING
    assert simulation.automations[second_id].reason_code == "FACTORY_QUEUED"

    assert simulation.execute(PauseAutomationCommand(first_id)).accepted
    resumed_active = simulation.execute(ResumeAutomationCommand(first_id))
    assert resumed_active.accepted
    assert simulation.assignments["factory"] == first_id

    simulation.advance(30)

    assert [simulation.automations[item].status for item in (first_id, second_id, third_id)] == [
        AutomationStatus.COMPLETED
    ] * 3
    assert [_production_ids(simulation, item) for item in (first_id, second_id, third_id)] == [
        ["scout_001"],
        ["scout_002"],
        ["scout_003"],
    ]
    active_ticks = [
        next(
            transition.tick
            for transition in simulation.automations[item].transition_history
            if transition.reason_code == reason
        )
        for item, reason in (
            (first_id, "VALIDATION_SUCCEEDED"),
            (second_id, "FACTORY_QUEUE_STARTED"),
            (third_id, "FACTORY_QUEUE_STARTED"),
        )
    ]
    assert active_ticks == [0, 5, 10]


def test_canceling_factory_job_starts_next_without_disturbing_the_active_job() -> None:
    simulation = Simulation(runtime_map())
    first = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    second = simulation.execute(CreateProductionCommand("factory", EntityKind.LIGHT_TANK, 1))
    third = simulation.execute(CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1))

    assert simulation.execute(CancelAutomationCommand(third.automation_id or "")).accepted
    assert simulation.assignments["factory"] == first.automation_id
    assert [item.automation_id for item in simulation.production_queue("factory")] == [
        first.automation_id,
        second.automation_id,
    ]

    assert simulation.execute(CancelAutomationCommand(first.automation_id or "")).accepted
    assert simulation.assignments["factory"] == second.automation_id
    assert simulation.automations[second.automation_id or ""].status is AutomationStatus.ACTIVE
    assert simulation.automations[second.automation_id or ""].reason_code == "FACTORY_QUEUE_STARTED"


def test_removing_production_source_fails_automation() -> None:
    simulation = Simulation(runtime_map())
    created = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 2))

    simulation.remove_entity("factory", "DESTROYED")

    automation = simulation.automations[created.automation_id or ""]
    assert automation.status is AutomationStatus.FAILED
    assert automation.reason_code == "SOURCE_ENTITY_REMOVED"


def test_production_charges_cost_and_waits_for_resources() -> None:
    simulation = economy_combat_simulation()
    created = simulation.execute(CreateProductionCommand("factory", EntityKind.LIGHT_TANK, 1))
    simulation.advance()
    assert simulation.resources["player"] == 400
    assert any(
        event.event_type is EventType.RESOURCE_CHANGED
        and event.details["reason"] == "PRODUCTION_COST"
        for event in simulation.events.events
    )
    simulation.advance(3)
    assert not simulation.automations[created.automation_id or ""].parameters.to_dict()[
        "produced_entity_ids"
    ]
    simulation.advance()
    assert simulation.automations[created.automation_id or ""].parameters.to_dict()[
        "produced_entity_ids"
    ] == ["light_tank_001"]

    poor = economy_combat_simulation()
    poor.resources["player"] = 50
    result = poor.execute(CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1))
    poor.advance()
    assert poor.automations[result.automation_id or ""].status is AutomationStatus.WAITING
    assert poor.resources["player"] == 50


@pytest.mark.parametrize(
    "kind",
    [EntityKind.SCOUT, EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK, EntityKind.BUILDER],
    ids=lambda kind: kind.value,
)
def test_factory_can_produce_every_mobile_unit_kind_through_shared_logic(kind: EntityKind) -> None:
    simulation = builder_factory_simulation()
    before = simulation.resources["player"]
    result = simulation.execute(CreateProductionCommand("factory", kind, 1))
    assert result.accepted
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS)
    production = simulation.automations[result.automation_id or ""]
    parameters = production.parameters
    assert isinstance(parameters, ProductionParameters)
    assert production.status is AutomationStatus.COMPLETED
    assert simulation.entities[parameters.produced_entity_ids[0]].kind is kind
    assert simulation.resources["player"] == before - kind.profile.production_cost


def test_factory_loop_button_broadcasts_to_same_type_selection() -> None:
    app = AirtsApp(builder_factory_simulation())
    app.selected_entities = {"factory", "factory_2"}
    app._selection_changed()
    button = pygame.Rect(10, 10, 80, 30)
    app._command_buttons = [(button, "loop:heavy_tank")]

    app._handle_command_click(button.center)

    loops = [app.simulation.continuous_production(item) for item in ("factory", "factory_2")]
    assert all(loop is not None for loop in loops)
    assert all(
        isinstance(loop.parameters, ProductionParameters)
        and loop.parameters.unit_kind is EntityKind.HEAVY_TANK
        for loop in loops
        if loop is not None
    )
    assert "2 factories" in app.notice


def test_ordered_factory_queue_broadcasts_to_same_type_selection() -> None:
    app = AirtsApp(builder_factory_simulation())
    app.selected_entities = {"factory", "factory_2"}
    app._selection_changed()
    app.production_sequence = [(EntityKind.SCOUT, 2), (EntityKind.BUILDER, 1)]
    button = pygame.Rect(10, 10, 80, 30)
    app._command_buttons = [(button, "start_queue")]

    app._handle_command_click(button.center)

    for factory_id in ("factory", "factory_2"):
        queue = app.simulation.production_queue(factory_id)
        assert len(queue) == 1
        parameters = queue[0].parameters
        assert isinstance(parameters, ProductionParameters)
        assert parameters.sequence == ((EntityKind.SCOUT, 2), (EntityKind.BUILDER, 1))
    assert not app.production_sequence
    assert "2 factories" in app.notice


def test_produce_and_defend_broadcasts_to_every_selected_factory_loop() -> None:
    simulation = builder_factory_simulation()
    loops = [
        simulation.execute(
            CreateProductionCommand(factory_id, EntityKind.LIGHT_TANK, 1, continuous=True)
        )
        for factory_id in ("factory", "factory_2")
    ]
    app = AirtsApp(simulation)
    app.selected_entities = {"factory", "factory_2"}
    app._selection_changed()
    app.active_target = PolylineTarget((Point(6, 14), Point(18, 14)))

    app._create_production()

    for result in loops:
        production = simulation.automations[result.automation_id or ""]
        parameters = production.parameters
        assert isinstance(parameters, ProductionParameters)
        assert parameters.defend_target == app.active_target
    assert "2 factories" in app.notice


def test_finite_factory_batch_obeys_order_and_exact_quantities_then_leaves_live_list() -> None:
    simulation = builder_factory_simulation()
    result = simulation.execute(
        CreateProductionBatchCommand(
            "factory",
            ((EntityKind.LIGHT_TANK, 2), (EntityKind.HEAVY_TANK, 1), (EntityKind.SCOUT, 3)),
        )
    )
    assert result.accepted

    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 6)

    automation = simulation.automations[result.automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, ProductionParameters)
    assert [simulation.entities[item].kind for item in parameters.produced_entity_ids] == [
        EntityKind.LIGHT_TANK,
        EntityKind.LIGHT_TANK,
        EntityKind.HEAVY_TANK,
        EntityKind.SCOUT,
        EntityKind.SCOUT,
        EntityKind.SCOUT,
    ]
    assert automation.status is AutomationStatus.COMPLETED
    assert automation not in simulation.live_automations


def test_continuous_factory_production_repeats_one_kind_until_canceled() -> None:
    simulation = builder_factory_simulation()
    result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.BUILDER, 1, continuous=True)
    )
    assert result.accepted

    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 3)
    automation = simulation.automations[result.automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.produced_count == 3
    assert all(
        simulation.entities[item].kind is EntityKind.BUILDER
        for item in parameters.produced_entity_ids
    )
    assert automation.status is AutomationStatus.ACTIVE

    simulation.execute(CancelAutomationCommand(automation.automation_id))
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 2)
    assert parameters.produced_count == 3
    assert automation not in simulation.live_automations


def test_finite_player_queue_preempts_then_resumes_continuous_loop() -> None:
    simulation = builder_factory_simulation()
    loop_result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1, continuous=True)
    )
    simulation.advance(2)
    loop = simulation.automations[loop_result.automation_id or ""]
    loop_parameters = loop.parameters
    assert isinstance(loop_parameters, ProductionParameters)
    assert loop_parameters.progress_ticks == 2

    queue_result = simulation.execute(
        CreateProductionBatchCommand("factory", ((EntityKind.SCOUT, 1), (EntityKind.BUILDER, 1)))
    )
    assert queue_result.accepted
    assert simulation.production_queue("factory")[0].automation_id == queue_result.automation_id

    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 2)
    queue = simulation.automations[queue_result.automation_id or ""]
    queue_parameters = queue.parameters
    assert isinstance(queue_parameters, ProductionParameters)
    assert [simulation.entities[item].kind for item in queue_parameters.produced_entity_ids] == [
        EntityKind.SCOUT,
        EntityKind.BUILDER,
    ]
    assert queue.status is AutomationStatus.COMPLETED
    assert loop.status is AutomationStatus.ACTIVE
    assert loop_parameters.progress_ticks == 2


def test_produce_and_defend_attaches_area_to_current_loop_kind() -> None:
    simulation = builder_factory_simulation()
    loop_result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1, continuous=True)
    )
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS)
    loop = simulation.automations[loop_result.automation_id or ""]
    parameters = loop.parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.defend_target is None
    app = AirtsApp(simulation)
    app.selected_entities = {"factory"}
    app._selection_changed()
    app.active_target = rectangle_region(Point(12, 12), Point(18, 18))

    app._create_production()

    assert (
        len([item for item in simulation.automations.values() if item.kind.value == "production"])
        == 1
    )
    assert parameters.unit_kind is EntityKind.HEAVY_TANK
    assert parameters.defend_target == app.active_target
    assert parameters.defend_automation_id is not None
    defender = simulation.entities[parameters.produced_entity_ids[0]]
    assert simulation.assignments[defender.entity_id] == parameters.defend_automation_id


def test_automation_inspector_can_attach_current_loop_to_defense_area() -> None:
    simulation = builder_factory_simulation()
    loop_result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.BUILDER, 1, continuous=True)
    )
    app = AirtsApp(simulation)
    app.selected_automation_id = loop_result.automation_id
    app.active_target = rectangle_region(Point(12, 12), Point(18, 18))

    app._apply_target_to_automation()

    parameters = simulation.automations[loop_result.automation_id or ""].parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.defend_target == app.active_target


def test_factory_production_and_linked_area_defense_share_one_panel_item() -> None:
    simulation = builder_factory_simulation()
    result = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.SCOUT,
            1,
            continuous=True,
            defend_target=rectangle_region(Point(5, 5), Point(9, 9)),
        )
    )
    assert result.accepted
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS)
    assert {item.kind for item in simulation.live_automations} == {
        AutomationKind.PRODUCTION,
        AutomationKind.DEFEND,
    }

    rows = AirtsApp(simulation)._automation_panel_rows()

    assert len(rows) == 1
    assert rows[0].kind is AutomationKind.PRODUCTION


def test_factory_and_area_interaction_attaches_current_loop_to_defense() -> None:
    simulation = interaction_simulation()
    app = AirtsApp(simulation)
    target = rectangle_region(Point(8, 8), Point(13, 13))
    created_region = simulation.execute(CreateSpatialReferenceCommand(target, "Front Line"))
    factory_position = simulation.entities["factory"].selection_position

    loop = simulation.execute(
        CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1, continuous=True)
    )
    app._select_entities(factory_position, factory_position)
    app._select_entities(Point(9, 9), Point(9, 9))

    app._create_production()

    assert app.selected_entities == {"factory"}
    assert app.selected_regions == {created_region.reference_id}
    automation = simulation.automations[app.selected_automation_id or ""]
    assert automation.automation_id == loop.automation_id
    assert isinstance(automation.parameters, ProductionParameters)
    assert automation.parameters.continuous
    assert automation.parameters.unit_kind is EntityKind.HEAVY_TANK
    assert automation.parameters.defend_target == target


def test_factory_produce_and_defend_supports_line_target() -> None:
    simulation = interaction_simulation()
    simulation.resources["player"] = 10_000
    app = AirtsApp(simulation)
    target = PolylineTarget((Point(8, 8), Point(13, 8), Point(15, 11)))
    loop = simulation.execute(
        CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1, continuous=True)
    )
    app.selected_entities = {"factory"}
    app._selection_changed()
    app.active_target = target

    app._create_production()
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 2)

    production = simulation.automations[loop.automation_id or ""]
    parameters = production.parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.unit_kind is EntityKind.HEAVY_TANK
    assert parameters.defend_target == target
    defend = simulation.automations[parameters.defend_automation_id or ""]
    assert isinstance(defend.parameters, DefendParameters)
    assert not defend.parameters.gathering_point
    assert len(defend.parameters.stations) == 2
    assert all(station.y >= 8 for station in defend.parameters.stations.values())
