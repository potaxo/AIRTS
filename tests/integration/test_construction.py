"""Construction progress, builder coordination, and placement controls."""

from __future__ import annotations

from unittest.mock import patch

import pygame
import pytest
from tests.integration.scenarios import (
    builder_factory_simulation,
)

from airts.contracts.automations import (
    AutomationStatus,
    ConstructionParameters,
)
from airts.contracts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    CreateConstructionCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
    PauseAutomationCommand,
)
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.systems.construction import construction_distance
from airts.world.entities import UnitState
from airts.world.units.catalog import EntityKind


def test_builder_construction_is_paid_timed_and_places_building_on_completion() -> None:
    simulation = builder_factory_simulation()
    before = simulation.resources["player"]
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(6, 10))
    )
    assert result.accepted
    automation = simulation.automations[result.automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, ConstructionParameters)

    simulation.advance(Simulation.CONSTRUCTION_BUILD_TICKS - 1)
    assert parameters.constructed_entity_id is None
    assert (
        simulation.resources["player"]
        == before - EntityKind.RESOURCE_GENERATOR.profile.construction_cost
    )

    simulation.advance()
    assert automation.status is AutomationStatus.COMPLETED
    assert parameters.constructed_entity_id is not None
    building = simulation.entities[parameters.constructed_entity_id]
    assert building.kind is EntityKind.RESOURCE_GENERATOR
    assert building.position == Point(6, 10)
    assert automation not in simulation.live_automations


@pytest.mark.parametrize(
    ("kind", "position", "reason"),
    [
        pytest.param(
            EntityKind.LIGHT_TANK, Point(12, 12), "UNSUPPORTED_CONSTRUCTION_KIND", id="mobile-kind"
        ),
        pytest.param(
            EntityKind.COMMAND_CENTER,
            Point(12, 12),
            "UNSUPPORTED_CONSTRUCTION_KIND",
            id="command-center",
        ),
        pytest.param(EntityKind.FACTORY, Point(2, 2), "BUILDING_OVERLAP", id="occupied-site"),
        pytest.param(EntityKind.FACTORY, Point(22, 22), "FOOTPRINT_OUTSIDE_MAP", id="outside-map"),
        pytest.param(
            EntityKind.RESOURCE_GENERATOR, Point(20, 20), "BUILDING_TERRAIN_BLOCKED", id="water"
        ),
    ],
)
def test_builder_rejects_invalid_construction_without_mutating_world(
    kind: EntityKind, position: Point, reason: str
) -> None:
    simulation = builder_factory_simulation()
    before = simulation.snapshot()
    result = simulation.execute(CreateConstructionCommand("builder", kind, position))
    assert not result.accepted
    assert reason in result.reason
    assert simulation.snapshot() == before


def test_rejected_construction_preserves_queue_while_builder_is_under_repair() -> None:
    simulation = builder_factory_simulation()
    active = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(6, 10))
    )
    queued = simulation.execute(
        CreateConstructionCommand(
            "builder", EntityKind.RESOURCE_GENERATOR, Point(16, 16), queued=True
        )
    )
    assert active.accepted and queued.accepted
    simulation.entities["builder"].health = 1
    repair = simulation.execute(CreateRepairAndReturnCommand(("builder",)))
    assert repair.accepted
    before = simulation.snapshot()

    rejected = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(12, 12))
    )

    assert not rejected.accepted
    assert rejected.reason == "CONTROL_CONFLICT"
    assert simulation.snapshot() == before
    assert simulation.automations[queued.automation_id or ""].status is AutomationStatus.WAITING
    assert simulation.assignments["builder"] == repair.automation_id


def test_multiple_builders_share_one_job_and_reduce_construction_time() -> None:
    single = builder_factory_simulation()
    single_result = single.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10))
    )
    single.advance(Simulation.CONSTRUCTION_BUILD_TICKS // 2)
    single_parameters = single.automations[single_result.automation_id or ""].parameters
    assert isinstance(single_parameters, ConstructionParameters)
    assert single_parameters.constructed_entity_id is None
    assert single_parameters.construction_value == single_parameters.required_value / 2

    team = builder_factory_simulation()
    team_result = team.execute(
        CreateConstructionCommand(
            "builder",
            EntityKind.REPAIR_HUB,
            Point(6, 10),
            builder_ids=("builder", "builder_2"),
        )
    )
    assert team_result.accepted
    team_automation = team.automations[team_result.automation_id or ""]
    team_parameters = team_automation.parameters
    assert isinstance(team_parameters, ConstructionParameters)
    team.advance()
    assert team_parameters.construction_value == EntityKind.BUILDER.profile.build_speed * 2
    assert 0 < team_parameters.construction_value < team_parameters.required_value
    team.advance(Simulation.CONSTRUCTION_BUILD_TICKS // 2 - 1)
    assert team_parameters.constructed_entity_id is not None
    assert team_parameters.construction_value == team_parameters.required_value
    assert team_automation.entity_ids == ["builder", "builder_2"]
    assert team.resources["player"] == 10_000 - EntityKind.REPAIR_HUB.profile.construction_cost


def test_builder_group_placement_submits_every_selected_builder() -> None:
    app = AirtsApp(builder_factory_simulation())
    app.selected_entities = {"builder", "builder_2"}
    app._selection_changed()
    app.placement_kind = EntityKind.RESOURCE_GENERATOR

    app._handle_mouse_down(1, app.screen_point(Point(14, 14)))

    construction = next(
        automation
        for automation in app.simulation.automations.values()
        if isinstance(automation.parameters, ConstructionParameters)
    )
    assert construction.entity_ids == ["builder", "builder_2"]
    assert app.placement_kind is None


def test_construction_preview_reports_validity_before_placement() -> None:
    app = AirtsApp(builder_factory_simulation())
    app.placement_kind = EntityKind.RESOURCE_GENERATOR

    valid = app._construction_preview_at(Point(14.2, 14.8))
    blocked = app._construction_preview_at(Point(2.2, 2.2))

    assert valid is not None and valid[0] == Point(14, 14) and valid[1]
    assert blocked is not None and blocked[0] == Point(2, 2) and not blocked[1]


def test_builder_must_enter_build_range_before_contributing_work() -> None:
    simulation = builder_factory_simulation()
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(16, 14))
    )
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters)
    initial_distance = simulation.entities["builder"].position.distance_to(Point(16, 14))

    simulation.advance()

    assert EntityKind.BUILDER.profile.build_range > 0
    assert parameters.construction_value == 0
    assert simulation.entities["builder"].path

    for _ in range(100):
        simulation.advance()
        if parameters.construction_value > 0:
            break

    assert parameters.construction_value > 0
    assert simulation.entities["builder"].position.distance_to(Point(16, 14)) < initial_distance


def test_shift_construction_appends_fifo_jobs_and_keeps_placement_active() -> None:
    app = AirtsApp(builder_factory_simulation())
    app.selected_entities = {"builder", "builder_2"}
    app._selection_changed()
    app.placement_kind = EntityKind.RESOURCE_GENERATOR

    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        app._handle_mouse_down(1, app.screen_point(Point(12, 12)))
        app._handle_mouse_up(1, app.screen_point(Point(12, 12)))
        app._handle_mouse_down(1, app.screen_point(Point(16, 16)))
        app._handle_mouse_up(1, app.screen_point(Point(16, 16)))

    constructions = [
        automation
        for automation in app.simulation.automations.values()
        if automation.kind.value == "construction"
    ]
    assert len(constructions) == 2
    assert constructions[0].status is AutomationStatus.ACTIVE
    assert constructions[1].status is AutomationStatus.WAITING
    assert constructions[1].reason_code == "BUILDERS_QUEUED"
    assert app.placement_kind is EntityKind.RESOURCE_GENERATOR
    assert app.simulation.command_history[-1]["command"]["queued"] is True

    for _ in range(100):
        app.simulation.advance()
        if constructions[0].status is AutomationStatus.COMPLETED:
            break

    assert constructions[0].status is AutomationStatus.COMPLETED
    assert constructions[1].status is AutomationStatus.ACTIVE


def test_completed_construction_waits_for_builder_to_leave_footprint() -> None:
    simulation = builder_factory_simulation()
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(12, 12))
    )
    automation = simulation.automations[result.automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, ConstructionParameters)
    parameters.construction_value = (
        parameters.required_value - EntityKind.BUILDER.profile.build_speed
    )
    simulation.occupancy.move("builder", frozenset({(12, 12)}))
    simulation.entities["builder"].position = Point(12.5, 12.5)

    simulation.advance()

    assert parameters.constructed_entity_id is None
    assert automation.status is AutomationStatus.ACTIVE
    assert simulation.entities["builder"].path

    for _ in range(50):
        simulation.advance()
        if parameters.constructed_entity_id is not None:
            break

    assert parameters.constructed_entity_id is not None
    building = simulation.entities[parameters.constructed_entity_id]
    assert not building.occupied_cells.intersection(simulation.entities["builder"].occupied_cells)


def test_right_click_exits_shift_construction_without_moving_builders() -> None:
    app = AirtsApp(builder_factory_simulation())
    app.selected_entities = {"builder", "builder_2"}
    app._selection_changed()
    app.placement_kind = EntityKind.RESOURCE_GENERATOR
    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        app._handle_mouse_down(1, app.screen_point(Point(6, 10)))
        app._handle_mouse_up(1, app.screen_point(Point(6, 10)))
        app._handle_mouse_down(1, app.screen_point(Point(16, 16)))
        app._handle_mouse_up(1, app.screen_point(Point(16, 16)))
    assignments = dict(app.simulation.assignments)
    command_count = len(app.simulation.command_history)

    app._handle_mouse_down(3, app.screen_point(Point(20, 20)))

    assert app.placement_kind is None
    assert app.simulation.assignments == assignments
    assert len(app.simulation.command_history) == command_count
    assert all(app.simulation.entities[item].move_target is None for item in assignments)


def test_poor_builder_travels_waits_then_builds_fifo_without_remote_charge() -> None:
    simulation = builder_factory_simulation()
    simulation.resources["player"] = 0
    first = simulation.execute(
        CreateConstructionCommand(
            "builder", EntityKind.RESOURCE_EXTRACTOR, Point(16, 14), queued=True
        )
    )
    second = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10), queued=True)
    )
    first_job = simulation.automations[first.automation_id or ""]
    first_parameters = first_job.parameters
    assert isinstance(first_parameters, ConstructionParameters)
    initial = simulation.entities["builder"].position
    simulation.advance()
    assert simulation.entities["builder"].position != initial
    assert simulation.entities["builder"].path
    assert not first_parameters.cost_paid
    assert first_parameters.site_entity_id is None
    simulation.advance(100)
    assert first_job.reason_code == "INSUFFICIENT_RESOURCES"
    assert (
        construction_distance(simulation.entities["builder"].position, first_parameters)
        <= EntityKind.BUILDER.profile.build_range
    )
    assert simulation.entities["builder"].state is UnitState.WAITING
    assert simulation.resources["player"] == 0
    simulation.resources["player"] = EntityKind.RESOURCE_EXTRACTOR.profile.construction_cost
    simulation.advance()
    site = simulation.entities[first_parameters.site_entity_id or ""]
    assert not site.is_complete and site.health < site.kind.profile.max_health
    assert site.occupied_cells <= simulation._building_cells()
    assert first_parameters.cost_paid
    assert simulation.resources["player"] == 0
    simulation.advance(100)
    assert first_job.status is AutomationStatus.COMPLETED
    assert site.is_complete
    # At 2/s the completed extractor cannot yet fund the 250 repair hub.
    assert (
        simulation.automations[second.automation_id or ""].reason_code == "INSUFFICIENT_RESOURCES"
    )
    simulation.advance(1300)
    assert simulation.automations[second.automation_id or ""].status is AutomationStatus.COMPLETED
    assert site.is_complete
    assert not simulation.assignments
    assert not any(job.kind.value == "economy" for job in simulation.automations.values())


def test_overlapping_blueprints_do_not_reserve_and_covered_job_skips_to_next() -> None:
    simulation = builder_factory_simulation()
    first = simulation.execute(
        CreateConstructionCommand(
            "builder", EntityKind.RESOURCE_EXTRACTOR, Point(16, 14), queued=True
        )
    )
    covered = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(16, 14), queued=True)
    )
    last = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10), queued=True)
    )
    assert first.accepted and covered.accepted and last.accepted
    cells_before = simulation._building_cells()
    simulation.advance()
    assert simulation._building_cells() == cells_before
    assert simulation.execute(MoveCommand(("scout",), Point(20.5, 14.5))).accepted
    simulation.advance(200)
    assert simulation.automations[first.automation_id or ""].status is AutomationStatus.COMPLETED
    assert simulation.automations[covered.automation_id or ""].reason_code == "BUILDING_OVERLAP"
    assert simulation.automations[last.automation_id or ""].status is AutomationStatus.COMPLETED


def test_site_occupied_by_unit_waits_without_charge_or_phantom_building() -> None:
    simulation = builder_factory_simulation()
    scout = simulation.entities["scout"]
    scout.position = Point(6.5, 10.5)
    simulation.occupancy.move(scout.entity_id, scout.occupied_cells)
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_EXTRACTOR, Point(6, 10))
    )
    job = simulation.automations[result.automation_id or ""]
    parameters = job.parameters
    assert isinstance(parameters, ConstructionParameters)
    before = simulation.resources["player"]
    simulation.advance(30)
    assert job.reason_code == "SITE_OCCUPIED"
    assert parameters.site_entity_id is None and not parameters.cost_paid
    assert simulation.resources["player"] == before
    assert simulation.execute(MoveCommand(("scout",), Point(13.5, 10.5))).accepted
    simulation.advance(60)
    assert job.status is AutomationStatus.COMPLETED


def test_unfinished_factory_cannot_produce_and_enemy_can_destroy_it_then_queue_continues() -> None:
    simulation = builder_factory_simulation()
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.FACTORY, Point(6, 10))
    )
    queued = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(16, 14), queued=True)
    )
    job = simulation.automations[result.automation_id or ""]
    parameters = job.parameters
    assert isinstance(parameters, ConstructionParameters)
    simulation.advance()
    site_id = parameters.site_entity_id or ""
    production = simulation.execute(CreateProductionCommand(site_id, EntityKind.SCOUT, 1))
    assert not production.accepted and production.reason == "FACTORY_UNDER_CONSTRUCTION"
    enemy = simulation.entities["tank"]
    enemy.owner_id = "enemy"
    assert simulation.execute(AttackCommand((enemy.entity_id,), site_id, owner_id="enemy")).accepted
    # Pause work so this low-health scaffold remains exposed to real projectile damage.
    assert simulation.execute(PauseAutomationCommand(job.automation_id)).accepted
    for _ in range(40):
        simulation.advance()
        if site_id not in simulation.entities:
            break
    assert site_id not in simulation.entities
    assert job.reason_code == "CONSTRUCTION_DESTROYED"
    assert simulation.assignments["builder"] == queued.automation_id
    simulation.remove_entity(enemy.entity_id)
    simulation.advance(100)
    assert simulation.automations[queued.automation_id or ""].status is AutomationStatus.COMPLETED


def test_canceling_construction_keeps_real_site_and_reissuing_resumes_without_repaying() -> None:
    simulation = builder_factory_simulation()
    command = CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10))
    result = simulation.execute(command)
    simulation.advance(3)
    job = simulation.automations[result.automation_id or ""]
    parameters = job.parameters
    assert isinstance(parameters, ConstructionParameters)
    site_id = parameters.site_entity_id
    balance = simulation.resources["player"]
    assert simulation.execute(CancelAutomationCommand(job.automation_id)).accepted
    assert site_id in simulation.entities
    resumed = simulation.execute(command)
    assert resumed.accepted
    simulation.advance(20)
    assert simulation.automations[resumed.automation_id or ""].status is AutomationStatus.COMPLETED
    assert simulation.resources["player"] == balance
    assert simulation.entities[site_id or ""].is_complete


def test_queued_group_keeps_surviving_builder_when_primary_is_destroyed() -> None:
    simulation = builder_factory_simulation()
    first = simulation.execute(
        CreateConstructionCommand(
            "builder", EntityKind.REPAIR_HUB, Point(6, 10), builder_ids=("builder", "builder_2")
        )
    )
    queued = simulation.execute(
        CreateConstructionCommand(
            "builder",
            EntityKind.RESOURCE_EXTRACTOR,
            Point(16, 14),
            builder_ids=("builder", "builder_2"),
            queued=True,
        )
    )
    simulation.advance(2)
    simulation.remove_entity("builder")
    simulation.advance(140)
    assert simulation.automations[first.automation_id or ""].status is AutomationStatus.COMPLETED
    assert simulation.automations[queued.automation_id or ""].status is AutomationStatus.COMPLETED
    assert simulation.automations[queued.automation_id or ""].entity_ids == ["builder_2"]


def test_builder_repairs_and_returns_to_scaffold_before_next_shift_order() -> None:
    simulation = builder_factory_simulation()
    first = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10))
    )
    queued = simulation.execute(
        CreateConstructionCommand(
            "builder", EntityKind.RESOURCE_EXTRACTOR, Point(16, 14), queued=True
        )
    )
    simulation.advance(2)
    simulation.entities["builder"].health = 1
    repair = simulation.execute(CreateRepairAndReturnCommand(("builder",)))
    assert repair.accepted
    simulation.advance()
    assert simulation.automations[first.automation_id or ""].status is AutomationStatus.ACTIVE
    assert simulation.suspended_assignments["builder"] == first.automation_id
    simulation.advance(200)
    assert simulation.automations[first.automation_id or ""].status is AutomationStatus.COMPLETED
    assert simulation.automations[queued.automation_id or ""].status is AutomationStatus.COMPLETED


def test_only_owner_sees_blueprints_but_enemy_scaffolds_are_drawn() -> None:
    simulation = builder_factory_simulation()
    simulation.remove_entity("scout")
    simulation.remove_entity("tank")
    simulation.entities["builder_2"].owner_id = "enemy"
    simulation.resources["enemy"] = 10_000
    own = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(16, 16))
    )
    enemy = simulation.execute(
        CreateConstructionCommand(
            "builder_2", EntityKind.REPAIR_HUB, Point(14, 5), owner_id="enemy"
        )
    )
    assert own.accepted and enemy.accepted
    app = AirtsApp(simulation)
    surface = pygame.Surface(app.window_size)
    app._draw_construction(surface)
    assert surface.get_at(app.screen_point(Point(17, 17)))[:3] != (0, 0, 0)
    assert surface.get_at(app.screen_point(Point(15, 6)))[:3] == (0, 0, 0)
    enemy_parameters = simulation.automations[enemy.automation_id or ""].parameters
    assert isinstance(enemy_parameters, ConstructionParameters)
    for _ in range(100):
        simulation.advance()
        if enemy_parameters.site_entity_id is not None:
            break
    assert enemy_parameters.site_entity_id is not None
    surface.fill((0, 0, 0))
    app._draw_construction(surface)
    assert surface.get_at(app.screen_point(Point(15.5, 6.5)))[:3] != (0, 0, 0)
