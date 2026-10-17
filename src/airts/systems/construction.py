"""Authoritative builder construction validation and execution."""

from __future__ import annotations

from math import ceil, floor
from typing import TYPE_CHECKING

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    ConstructionParameters,
)
from airts.contracts.commands import (
    CommandResult,
    CreateConstructionCommand,
    ValidationFailure,
    ValidationPhase,
)
from airts.contracts.spatial import Point
from airts.navigation.pathfinding import PathfindingError
from airts.systems import (
    automation_assignments,
    automation_lifecycle,
    automation_runtime,
    construction_membership,
    economy,
    movement,
)
from airts.systems.entity_registry import allocate_entity_id
from airts.systems.validation import validate_entities
from airts.world.entities import Entity, UnitState
from airts.world.map import Cell, footprint_cells
from airts.world.units.catalog import BUILDABLE_KINDS, EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def create_construction(
    simulation: Simulation, command: CreateConstructionCommand
) -> CommandResult:
    builder_ids = tuple(dict.fromkeys(command.builder_ids or (command.builder_id,)))
    if command.builder_id not in builder_ids:
        builder_ids = (command.builder_id, *builder_ids)
    failure = validate_entities(simulation, builder_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("create_construction", failure)
    if any(not simulation.entities[builder_id].profile.can_build for builder_id in builder_ids):
        return simulation._reject_validation(
            "create_construction",
            ValidationFailure(ValidationPhase.CAPABILITY, "ENTITY_NOT_BUILDER", "builder_id"),
        )
    if command.building_kind not in BUILDABLE_KINDS:
        return simulation._reject_validation(
            "create_construction",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "UNSUPPORTED_CONSTRUCTION_KIND",
                "building_kind",
            ),
        )
    placement_failure = validate_building_placement(
        simulation, command.building_kind, command.position, owner_id=command.owner_id
    )
    if placement_failure is not None:
        return simulation._reject_validation("create_construction", placement_failure)
    existing_site = resumable_site(
        simulation, command.building_kind, command.position, command.owner_id
    )
    automation = automation_lifecycle.new_automation(
        simulation,
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
            construction_value=(
                0.0
                if existing_site is None
                else (existing_site.construction_progress or 0.0)
                * simulation.CONSTRUCTION_REQUIRED_VALUE
            ),
            cost_paid=existing_site is not None,
            site_entity_id=None if existing_site is None else existing_site.entity_id,
        ),
    )
    has_active_construction = any(
        incumbent.kind is AutomationKind.CONSTRUCTION
        and set(builder_ids).intersection(incumbent.entity_ids)
        for incumbent in simulation.live_automations
    )
    if command.queued and has_active_construction:
        automation_assignments.activate(simulation, automation, builder_ids, assign_entities=False)
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.WAITING, "BUILDERS_QUEUED"
        )
        return simulation._accept("create_construction", automation.automation_id)
    claim_failure = automation_lifecycle.validate_claims(
        simulation, automation, builder_ids, replace_existing=True
    )
    if claim_failure is not None:
        return simulation._reject_validation("create_construction", claim_failure)
    if not command.queued:
        construction_membership.cancel_queued_construction(simulation, builder_ids)
    automation_assignments.activate(simulation, automation, builder_ids)
    return simulation._accept("create_construction", automation.automation_id)


def validate_building_placement(
    simulation: Simulation,
    kind: EntityKind,
    position: Point,
    *,
    ignore_construction_id: str | None = None,
    owner_id: str | None = None,
) -> ValidationFailure | None:
    if not position.x.is_integer() or not position.y.is_integer():
        return ValidationFailure(ValidationPhase.SPATIAL, "BUILDING_NOT_GRID_ALIGNED", "position")
    if (
        kind is EntityKind.RESOURCE_GENERATOR
        and simulation.game_map.resource_sites is not None
        and position not in simulation.game_map.resource_sites
    ):
        return ValidationFailure(ValidationPhase.SPATIAL, "RESOURCE_SITE_REQUIRED", "position")
    cells = footprint_cells(kind, position)
    if any(not simulation.game_map.contains_cell(cell) for cell in cells):
        return ValidationFailure(ValidationPhase.SPATIAL, "FOOTPRINT_OUTSIDE_MAP", "position")
    if any(not simulation.game_map.is_cell_passable(cell) for cell in cells):
        return ValidationFailure(ValidationPhase.SPATIAL, "BUILDING_TERRAIN_BLOCKED", "position")
    ignored_site = None
    if owner_id is not None:
        existing = resumable_site(simulation, kind, position, owner_id)
        ignored_site = None if existing is None else existing.entity_id
    if ignore_construction_id is not None:
        parameters = simulation.automations[ignore_construction_id].parameters
        assert isinstance(parameters, ConstructionParameters)
        ignored_site = parameters.site_entity_id
    if any(
        entity.entity_id != ignored_site and not entity.is_movable and cells & entity.occupied_cells
        for entity in simulation.entities.values()
    ):
        return ValidationFailure(ValidationPhase.SPATIAL, "BUILDING_OVERLAP", "position")
    return None


def resumable_site(
    simulation: Simulation, kind: EntityKind, position: Point, owner_id: str
) -> Entity | None:
    claimed = {
        automation.parameters.site_entity_id
        for automation in simulation.live_automations
        if isinstance(automation.parameters, ConstructionParameters)
    }
    return next(
        (
            entity
            for entity in simulation.entities.values()
            if entity.kind is kind
            and entity.position == position
            and entity.owner_id == owner_id
            and not entity.is_complete
            and entity.entity_id not in claimed
        ),
        None,
    )


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
        if any(
            simulation.suspended_assignments.get(builder_id) == automation.automation_id
            for builder_id in automation.entity_ids
        ):
            return
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.FAILED, "BUILDER_UNAVAILABLE"
        )
        automation_assignments.release_automation(simulation, automation)
        start_next_construction(simulation)
        return
    site_cells = construction_cells(parameters)
    builders_in_range: list[str] = []
    route_available = False
    points_by_range: dict[float, tuple[Point, ...]] = {}
    for builder_id in active_builders:
        builder = simulation.entities[builder_id]
        inside_footprint = bool(builder.occupied_cells & site_cells)
        if (
            not inside_footprint
            and construction_distance(builder.position, parameters) <= builder.profile.build_range
        ):
            builder.path.clear()
            builder.move_target = None
            builder.state = UnitState.WAITING
            movement.reset_movement_liveness(builder)
            builders_in_range.append(builder_id)
            route_available = True
            continue
        build_range = builder.profile.build_range
        destinations = points_by_range.get(build_range)
        if destinations is None:
            destinations = construction_interaction_points(simulation, parameters, build_range)
            points_by_range[build_range] = destinations
        if inside_footprint and builder.move_target not in destinations:
            builder.path.clear()
            builder.move_target = None
            movement.reset_movement_liveness(builder)
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
        movement.start_path(
            simulation,
            builder,
            destination,
            path,
            automation.automation_id,
            UnitState.MOVING,
        )
        route_available = True
    if not builders_in_range:
        if not route_available:
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.FAILED, "BUILD_SITE_UNREACHABLE"
            )
            automation_assignments.release_automation(simulation, automation)
            start_next_construction(simulation)
        return
    if parameters.site_entity_id is None:
        placement_failure = validate_building_placement(
            simulation,
            parameters.building_kind,
            parameters.position,
            ignore_construction_id=automation.automation_id,
        )
        if placement_failure is not None:
            # Another real building may have covered this non-reserving blueprint.
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.FAILED, placement_failure.code
            )
            automation_assignments.release_automation(simulation, automation)
            start_next_construction(simulation)
            return
        site_occupants = frozenset(
            entity_id for cell in site_cells for entity_id in simulation.occupancy.occupants(cell)
        )
        if site_occupants:
            wait_for_site(simulation, automation, "SITE_OCCUPIED")
            return
        if not parameters.cost_paid:
            cost = parameters.building_kind.profile.construction_cost
            balance = simulation.resources.get(automation.owner_id, 0)
            if balance < cost:
                wait_for_site(simulation, automation, "INSUFFICIENT_RESOURCES")
                return
            simulation.resources[automation.owner_id] = balance - cost
            parameters.cost_paid = True
        entity_id = allocate_entity_id(simulation, parameters.building_kind.value)
        building = Entity(
            entity_id,
            parameters.building_kind,
            automation.owner_id,
            parameters.position,
            max(
                1,
                round(
                    parameters.building_kind.profile.max_health
                    * parameters.construction_value
                    / parameters.required_value
                ),
            ),
            construction_progress=parameters.construction_value / parameters.required_value,
        )
        simulation.occupancy.place(entity_id, building.occupied_cells)
        simulation.entities[entity_id] = building
        parameters.site_entity_id = entity_id
        simulation._invalidate_navigation_cache()
    if automation.status is AutomationStatus.WAITING:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.ACTIVE, "CONSTRUCTION_READY"
        )
    building = simulation.entities[parameters.site_entity_id]
    for builder_id in builders_in_range:
        simulation.entities[builder_id].state = UnitState.BUILDING
    work = sum(
        simulation.entities[builder_id].profile.build_speed for builder_id in builders_in_range
    )
    previous_health = max(
        1,
        round(
            building.kind.profile.max_health
            * parameters.construction_value
            / parameters.required_value
        ),
    )
    parameters.construction_value = min(
        parameters.required_value,
        parameters.construction_value + work,
    )
    building.construction_progress = parameters.construction_value / parameters.required_value
    building.health = min(
        building.kind.profile.max_health,
        building.health
        + round(building.kind.profile.max_health * building.construction_progress)
        - previous_health,
    )
    if parameters.construction_value < parameters.required_value:
        return
    building.construction_progress = None
    economy.change_income(simulation, building.owner_id, building.profile.income_per_second)
    parameters.constructed_entity_id = building.entity_id
    automation_lifecycle.transition(
        simulation, automation, AutomationStatus.COMPLETED, "CONSTRUCTION_COMPLETED"
    )
    automation_assignments.release_automation(simulation, automation)
    start_next_construction(simulation)


def wait_for_site(simulation: Simulation, automation: Automation, reason: str) -> None:
    if automation.status is AutomationStatus.WAITING and automation.reason_code != reason:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.ACTIVE, "CONSTRUCTION_RECHECKED"
        )
    if automation.status is AutomationStatus.ACTIVE:
        automation_lifecycle.transition(simulation, automation, AutomationStatus.WAITING, reason)


def construction_cells(parameters: ConstructionParameters) -> frozenset[Cell]:
    return footprint_cells(parameters.building_kind, parameters.position)


def construction_distance(point: Point, parameters: ConstructionParameters) -> float:
    width, height = parameters.building_kind.profile.footprint
    nearest_x = min(max(point.x, parameters.position.x), parameters.position.x + width)
    nearest_y = min(max(point.y, parameters.position.y), parameters.position.y + height)
    return point.distance_to(Point(nearest_x, nearest_y))


def construction_interaction_points(
    simulation: Simulation,
    parameters: ConstructionParameters,
    build_range: float,
) -> tuple[Point, ...]:
    width, height = parameters.building_kind.profile.footprint
    site_cells = construction_cells(parameters)
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
                and construction_distance(point, parameters) <= build_range
            ):
                candidates.append(point)
    return tuple(candidates)


def start_next_construction(simulation: Simulation) -> None:
    queued = sorted(
        (
            automation
            for automation in simulation._live_automation_candidates
            if automation.kind is AutomationKind.CONSTRUCTION
            and automation.status is AutomationStatus.WAITING
            and automation.reason_code == "BUILDERS_QUEUED"
        ),
        key=lambda automation: (
            automation.created_tick,
            int(automation.automation_id.removeprefix("automation_")),
        ),
    )
    for automation in queued:
        builder_ids = tuple(automation.entity_ids)
        if not builder_ids:
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.FAILED, "BUILDER_UNAVAILABLE"
            )
            continue
        if any(
            builder_id in simulation.assignments
            or builder_id in simulation.suspended_assignments
            or simulation.entities[builder_id].repair_target_id is not None
            or simulation.entities[builder_id].reclaim_target_id is not None
            for builder_id in builder_ids
        ):
            continue
        for builder_id in builder_ids:
            automation_assignments.assign(simulation, builder_id, automation)
            automation_runtime.initialize_runtime_entity(simulation, automation, builder_id)
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.ACTIVE, "BUILDERS_AVAILABLE"
        )
