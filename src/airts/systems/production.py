"""Factory production queues, spawning, and produced-unit assignment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    PatrolParameters,
    ProductionParameters,
    build_defend_stations,
    build_patrol_waypoints,
    target_center,
)
from airts.commands import CommandResult, CreateProductionBatchCommand, CreateProductionCommand
from airts.events import EventType
from airts.geometry import Point, PolygonRegion, PolylineTarget
from airts.navigation.movement import collision_radius
from airts.navigation.pathfinding import PathfindingError
from airts.systems.command_handlers import coordinate_shared_defend_stations
from airts.validation import (
    ValidationFailure,
    ValidationPhase,
    validate_positive,
    validate_priority,
)
from airts.world.entities import Entity, UnitState
from airts.world.map_model import Cell, EntityCategory, EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def create_production(simulation: Simulation, command: CreateProductionCommand) -> CommandResult:
    priority_failure = validate_priority(command.priority)
    count_failure = validate_positive(command.target_count, "target_count")
    if priority_failure or count_failure:
        failure = priority_failure if priority_failure is not None else count_failure
        assert failure is not None
        return simulation._reject_validation("create_production", failure)
    failure = simulation._validate_entities((command.factory_id,), command.owner_id)
    if failure is not None:
        return simulation._reject_validation("create_production", failure)
    factory = simulation.entities[command.factory_id]
    if factory.kind is not EntityKind.FACTORY:
        return simulation._reject_validation(
            "create_production",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "ENTITY_NOT_FACTORY",
                "factory_id",
                {"entity_id": command.factory_id, "kind": factory.kind.value},
            ),
        )
    if command.unit_kind.profile.category is not EntityCategory.UNIT:
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
        geometry_failure = simulation._validate_geometry(command.defend_target)
        if geometry_failure is not None:
            return simulation._reject_validation("create_production", geometry_failure)
        try:
            if isinstance(command.defend_target, PolygonRegion):
                simulation._gathering_slots(
                    command.defend_target,
                    1,
                    collision_radius(command.unit_kind),
                )
            else:
                build_defend_stations(command.defend_target, ("preview",), simulation.game_map)
        except ValueError as error:
            return simulation._reject_validation(
                "create_production",
                ValidationFailure(
                    ValidationPhase.SPATIAL,
                    reason(error),
                    "defend_target",
                ),
            )
    if command.patrol_target is not None:
        geometry_failure = simulation._validate_geometry(command.patrol_target)
        if geometry_failure is not None:
            return simulation._reject_validation("create_production", geometry_failure)
        try:
            build_patrol_waypoints(command.patrol_target, simulation.game_map)
        except ValueError as error:
            return simulation._reject_validation(
                "create_production",
                ValidationFailure(
                    ValidationPhase.SPATIAL,
                    reason(error),
                    "patrol_target",
                ),
            )
    if command.continuous:
        simulation._supersede_continuous_production(command.factory_id)
    else:
        simulation._preempt_continuous_production(command.factory_id)
    build_ticks = simulation.PRODUCTION_BUILD_TICKS
    automation = simulation._new_automation(
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
        ),
    )
    if simulation.assignments.get(command.factory_id) is not None:
        simulation._activate(automation, ())
        simulation._transition(automation, AutomationStatus.WAITING, "FACTORY_QUEUED")
        return simulation._accept("create_production", automation.automation_id)
    failure = simulation._validate_claims(automation, (command.factory_id,))
    if failure is not None:
        return simulation._reject_validation("create_production", failure)
    simulation._activate(automation, (command.factory_id,))
    factory.state = UnitState.PRODUCING
    simulation._record_production_started(automation)
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
        simulation._transition(incumbent, AutomationStatus.PAUSED, "USER_QUEUE_PREEMPTING")
        simulation._transition(incumbent, AutomationStatus.WAITING, "FACTORY_QUEUED")
    else:
        simulation._transition(incumbent, AutomationStatus.WAITING, "FACTORY_QUEUED")
    simulation._release_automation(incumbent)


def create_production_batch(
    simulation: Simulation, command: CreateProductionBatchCommand
) -> CommandResult:
    if not command.sequence:
        return simulation._reject_validation(
            "create_production_batch",
            ValidationFailure(ValidationPhase.SCHEMA, "EMPTY_PRODUCTION_SEQUENCE", "sequence"),
        )
    for kind, quantity in command.sequence:
        if kind.profile.category is not EntityCategory.UNIT or quantity <= 0:
            return simulation._reject_validation(
                "create_production_batch",
                ValidationFailure(
                    ValidationPhase.SCHEMA, "INVALID_PRODUCTION_SEQUENCE", "sequence"
                ),
            )
    first_kind, first_quantity = command.sequence[0]
    result = simulation._create_production(
        CreateProductionCommand(
            command.factory_id,
            first_kind,
            sum(quantity for _, quantity in command.sequence),
            title=command.title,
            priority=command.priority,
            owner_id=command.owner_id,
            original_instruction=command.original_instruction,
        )
    )
    if result.accepted:
        parameters = production_parameters(simulation.automations[result.automation_id or ""])
        parameters.sequence = command.sequence
        parameters.target_count = sum(quantity for _, quantity in command.sequence)
        parameters.unit_kind = first_kind
    return CommandResult(result.accepted, result.reason, result.automation_id)


def supersede_continuous_production(simulation: Simulation, factory_id: str) -> None:
    superseded = tuple(
        automation
        for automation in simulation._factory_production_jobs(factory_id)
        if production_parameters(automation).continuous
    )
    for automation in superseded:
        simulation._transition(
            automation,
            AutomationStatus.CANCELED,
            "SUPERSEDED_BY_LATEST_CONTINUOUS_PRODUCTION",
        )
        simulation._release_automation(automation)
    if superseded:
        simulation._start_next_production(factory_id)


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
            simulation._transition(automation, AutomationStatus.PAUSED, "FACTORY_UNAVAILABLE")
        return
    unit_kind = parameters.current_unit_kind
    cost = unit_kind.profile.production_cost
    if not parameters.cost_paid:
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
    spawn = simulation._find_spawn_point(simulation.entities[parameters.factory_id])
    if spawn is None:
        if automation.status is AutomationStatus.ACTIVE:
            simulation._transition(automation, AutomationStatus.WAITING, "SPAWN_BLOCKED")
        return
    if automation.status is AutomationStatus.WAITING:
        simulation._transition(automation, AutomationStatus.ACTIVE, "SPAWN_AVAILABLE")
    parameters.progress_ticks = 0
    parameters.cost_paid = False
    entity_id = simulation._spawn_unit(automation, parameters, spawn)
    parameters.produced_count += 1
    parameters.produced_entity_ids.append(entity_id)
    if parameters.sequence:
        parameters.sequence_produced += 1
        if parameters.sequence_produced >= parameters.sequence[parameters.sequence_index][1]:
            parameters.sequence_index += 1
            parameters.sequence_produced = 0
    if parameters.defend_target is not None:
        simulation._assign_produced_defender(automation, parameters, entity_id)
    elif parameters.patrol_target is not None:
        simulation._assign_produced_patroller(automation, parameters, entity_id)
    simulation.events.record(
        simulation.tick,
        EventType.PRODUCTION_COMPLETED,
        entity_id,
        automation_id=automation.automation_id,
        factory_id=parameters.factory_id,
        produced_count=parameters.produced_count,
    )
    if not parameters.continuous and parameters.produced_count >= parameters.target_count:
        simulation._transition(automation, AutomationStatus.COMPLETED, "TARGET_COUNT_REACHED")
        simulation._release_automation(automation)
        simulation._start_next_production(parameters.factory_id)


def factory_production_jobs(simulation: Simulation, factory_id: str) -> tuple[Automation, ...]:
    return tuple(
        sorted(
            (
                automation
                for automation in simulation.automations.values()
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
            for automation in simulation._factory_production_jobs(factory_id)
            if automation.status is AutomationStatus.WAITING
            and automation.reason_code == "FACTORY_QUEUED"
        ),
        None,
    )
    if queued is None or factory_id not in simulation.entities:
        return
    simulation._assign(factory_id, queued)
    simulation._transition(queued, AutomationStatus.ACTIVE, "FACTORY_QUEUE_STARTED")
    simulation.entities[factory_id].state = UnitState.PRODUCING
    simulation._record_production_started(queued)


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
    while True:
        unit_kind = parameters.current_unit_kind
        entity_id = f"{unit_kind.value}_{simulation._next_entity_number:03d}"
        simulation._next_entity_number += 1
        if entity_id not in simulation.entities:
            break
    entity = Entity(
        entity_id=entity_id,
        kind=unit_kind,
        owner_id=automation.owner_id,
        position=position,
        health=unit_kind.profile.max_health,
    )
    simulation.entities[entity_id] = entity
    simulation.occupancy.place(entity_id, entity.occupied_cells)
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
            simulation._start_path(
                entity,
                parameters.rally_point,
                path,
                automation.automation_id,
                UnitState.MOVING,
            )
    return entity_id


def assign_produced_defender(
    simulation: Simulation,
    production: Automation,
    parameters: ProductionParameters,
    entity_id: str,
) -> None:
    target = parameters.defend_target
    assert target is not None
    defend = simulation.automations.get(parameters.defend_automation_id or "")
    if defend is None or defend.status.terminal:
        gathering_point = isinstance(target, PolygonRegion)
        if gathering_point:
            slots = simulation._gathering_slots(
                target, 1, collision_radius(simulation.entities[entity_id].kind)
            )
            stations = {entity_id: slots[0]}
        else:
            stations = build_defend_stations(target, (entity_id,), simulation.game_map)
            slots = ()
        defend = simulation._new_automation(
            AutomationKind.DEFEND,
            f"Defend {production.title}",
            production.owner_id,
            production.priority,
            production.original_instruction,
            [entity_id],
            DefendParameters(
                target,
                stations,
                gathering_point=gathering_point,
                deployment_slots=slots,
                assembly_radius=(slots[0].distance_to(target_center(target)) if slots else 0.0),
            ),
        )
        simulation._activate(defend, (entity_id,))
        parameters.defend_automation_id = defend.automation_id
        coordinate_shared_defend_stations(simulation, target, production.owner_id)
        return
    if entity_id not in defend.entity_ids:
        defend.entity_ids.append(entity_id)
    defend_parameters = _defend_parameters(defend)
    if defend_parameters.gathering_point:
        slot_index = len(defend.entity_ids) - 1
        slots = simulation._gathering_slots(
            target,
            slot_index + 1,
            max(collision_radius(simulation.entities[item].kind) for item in defend.entity_ids),
        )
        defend_parameters.deployment_slots = slots
        station = slots[slot_index]
        defend_parameters.stations[entity_id] = station
        defend_parameters.assembly_radius = max(
            defend_parameters.assembly_radius,
            station.distance_to(target_center(target)),
        )
    else:
        defend_parameters.stations = build_defend_stations(
            target,
            tuple(defend.entity_ids),
            simulation.game_map,
        )
        for defender_id in defend.entity_ids:
            simulation._assign(defender_id, defend)
            simulation._initialize_runtime_entity(defend, defender_id)
        coordinate_shared_defend_stations(simulation, target, production.owner_id)
        return
    simulation._assign(entity_id, defend)
    simulation._initialize_runtime_entity(defend, entity_id)
    coordinate_shared_defend_stations(simulation, target, production.owner_id)


def attach_production_defense(
    simulation: Simulation,
    production: Automation,
    target: PolygonRegion | PolylineTarget,
) -> None:
    parameters = production_parameters(production)
    parameters.rally_point = None
    parameters.patrol_target = None
    parameters.patrol_automation_id = None
    parameters.defend_target = target
    defend = simulation.automations.get(parameters.defend_automation_id or "")
    produced_ids = [
        entity_id
        for entity_id in parameters.produced_entity_ids
        if entity_id in simulation.entities
        and simulation.entities[entity_id].owner_id == production.owner_id
        and simulation.entities[entity_id].is_movable
    ]
    if defend is not None and not defend.status.terminal:
        if isinstance(target, PolygonRegion):
            radius = max(
                (collision_radius(simulation.entities[item].kind) for item in produced_ids),
                default=collision_radius(parameters.current_unit_kind),
            )
            slots = simulation._gathering_slots(target, max(1, len(produced_ids)), radius)
            stations = dict(zip(produced_ids, slots, strict=False))
            parameters_for_defend = DefendParameters(
                target,
                stations,
                gathering_point=True,
                deployment_slots=slots[: len(produced_ids)],
                assembly_radius=max(
                    (
                        slot.distance_to(target_center(target))
                        for slot in slots[: len(produced_ids)]
                    ),
                    default=0.0,
                ),
            )
        else:
            stations = build_defend_stations(target, tuple(produced_ids), simulation.game_map)
            parameters_for_defend = DefendParameters(target, stations)
        defend.entity_ids = list(produced_ids)
        defend.parameters = parameters_for_defend
        for entity_id in produced_ids:
            simulation._assign(entity_id, defend)
            simulation._initialize_runtime_entity(defend, entity_id)
        coordinate_shared_defend_stations(simulation, target, production.owner_id)
        return
    parameters.defend_automation_id = None
    for entity_id in produced_ids:
        simulation._assign_produced_defender(production, parameters, entity_id)


def assign_produced_patroller(
    simulation: Simulation,
    production: Automation,
    parameters: ProductionParameters,
    entity_id: str,
) -> None:
    target = parameters.patrol_target
    assert target is not None
    patrol = simulation.automations.get(parameters.patrol_automation_id or "")
    if patrol is None or patrol.status.terminal:
        waypoints = build_patrol_waypoints(target, simulation.game_map)
        patrol = simulation._new_automation(
            AutomationKind.PATROL,
            f"Patrol {production.title}",
            production.owner_id,
            production.priority,
            production.original_instruction,
            [entity_id],
            PatrolParameters(target, waypoints),
        )
        simulation._activate(patrol, (entity_id,))
        parameters.patrol_automation_id = patrol.automation_id
        return
    if entity_id not in patrol.entity_ids:
        patrol.entity_ids.append(entity_id)
    patrol_parameters = _patrol_parameters(patrol)
    patrol_parameters.waypoint_indices[entity_id] = (
        0
        if isinstance(target, PolylineTarget)
        else (len(patrol.entity_ids) - 1) % len(patrol_parameters.waypoints)
    )
    simulation._assign(entity_id, patrol)
    simulation._initialize_runtime_entity(patrol, entity_id)


def find_spawn_point(simulation: Simulation, factory: Entity) -> Point | None:
    occupied = factory.occupied_cells
    candidates: set[Cell] = set()
    for x, y in occupied:
        candidates.update({(x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)})
    for cell in sorted(candidates, key=lambda item: (item[1], item[0])):
        if (
            cell not in occupied
            and simulation.game_map.is_cell_passable(cell)
            and not simulation.occupancy.occupants(cell)
        ):
            return Point(cell[0] + 0.5, cell[1] + 0.5)
    return None


def production_parameters(automation: Automation) -> ProductionParameters:
    if not isinstance(automation.parameters, ProductionParameters):
        raise TypeError("automation does not have production parameters")
    return automation.parameters


def _defend_parameters(automation: Automation) -> DefendParameters:
    if not isinstance(automation.parameters, DefendParameters):
        raise TypeError("automation does not have defend parameters")
    return automation.parameters


def _patrol_parameters(automation: Automation) -> PatrolParameters:
    if not isinstance(automation.parameters, PatrolParameters):
        raise TypeError("automation does not have patrol parameters")
    return automation.parameters


def reason(error: Exception) -> str:
    return str(error).upper().replace(" ", "_")
