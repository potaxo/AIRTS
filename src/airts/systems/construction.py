"""Authoritative builder construction validation and execution."""

from __future__ import annotations

from math import ceil, floor
from typing import TYPE_CHECKING

from airts.automations import Automation, AutomationKind, AutomationStatus, ConstructionParameters
from airts.commands import CommandResult, CreateConstructionCommand
from airts.geometry import Point
from airts.navigation.pathfinding import PathfindingError
from airts.validation import ValidationFailure, ValidationPhase
from airts.world.entities import Entity, UnitState
from airts.world.map_model import Cell, EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def create_construction(
    simulation: Simulation, command: CreateConstructionCommand
) -> CommandResult:
    builder_ids = tuple(dict.fromkeys(command.builder_ids or (command.builder_id,)))
    if command.builder_id not in builder_ids:
        builder_ids = (command.builder_id, *builder_ids)
    failure = simulation._validate_entities(builder_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("create_construction", failure)
    if any(
        simulation.entities[builder_id].kind is not EntityKind.BUILDER for builder_id in builder_ids
    ):
        return simulation._reject_validation(
            "create_construction",
            ValidationFailure(ValidationPhase.CAPABILITY, "ENTITY_NOT_BUILDER", "builder_id"),
        )
    allowed = {EntityKind.FACTORY, EntityKind.REPAIR_HUB, EntityKind.RESOURCE_GENERATOR}
    if command.building_kind not in allowed:
        return simulation._reject_validation(
            "create_construction",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "UNSUPPORTED_CONSTRUCTION_KIND",
                "building_kind",
            ),
        )
    placement_failure = simulation._validate_building_placement(
        command.building_kind, command.position
    )
    if placement_failure is not None:
        return simulation._reject_validation("create_construction", placement_failure)
    automation = simulation._new_automation(
        AutomationKind.CONSTRUCTION,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(builder_ids),
        ConstructionParameters(
            command.builder_id,
            command.building_kind,
            command.position,
            simulation.CONSTRUCTION_REQUIRED_VALUE,
            builder_ids=list(builder_ids),
        ),
    )
    has_active_construction = any(
        incumbent_id is not None
        and simulation.automations[incumbent_id].kind is AutomationKind.CONSTRUCTION
        for builder_id in builder_ids
        if (incumbent_id := simulation.assignments.get(builder_id)) is not None
    )
    if command.queued and has_active_construction:
        simulation._activate(automation, builder_ids, assign_entities=False)
        simulation._transition(automation, AutomationStatus.WAITING, "BUILDERS_QUEUED")
        return simulation._accept("create_construction", automation.automation_id)
    if not command.queued:
        simulation._cancel_queued_construction(builder_ids)
    claim_failure = simulation._validate_claims(automation, builder_ids, replace_existing=True)
    if claim_failure is not None:
        return simulation._reject_validation("create_construction", claim_failure)
    simulation._activate(automation, builder_ids)
    return simulation._accept("create_construction", automation.automation_id)


def validate_building_placement(
    simulation: Simulation,
    kind: EntityKind,
    position: Point,
    *,
    ignore_construction_id: str | None = None,
) -> ValidationFailure | None:
    if not position.x.is_integer() or not position.y.is_integer():
        return ValidationFailure(ValidationPhase.SPATIAL, "BUILDING_NOT_GRID_ALIGNED", "position")
    width, height = kind.profile.footprint
    cells = frozenset(
        (int(position.x) + x, int(position.y) + y) for y in range(height) for x in range(width)
    )
    if any(not simulation.game_map.contains_cell(cell) for cell in cells):
        return ValidationFailure(ValidationPhase.SPATIAL, "FOOTPRINT_OUTSIDE_MAP", "position")
    if any(not simulation.game_map.is_cell_passable(cell) for cell in cells):
        return ValidationFailure(ValidationPhase.SPATIAL, "BUILDING_TERRAIN_BLOCKED", "position")
    if cells & simulation._building_cells():
        return ValidationFailure(ValidationPhase.SPATIAL, "BUILDING_OVERLAP", "position")
    for automation in simulation.live_automations:
        if (
            automation.kind is not AutomationKind.CONSTRUCTION
            or automation.automation_id == ignore_construction_id
        ):
            continue
        parameters = automation.parameters
        assert isinstance(parameters, ConstructionParameters)
        if cells & simulation._construction_cells(parameters):
            return ValidationFailure(
                ValidationPhase.SPATIAL,
                "CONSTRUCTION_SITE_RESERVED",
                "position",
            )
    return None


def cancel_queued_construction(simulation: Simulation, builder_ids: tuple[str, ...]) -> None:
    selected = frozenset(builder_ids)
    for automation in simulation.automations.values():
        if (
            automation.kind is AutomationKind.CONSTRUCTION
            and automation.status is AutomationStatus.WAITING
            and automation.reason_code == "BUILDERS_QUEUED"
            and selected.intersection(automation.entity_ids)
        ):
            simulation._transition(automation, AutomationStatus.CANCELED, "QUEUE_REPLACED")


def drive_construction(simulation: Simulation, automation: Automation) -> None:
    parameters = automation.parameters
    assert isinstance(parameters, ConstructionParameters)
    if (
        automation.status is AutomationStatus.WAITING
        and automation.reason_code == "BUILDERS_QUEUED"
    ):
        return
    active_builders = [
        builder_id
        for builder_id in automation.entity_ids
        if simulation.assignments.get(builder_id) == automation.automation_id
        and builder_id in simulation.entities
    ]
    if not active_builders:
        simulation._transition(automation, AutomationStatus.FAILED, "BUILDER_UNAVAILABLE")
        simulation._release_automation(automation)
        simulation._start_next_construction()
        return
    if not parameters.cost_paid:
        cost = parameters.building_kind.profile.construction_cost
        balance = simulation.resources.get(automation.owner_id, 0)
        if balance < cost:
            if automation.status is AutomationStatus.ACTIVE:
                simulation._transition(
                    automation, AutomationStatus.WAITING, "INSUFFICIENT_RESOURCES"
                )
            return
        simulation.resources[automation.owner_id] = balance - cost
        parameters.cost_paid = True
        if automation.status is AutomationStatus.WAITING:
            simulation._transition(automation, AutomationStatus.ACTIVE, "RESOURCES_AVAILABLE")
    placement_failure = simulation._validate_building_placement(
        parameters.building_kind,
        parameters.position,
        ignore_construction_id=automation.automation_id,
    )
    if placement_failure is not None:
        simulation._transition(automation, AutomationStatus.FAILED, placement_failure.code)
        simulation._release_automation(automation)
        simulation._start_next_construction()
        return
    site_cells = simulation._construction_cells(parameters)
    site_occupants = frozenset(
        entity_id for cell in site_cells for entity_id in simulation.occupancy.occupants(cell)
    )
    if (
        automation.status is AutomationStatus.WAITING
        and automation.reason_code == "SITE_OCCUPIED"
        and not site_occupants
    ):
        simulation._transition(automation, AutomationStatus.ACTIVE, "SITE_CLEARED")
    builders_in_range: list[str] = []
    route_available = False
    destinations = simulation._construction_interaction_points(parameters)
    for builder_id in active_builders:
        builder = simulation.entities[builder_id]
        inside_footprint = bool(builder.occupied_cells & site_cells)
        if (
            not inside_footprint
            and simulation._construction_distance(builder.position, parameters)
            <= builder.kind.profile.build_range
        ):
            builder.path.clear()
            builder.move_target = None
            builder.state = UnitState.BUILDING
            simulation._reset_movement_liveness(builder, clear_stop=True)
            builders_in_range.append(builder_id)
            route_available = True
            continue
        if inside_footprint and builder.move_target not in destinations:
            builder.path.clear()
            builder.move_target = None
            simulation._reset_movement_liveness(builder, clear_stop=True)
        if builder.path or builder.move_target is not None:
            route_available = True
            continue
        try:
            destination, path = simulation._routes.shared_path_to_any(
                builder.position,
                destinations,
                simulation._building_cells(),
            )
        except PathfindingError:
            continue
        simulation._start_path(
            builder,
            destination,
            path,
            automation.automation_id,
            UnitState.MOVING,
        )
        route_available = True
    if not builders_in_range:
        if not route_available:
            simulation._transition(automation, AutomationStatus.FAILED, "BUILD_SITE_UNREACHABLE")
            simulation._release_automation(automation)
            simulation._start_next_construction()
        return
    work = sum(
        simulation.entities[builder_id].kind.profile.build_speed for builder_id in builders_in_range
    )
    parameters.construction_value = min(
        parameters.required_value,
        parameters.construction_value + work,
    )
    if parameters.construction_value < parameters.required_value:
        return
    site_occupants = frozenset(
        entity_id for cell in site_cells for entity_id in simulation.occupancy.occupants(cell)
    )
    if site_occupants:
        if automation.status is AutomationStatus.ACTIVE:
            simulation._transition(automation, AutomationStatus.WAITING, "SITE_OCCUPIED")
        return
    while True:
        entity_id = f"{parameters.building_kind.value}_{simulation._next_entity_number:03d}"
        simulation._next_entity_number += 1
        if entity_id not in simulation.entities:
            break
    building = Entity(
        entity_id,
        parameters.building_kind,
        automation.owner_id,
        parameters.position,
        parameters.building_kind.profile.max_health,
    )
    simulation.occupancy.place(entity_id, building.occupied_cells)
    simulation.entities[entity_id] = building
    simulation._invalidate_navigation_cache()
    parameters.constructed_entity_id = entity_id
    simulation._transition(automation, AutomationStatus.COMPLETED, "CONSTRUCTION_COMPLETED")
    simulation._release_automation(automation)
    simulation._start_next_construction()


def construction_cells(parameters: ConstructionParameters) -> frozenset[Cell]:
    width, height = parameters.building_kind.profile.footprint
    origin_x = int(parameters.position.x)
    origin_y = int(parameters.position.y)
    return frozenset((origin_x + x, origin_y + y) for y in range(height) for x in range(width))


def construction_distance(point: Point, parameters: ConstructionParameters) -> float:
    width, height = parameters.building_kind.profile.footprint
    nearest_x = min(max(point.x, parameters.position.x), parameters.position.x + width)
    nearest_y = min(max(point.y, parameters.position.y), parameters.position.y + height)
    return point.distance_to(Point(nearest_x, nearest_y))


def construction_interaction_points(
    simulation: Simulation, parameters: ConstructionParameters
) -> tuple[Point, ...]:
    build_range = EntityKind.BUILDER.profile.build_range
    width, height = parameters.building_kind.profile.footprint
    site_cells = simulation._construction_cells(parameters)
    candidates: list[Point] = []
    for y in range(
        floor(parameters.position.y - build_range),
        ceil(parameters.position.y + height + build_range),
    ):
        for x in range(
            floor(parameters.position.x - build_range),
            ceil(parameters.position.x + width + build_range),
        ):
            cell = (x, y)
            point = Point(x + 0.5, y + 0.5)
            if (
                cell not in site_cells
                and simulation.game_map.is_cell_passable(cell)
                and simulation._construction_distance(point, parameters) <= build_range
            ):
                candidates.append(point)
    return tuple(candidates)


def start_next_construction(simulation: Simulation) -> None:
    queued = sorted(
        (
            automation
            for automation in simulation.automations.values()
            if automation.kind is AutomationKind.CONSTRUCTION
            and automation.status is AutomationStatus.WAITING
            and automation.reason_code == "BUILDERS_QUEUED"
        ),
        key=lambda automation: (automation.created_tick, automation.automation_id),
    )
    for automation in queued:
        parameters = automation.parameters
        assert isinstance(parameters, ConstructionParameters)
        builder_ids = tuple(parameters.builder_ids)
        if not builder_ids or any(
            builder_id not in simulation.entities for builder_id in builder_ids
        ):
            simulation._transition(automation, AutomationStatus.FAILED, "BUILDER_UNAVAILABLE")
            continue
        if any(builder_id in simulation.assignments for builder_id in builder_ids):
            continue
        failure = simulation._validate_building_placement(
            parameters.building_kind,
            parameters.position,
            ignore_construction_id=automation.automation_id,
        )
        if failure is not None:
            simulation._transition(automation, AutomationStatus.FAILED, failure.code)
            continue
        for builder_id in builder_ids:
            simulation._assign(builder_id, automation)
            simulation._initialize_runtime_entity(automation, builder_id)
        simulation._transition(automation, AutomationStatus.ACTIVE, "BUILDERS_AVAILABLE")
