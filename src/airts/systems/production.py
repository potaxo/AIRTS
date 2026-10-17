"""Factory production queue validation, payment, progress, and spawning."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    ProductionParameters,
    build_patrol_waypoints,
    production_parameters,
)
from airts.contracts.commands import (
    CommandResult,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    ReduceProductionCommand,
    ValidationFailure,
    ValidationPhase,
    error_reason,
    validate_auto_repair,
    validate_positive,
    validate_priority,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PolygonRegion, PolylineTarget
from airts.navigation.pathfinding import PathfindingError
from airts.systems import (
    automation_assignments,
    automation_lifecycle,
    movement,
    production_orders,
)
from airts.systems.entity_registry import allocate_entity_id
from airts.systems.spawning import find_spawn_point
from airts.systems.validation import validate_entities, validate_geometry
from airts.world.entities import Entity, UnitState
from airts.world.units.schema import EntityCategory

if TYPE_CHECKING:
    from airts.simulation import Simulation


def create_production(simulation: Simulation, command: CreateProductionCommand) -> CommandResult:
    failure = validate_auto_repair(command.auto_repair)
    if failure is not None:
        return simulation._reject_validation("create_production", failure)
    priority_failure = validate_priority(command.priority)
    count_failure = validate_positive(command.target_count, "target_count")
    if priority_failure or count_failure:
        failure = priority_failure if priority_failure is not None else count_failure
        assert failure is not None
        return simulation._reject_validation("create_production", failure)
    failure = validate_entities(simulation, (command.factory_id,), command.owner_id)
    if failure is not None:
        return simulation._reject_validation("create_production", failure)
    factory = simulation.entities[command.factory_id]
    if not factory.kind.production_kinds or not factory.is_complete:
        return simulation._reject_validation(
            "create_production",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "ENTITY_NOT_FACTORY" if factory.is_complete else "FACTORY_UNDER_CONSTRUCTION",
                "factory_id",
                {"entity_id": command.factory_id, "kind": factory.kind.value},
            ),
        )
    if command.unit_kind not in factory.kind.production_kinds:
        return simulation._reject_validation(
            "create_production",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "UNSUPPORTED_PRODUCTION_KIND",
                "unit_kind",
            ),
        )
    if command.rally_point is not None and not simulation.game_map.is_passable(command.rally_point):
        return simulation._reject_validation(
            "create_production",
            ValidationFailure(ValidationPhase.SPATIAL, "TARGET_NOT_PASSABLE", "rally_point"),
        )
    if command.defend_target is not None and command.patrol_target is not None:
        return simulation._reject_validation(
            "create_production",
            ValidationFailure(
                ValidationPhase.SCHEMA,
                "MULTIPLE_PRODUCTION_TARGETS",
                "patrol_target",
            ),
        )
    if command.rally_point is not None and (
        command.defend_target is not None or command.patrol_target is not None
    ):
        return simulation._reject_validation(
            "create_production",
            ValidationFailure(
                ValidationPhase.SCHEMA,
                "RALLY_AND_AUTOMATION_TARGET_CONFLICT",
                "rally_point",
            ),
        )
    if command.defend_target is not None:
        if not isinstance(command.defend_target, PolygonRegion | PolylineTarget):
            return simulation._reject_validation(
                "create_production",
                ValidationFailure(
                    ValidationPhase.SPATIAL,
                    "PRODUCTION_DEFENSE_REQUIRES_LINE_OR_AREA",
                    "defend_target",
                ),
            )
        geometry_failure = validate_geometry(simulation, command.defend_target)
        if geometry_failure is not None:
            return simulation._reject_validation("create_production", geometry_failure)
        try:
            production_orders.validate_defense_target(
                simulation, command.defend_target, command.unit_kind
            )
        except ValueError as error:
            return simulation._reject_validation(
                "create_production",
                ValidationFailure(
                    ValidationPhase.SPATIAL,
                    error_reason(error),
                    "defend_target",
                ),
            )
    if command.patrol_target is not None:
        geometry_failure = validate_geometry(simulation, command.patrol_target)
        if geometry_failure is not None:
            return simulation._reject_validation("create_production", geometry_failure)
        try:
            build_patrol_waypoints(command.patrol_target, simulation.game_map)
        except ValueError as error:
            return simulation._reject_validation(
                "create_production",
                ValidationFailure(
                    ValidationPhase.SPATIAL,
                    error_reason(error),
                    "patrol_target",
                ),
            )
    if command.continuous:
        supersede_continuous_production(simulation, command.factory_id)
    else:
        preempt_continuous_production(simulation, command.factory_id)
    build_ticks = command.unit_kind.profile.production_ticks
    automation = automation_lifecycle.new_automation(
        simulation,
        AutomationKind.PRODUCTION,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        [command.factory_id],
        ProductionParameters(
            command.factory_id,
            command.unit_kind,
            command.target_count,
            build_ticks,
            command.rally_point,
            continuous=command.continuous,
            defend_target=command.defend_target,
            patrol_target=command.patrol_target,
            auto_repair=command.auto_repair,
        ),
    )
    if simulation.assignments.get(command.factory_id) is not None:
        automation_assignments.activate(simulation, automation, ())
        if command.defend_target is not None:
            assert isinstance(command.defend_target, PolygonRegion | PolylineTarget)
            production_orders.attach_production_defense(
                simulation, automation, command.defend_target
            )
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.WAITING, "FACTORY_QUEUED"
        )
        return simulation._accept("create_production", automation.automation_id)
    failure = automation_lifecycle.validate_claims(simulation, automation, (command.factory_id,))
    if failure is not None:
        return simulation._reject_validation("create_production", failure)
    automation_assignments.activate(simulation, automation, (command.factory_id,))
    if command.defend_target is not None:
        assert isinstance(command.defend_target, PolygonRegion | PolylineTarget)
        production_orders.attach_production_defense(simulation, automation, command.defend_target)
    factory.state = UnitState.PRODUCING
    record_production_started(simulation, automation)
    return simulation._accept("create_production", automation.automation_id)


def preempt_continuous_production(simulation: Simulation, factory_id: str) -> None:
    incumbent_id = simulation.assignments.get(factory_id)
    incumbent = simulation.automations.get(incumbent_id or "")
    if (
        incumbent is None
        or incumbent.kind is not AutomationKind.PRODUCTION
        or not production_parameters(incumbent).continuous
        or incumbent.status.terminal
    ):
        return
    if incumbent.status is AutomationStatus.WAITING:
        automation_lifecycle.transition(
            simulation, incumbent, AutomationStatus.PAUSED, "USER_QUEUE_PREEMPTING"
        )
        automation_lifecycle.transition(
            simulation, incumbent, AutomationStatus.WAITING, "FACTORY_QUEUED"
        )
    else:
        automation_lifecycle.transition(
            simulation, incumbent, AutomationStatus.WAITING, "FACTORY_QUEUED"
        )
    automation_assignments.release_automation(simulation, incumbent)


def create_production_batch(
    simulation: Simulation, command: CreateProductionBatchCommand
) -> CommandResult:
    if not command.sequence:
        return simulation._reject_validation(
            "create_production_batch",
            ValidationFailure(ValidationPhase.SCHEMA, "EMPTY_PRODUCTION_SEQUENCE", "sequence"),
        )
    failure = validate_entities(simulation, (command.factory_id,), command.owner_id)
    if failure is not None:
        return simulation._reject_validation("create_production_batch", failure)
    source = simulation.entities[command.factory_id]
    for kind, quantity in command.sequence:
        if kind.profile.category is not EntityCategory.UNIT or quantity <= 0:
            return simulation._reject_validation(
                "create_production_batch",
                ValidationFailure(
                    ValidationPhase.SCHEMA, "INVALID_PRODUCTION_SEQUENCE", "sequence"
                ),
            )
        if kind not in source.kind.production_kinds:
            return simulation._reject_validation(
                "create_production_batch",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "UNSUPPORTED_PRODUCTION_KIND", "sequence"
                ),
            )
    first_kind = command.sequence[0][0]
    result = create_production(
        simulation,
        CreateProductionCommand(
            command.factory_id,
            first_kind,
            sum(quantity for _, quantity in command.sequence),
            title=command.title,
            priority=command.priority,
            owner_id=command.owner_id,
            original_instruction=command.original_instruction,
        ),
    )
    if result.accepted:
        parameters = production_parameters(simulation.automations[result.automation_id or ""])
        parameters.sequence = command.sequence
    return result


def reduce_production(simulation: Simulation, command: ReduceProductionCommand) -> CommandResult:
    failure = validate_entities(simulation, (command.factory_id,), command.owner_id)
    if failure is not None:
        return simulation._reject_validation("reduce_production", failure)
    source = simulation.entities[command.factory_id]
    if command.unit_kind not in source.kind.production_kinds or not source.is_complete:
        return simulation._reject_validation(
            "reduce_production",
            ValidationFailure(
                ValidationPhase.CAPABILITY, "UNSUPPORTED_PRODUCTION_KIND", "unit_kind"
            ),
        )
    job = next(
        (
            item
            for item in reversed(factory_production_jobs(simulation, command.factory_id))
            if not (parameters := production_parameters(item)).continuous
            and parameters.remaining_count(command.unit_kind) > 0
        ),
        None,
    )
    if job is None:
        return simulation._reject_validation(
            "reduce_production",
            ValidationFailure(ValidationPhase.CAPABILITY, "NO_PENDING_PRODUCTION", "unit_kind"),
        )
    parameters = production_parameters(job)
    if parameters.target_count - parameters.produced_count == 1:
        automation_lifecycle.transition(
            simulation, job, AutomationStatus.CANCELED, "PLAYER_REDUCED_PRODUCTION"
        )
        automation_assignments.release_automation(simulation, job)
        start_next_production(simulation, command.factory_id)
    else:
        parameters.target_count -= 1
        if parameters.sequence:
            sequence = list(parameters.sequence)
            index = next(
                index
                for index in range(len(sequence) - 1, parameters.sequence_index - 1, -1)
                if sequence[index][0] is command.unit_kind
                and sequence[index][1]
                > (parameters.sequence_produced if index == parameters.sequence_index else 0)
            )
            kind, quantity = sequence[index]
            removing_current = (
                index == parameters.sequence_index and quantity - parameters.sequence_produced == 1
            )
            if quantity == 1:
                sequence.pop(index)
            else:
                sequence[index] = (kind, quantity - 1)
                if removing_current:
                    parameters.sequence_index += 1
            if removing_current:
                parameters.sequence_produced = 0
                parameters.progress_ticks = 0
                parameters.cost_paid = False
            parameters.sequence = tuple(sequence)
            if removing_current:
                parameters.build_ticks = parameters.current_unit_kind.profile.production_ticks
        job.modified_tick = simulation.tick
    simulation.events.record(
        simulation.tick,
        EventType.AUTOMATION_MODIFIED,
        job.automation_id,
        reason="PLAYER_REDUCED_PRODUCTION",
        parameters=parameters.to_dict(),
    )
    return simulation._accept("reduce_production", job.automation_id)


def supersede_continuous_production(simulation: Simulation, factory_id: str) -> None:
    superseded = tuple(
        automation
        for automation in factory_production_jobs(simulation, factory_id)
        if production_parameters(automation).continuous
    )
    for automation in superseded:
        automation_lifecycle.transition(
            simulation,
            automation,
            AutomationStatus.CANCELED,
            "SUPERSEDED_BY_LATEST_CONTINUOUS_PRODUCTION",
        )
        automation_assignments.release_automation(simulation, automation)
    if superseded:
        start_next_production(simulation, factory_id)


def drive_production(simulation: Simulation, automation: Automation) -> None:
    parameters = production_parameters(automation)
    if (
        automation.reason_code == "FACTORY_QUEUE_STARTED"
        and automation.modified_tick == simulation.tick
    ):
        return
    if simulation.assignments.get(parameters.factory_id) != automation.automation_id:
        if (
            automation.status is AutomationStatus.WAITING
            and automation.reason_code == "FACTORY_QUEUED"
        ):
            return
        if automation.status is not AutomationStatus.PAUSED:
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.PAUSED, "FACTORY_UNAVAILABLE"
            )
        return
    unit_kind = parameters.current_unit_kind
    cost = unit_kind.profile.production_cost
    if not parameters.cost_paid:
        balance = simulation.resources.get(automation.owner_id, 0)
        if balance < cost:
            if automation.status is AutomationStatus.ACTIVE:
                automation_lifecycle.transition(
                    simulation, automation, AutomationStatus.WAITING, "INSUFFICIENT_RESOURCES"
                )
            return
        simulation.resources[automation.owner_id] = balance - cost
        parameters.cost_paid = True
        if automation.status is AutomationStatus.WAITING:
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.ACTIVE, "RESOURCES_AVAILABLE"
            )
        simulation.events.record(
            simulation.tick,
            EventType.RESOURCE_CHANGED,
            automation.owner_id,
            amount=-cost,
            balance=simulation.resources[automation.owner_id],
            reason="PRODUCTION_COST",
            automation_id=automation.automation_id,
        )
    if automation.status is AutomationStatus.ACTIVE:
        parameters.progress_ticks += 1
        if parameters.progress_ticks < parameters.build_ticks:
            return
    spawn = find_spawn_point(simulation, simulation.entities[parameters.factory_id], unit_kind)
    if spawn is None:
        if automation.status is AutomationStatus.ACTIVE:
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.WAITING, "SPAWN_BLOCKED"
            )
        return
    if automation.status is AutomationStatus.WAITING:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.ACTIVE, "SPAWN_AVAILABLE"
        )
    parameters.progress_ticks = 0
    parameters.cost_paid = False
    entity_id = spawn_unit(simulation, automation, parameters, spawn)
    parameters.produced_count += 1
    parameters.record_produced_entity(entity_id)
    if parameters.sequence:
        parameters.sequence_produced += 1
        if parameters.sequence_produced >= parameters.sequence[parameters.sequence_index][1]:
            parameters.sequence_index += 1
            parameters.sequence_produced = 0
        if parameters.produced_count < parameters.target_count:
            parameters.build_ticks = parameters.current_unit_kind.profile.production_ticks
    if parameters.defend_target is not None:
        production_orders.assign_produced_defender(simulation, automation, parameters, entity_id)
    elif parameters.patrol_target is not None:
        production_orders.assign_produced_patroller(simulation, automation, parameters, entity_id)
    simulation.events.record(
        simulation.tick,
        EventType.PRODUCTION_COMPLETED,
        entity_id,
        automation_id=automation.automation_id,
        factory_id=parameters.factory_id,
        produced_count=parameters.produced_count,
    )
    if not parameters.continuous and parameters.produced_count >= parameters.target_count:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.COMPLETED, "TARGET_COUNT_REACHED"
        )
        automation_assignments.release_automation(simulation, automation)
        start_next_production(simulation, parameters.factory_id)


def factory_production_jobs(simulation: Simulation, factory_id: str) -> tuple[Automation, ...]:
    return tuple(
        sorted(
            (
                automation
                for automation in simulation._live_automation_candidates
                if automation.kind is AutomationKind.PRODUCTION
                and not automation.status.terminal
                and production_parameters(automation).factory_id == factory_id
            ),
            key=lambda automation: (
                production_parameters(automation).continuous,
                automation.created_tick,
                automation.automation_id,
            ),
        )
    )


def start_next_production(simulation: Simulation, factory_id: str) -> None:
    incumbent_id = simulation.assignments.get(factory_id)
    if incumbent_id is not None:
        incumbent = simulation.automations.get(incumbent_id)
        if incumbent is not None and not incumbent.status.terminal:
            return
    queued = next(
        (
            automation
            for automation in factory_production_jobs(simulation, factory_id)
            if automation.status is AutomationStatus.WAITING
            and automation.reason_code == "FACTORY_QUEUED"
        ),
        None,
    )
    if queued is None or factory_id not in simulation.entities:
        return
    automation_assignments.assign(simulation, factory_id, queued)
    automation_lifecycle.transition(
        simulation, queued, AutomationStatus.ACTIVE, "FACTORY_QUEUE_STARTED"
    )
    simulation.entities[factory_id].state = UnitState.PRODUCING
    record_production_started(simulation, queued)


def record_production_started(simulation: Simulation, automation: Automation) -> None:
    parameters = production_parameters(automation)
    simulation.events.record(
        simulation.tick,
        EventType.PRODUCTION_STARTED,
        automation.automation_id,
        factory_id=parameters.factory_id,
        unit_kind=parameters.unit_kind.value,
        target_count=parameters.target_count,
    )


def spawn_unit(
    simulation: Simulation,
    automation: Automation,
    parameters: ProductionParameters,
    position: Point,
) -> str:
    unit_kind = parameters.current_unit_kind
    entity_id = allocate_entity_id(simulation, unit_kind.value)
    entity = Entity(
        entity_id=entity_id,
        kind=unit_kind,
        owner_id=automation.owner_id,
        position=position,
        health=unit_kind.profile.max_health,
    )
    simulation.entities[entity_id] = entity
    allowed_conflicts = frozenset(
        key
        for cell in entity.occupied_cells
        for key in simulation.occupancy.occupants(cell)
        if simulation.entities[key].is_movable
    )
    simulation.occupancy.place(entity_id, entity.occupied_cells, allowed_conflicts)
    if parameters.rally_point is not None:
        try:
            path = simulation._routes.shared_path(
                position,
                parameters.rally_point,
                simulation._building_cells(),
            )
        except PathfindingError:
            entity.state = UnitState.IDLE
            simulation.events.record(
                simulation.tick,
                EventType.PATHFINDING_FAILED,
                entity_id,
                automation_id=automation.automation_id,
                reason="RALLY_POINT_UNREACHABLE",
                target=[parameters.rally_point.x, parameters.rally_point.y],
            )
        else:
            movement.start_path(
                simulation,
                entity,
                parameters.rally_point,
                path,
                automation.automation_id,
                UnitState.MOVING,
            )
    return entity_id
