"""Construction progress, builder coordination, and placement controls."""

from __future__ import annotations

from unittest.mock import patch

import pygame
import pytest
from tests.integration.scenarios import (
    builder_factory_simulation,
)

from airts.automations import (
    AutomationStatus,
    ConstructionParameters,
)
from airts.commands import (
    CreateConstructionCommand,
)
from airts.geometry import Point
from airts.presentation.app import (
    AirtsApp,
)
from airts.simulation import Simulation
from airts.world.map_model import EntityKind


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

    app._handle_mouse_down(1, app._screen_point(Point(14, 14)))

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
        app._handle_mouse_down(1, app._screen_point(Point(12, 12)))
        app._handle_mouse_down(1, app._screen_point(Point(16, 16)))

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
        app._handle_mouse_down(1, app._screen_point(Point(6, 10)))
        app._handle_mouse_down(1, app._screen_point(Point(16, 16)))
    assignments = dict(app.simulation.assignments)
    command_count = len(app.simulation.command_history)

    app._handle_mouse_down(3, app._screen_point(Point(20, 20)))

    assert app.placement_kind is None
    assert app.simulation.assignments == assignments
    assert len(app.simulation.command_history) == command_count
    assert all(app.simulation.entities[item].move_target is None for item in assignments)
