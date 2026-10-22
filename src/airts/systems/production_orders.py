"""Orders attached to newly produced units, separate from queue execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    DefendParameters,
    PatrolParameters,
    ProductionParameters,
    build_patrol_waypoints,
    defend_parameters,
    patrol_parameters,
    production_parameters,
    target_center,
)
from airts.contracts.commands import (
    CommandResult,
    SetProductionDefenseCommand,
    ValidationFailure,
    ValidationPhase,
    error_reason,
    validate_auto_repair,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import PolygonRegion, PolylineTarget
from airts.navigation.collision.geometry import collision_radius
from airts.systems import automation_assignments, automation_lifecycle, automation_runtime
from airts.systems.validation import validate_entities, validate_geometry
from airts.world.entities import Entity, ProductionDeployment
from airts.world.units.catalog import EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def _record_deployment(simulation: Simulation, factory: Entity) -> None:
    simulation.events.record(
        simulation.tick,
        EventType.PRODUCTION_DEPLOYMENT_CHANGED,
        factory.entity_id,
        deployment=(
            None
            if factory.production_deployment is None
            else factory.production_deployment.to_dict()
        ),
    )


def set_production_defense(
    simulation: Simulation, command: SetProductionDefenseCommand
) -> CommandResult:
    failure = validate_entities(simulation, command.factory_ids, command.owner_id)
    if failure is None:
        failure = validate_auto_repair(command.auto_repair)
    if failure is not None:
        return simulation._reject_validation("set_production_defense", failure)
    if any(simulation.entities[key].kind is not EntityKind.FACTORY for key in command.factory_ids):
        return simulation._reject_validation(
            "set_production_defense",
            ValidationFailure(ValidationPhase.CAPABILITY, "ENTITY_NOT_FACTORY", "factory_ids"),
        )
    target = command.target
    if target is not None:
        if not isinstance(target, PolygonRegion | PolylineTarget):
            return simulation._reject_validation(
                "set_production_defense",
                ValidationFailure(
                    ValidationPhase.SPATIAL, "PRODUCTION_DEFENSE_REQUIRES_LINE_OR_AREA", "target"
                ),
            )
        failure = validate_geometry(simulation, target)
        if failure is not None:
            return simulation._reject_validation("set_production_defense", failure)
        try:
            validate_defense_target(
                simulation, target, max(EntityKind.FACTORY.production_kinds, key=collision_radius)
            )
        except ValueError as error:
            return simulation._reject_validation(
                "set_production_defense",
                ValidationFailure(ValidationPhase.SPATIAL, error_reason(error), "target"),
            )
    defense = (
        shared_production_defense(simulation, command.owner_id, target)
        if target is not None
        else None
    )
    for key in command.factory_ids:
        factory = simulation.entities[key]
        factory.production_deployment = (
            ProductionDeployment(
                target, command.auto_repair, defense.automation_id if defense is not None else None
            )
            if target is not None
            else None
        )
        _record_deployment(simulation, factory)
        for production in simulation.production_queue(key):
            parameters = production_parameters(production)
            if target is None:
                parameters.defend_target = None
                parameters.defend_automation_id = None
            else:
                parameters.auto_repair = command.auto_repair
                attach_production_defense(
                    simulation, production, target, auto_repair=command.auto_repair
                )
            production.modified_tick = simulation.tick
            simulation.events.record(
                simulation.tick,
                EventType.AUTOMATION_MODIFIED,
                production.automation_id,
                parameters=parameters.to_dict(),
            )
    if defense is not None:
        parameters_for_defense = defend_parameters(defense)
        if parameters_for_defense.auto_repair != command.auto_repair:
            parameters_for_defense.auto_repair = command.auto_repair
            defense.modified_tick = simulation.tick
            simulation.events.record(
                simulation.tick,
                EventType.AUTOMATION_MODIFIED,
                defense.automation_id,
                parameters=parameters_for_defense.to_dict(),
            )
        synchronize_production_defense(simulation, defense)
    return simulation._accept("set_production_defense")


def validate_defense_target(
    simulation: Simulation, target: PolygonRegion | PolylineTarget, unit_kind: EntityKind
) -> None:
    """Check future defenders with the same body clearance and geometry as deployment."""
    radius = collision_radius(unit_kind)
    if isinstance(target, PolylineTarget):
        automation_runtime.line_defense_slots(simulation, target, 1, radius)
    else:
        simulation._gathering_slots(target, 1, radius)


def shared_production_defense(
    simulation: Simulation, owner_id: str, target: PolygonRegion | PolylineTarget
) -> Automation | None:
    """Reuse one live production defense per owner and exact spatial target."""
    return next(
        (
            automation
            for automation in simulation._live_automation_candidates
            if automation.owner_id == owner_id
            and not automation.status.terminal
            and isinstance(automation.parameters, DefendParameters)
            and automation.parameters.production_defense
            and automation.parameters.target == target
        ),
        None,
    )


def synchronize_production_defense(simulation: Simulation, defense: Automation) -> None:
    """Edits to the shared controller also govern all its factories' future units."""
    parameters = defend_parameters(defense)
    assert isinstance(parameters.target, PolygonRegion | PolylineTarget)
    for factory in sorted(
        (
            entity
            for entity in simulation.entities.values()
            if entity.production_deployment is not None
        ),
        key=lambda entity: entity.entity_id,
    ):
        deployment = factory.production_deployment
        if deployment is None or factory.owner_id != defense.owner_id:
            continue
        previous = simulation.automations.get(deployment.defend_automation_id or "")
        if deployment.defend_automation_id != defense.automation_id and not (
            (previous is None or previous.status.terminal)
            and deployment.target == parameters.target
        ):
            continue
        if (
            deployment.target == parameters.target
            and deployment.auto_repair == parameters.auto_repair
            and deployment.defend_automation_id == defense.automation_id
        ):
            continue
        deployment.target = parameters.target
        deployment.auto_repair = parameters.auto_repair
        deployment.defend_automation_id = defense.automation_id
        _record_deployment(simulation, factory)
    for production in simulation.automations.values():
        source = production.parameters
        if (
            not isinstance(source, ProductionParameters)
            or source.defend_automation_id != defense.automation_id
        ):
            continue
        if (
            source.defend_target == parameters.target
            and source.auto_repair == parameters.auto_repair
        ):
            continue
        source.defend_target = parameters.target
        source.auto_repair = parameters.auto_repair
        production.modified_tick = simulation.tick
        simulation.events.record(
            simulation.tick,
            EventType.AUTOMATION_MODIFIED,
            production.automation_id,
            parameters=source.to_dict(),
        )


def detach_production_defense(simulation: Simulation, defense: Automation) -> None:
    """Canceling the shared defense stops future reinforcements without stopping production."""
    for factory in sorted(
        (
            entity
            for entity in simulation.entities.values()
            if entity.production_deployment is not None
        ),
        key=lambda entity: entity.entity_id,
    ):
        deployment = factory.production_deployment
        if deployment is not None and deployment.defend_automation_id == defense.automation_id:
            factory.production_deployment = None
            _record_deployment(simulation, factory)
    for production in simulation._live_automation_candidates:
        source = production.parameters
        if (
            not isinstance(source, ProductionParameters)
            or source.defend_automation_id != defense.automation_id
        ):
            continue
        source.defend_target = None
        source.defend_automation_id = None
        production.modified_tick = simulation.tick
        simulation.events.record(
            simulation.tick,
            EventType.AUTOMATION_MODIFIED,
            production.automation_id,
            parameters=source.to_dict(),
        )


def assign_produced_defender(
    simulation: Simulation,
    production: Automation,
    parameters: ProductionParameters,
    entity_id: str,
) -> None:
    target = parameters.defend_target
    assert isinstance(target, PolygonRegion | PolylineTarget)
    defend = simulation.automations.get(parameters.defend_automation_id or "")
    if defend is None or defend.status.terminal:
        defend = shared_production_defense(simulation, production.owner_id, target)
        if defend is not None:
            parameters.defend_automation_id = defend.automation_id
            synchronize_production_defense(simulation, defend)
    if defend is None:
        gathering_point = isinstance(target, PolygonRegion)
        if gathering_point:
            slots = simulation._gathering_slots(
                target, 1, collision_radius(simulation.entities[entity_id].kind)
            )
            stations = {entity_id: slots[0]}
        else:
            assert isinstance(target, PolylineTarget)
            stations, slots = automation_runtime.allocate_line_defense(
                simulation, target, (entity_id,)
            )
        defend = automation_lifecycle.new_automation(
            simulation,
            AutomationKind.DEFEND,
            "Production defense",
            production.owner_id,
            production.priority,
            production.original_instruction,
            [entity_id],
            DefendParameters(
                target,
                stations,
                auto_repair=parameters.auto_repair,
                gathering_point=gathering_point,
                deployment_slots=slots,
                assembly_radius=(slots[0].distance_to(target_center(target)) if slots else 0.0),
                production_defense=True,
            ),
        )
        automation_assignments.activate(simulation, defend, (entity_id,))
        for source in simulation._live_automation_candidates:
            source_parameters = source.parameters
            if (
                source.owner_id == production.owner_id
                and isinstance(source_parameters, ProductionParameters)
                and source_parameters.defend_target == target
            ):
                source_parameters.defend_automation_id = defend.automation_id
        synchronize_production_defense(simulation, defend)
        automation_runtime.coordinate_shared_defend_stations(
            simulation, target, production.owner_id
        )
        return
    if entity_id not in defend.entity_ids:
        defend.entity_ids.append(entity_id)
    automation_assignments.assign(simulation, entity_id, defend)
    # Allocate once across the whole defense, preserving existing stations before placing arrivals.
    automation_runtime.coordinate_shared_defend_stations(simulation, target, production.owner_id)
    automation_runtime.initialize_runtime_entity(simulation, defend, entity_id)


def attach_production_defense(
    simulation: Simulation,
    production: Automation,
    target: PolygonRegion | PolylineTarget,
    *,
    auto_repair: bool | None = None,
) -> None:
    parameters = production_parameters(production)
    parameters.rally_point = None
    parameters.patrol_target = None
    parameters.patrol_automation_id = None
    parameters.defend_target = target
    # Retarget only this source's future units; existing defenders keep their orders.
    defense = shared_production_defense(simulation, production.owner_id, target)
    parameters.defend_automation_id = defense.automation_id if defense is not None else None
    if defense is not None:
        if auto_repair is not None:
            if defend_parameters(defense).auto_repair != auto_repair:
                defend_parameters(defense).auto_repair = auto_repair
                defense.modified_tick = simulation.tick
                simulation.events.record(
                    simulation.tick,
                    EventType.AUTOMATION_MODIFIED,
                    defense.automation_id,
                    parameters=defense.parameters.to_dict(),
                )
        synchronize_production_defense(simulation, defense)


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
        patrol = automation_lifecycle.new_automation(
            simulation,
            AutomationKind.PATROL,
            f"Patrol {production.title}",
            production.owner_id,
            production.priority,
            production.original_instruction,
            [entity_id],
            PatrolParameters(target, waypoints, auto_repair=parameters.auto_repair),
        )
        automation_assignments.activate(simulation, patrol, (entity_id,))
        parameters.patrol_automation_id = patrol.automation_id
        return
    if entity_id not in patrol.entity_ids:
        patrol.entity_ids.append(entity_id)
    parameters_for_patrol = patrol_parameters(patrol)
    parameters_for_patrol.waypoint_indices[entity_id] = (
        0
        if isinstance(target, PolylineTarget)
        else (len(patrol.entity_ids) - 1) % len(parameters_for_patrol.waypoints)
    )
    automation_assignments.assign(simulation, entity_id, patrol)
    automation_runtime.initialize_runtime_entity(simulation, patrol, entity_id)
