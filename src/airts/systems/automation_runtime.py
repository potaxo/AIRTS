"""Shared runtime initialization and defend-formation maintenance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    assign_formation_slots,
    build_defend_stations,
    defend_parameters,
    patrol_parameters,
    target_center,
)
from airts.contracts.spatial import Point, PolylineTarget, SpatialTarget
from airts.navigation.collision.geometry import collision_radius
from airts.systems import movement
from airts.world.entities import UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def initialize_runtime_entity(
    simulation: Simulation, automation: Automation, entity_id: str
) -> None:
    entity = simulation.entities[entity_id]
    entity.repair_target_id = None
    entity.reclaim_target_id = None
    entity.clear_recovery_request()
    entity.path.clear()
    entity.hold_anchor = None
    entity.defend_arrived = False
    entity.recovery_path.clear()
    entity.recovery_blocked_cells = None
    entity.recovery_retry_tick = entity.contact_wait_ticks = 0
    entity.move_target = None
    entity.move_queue.clear()
    entity.attack_gather_point = None
    entity.attack_target_id = None
    entity.pursue_target = False
    movement.reset_movement_liveness(entity)
    if automation.kind is AutomationKind.PATROL:
        parameters = patrol_parameters(automation)
        parameters.waypoint_indices.setdefault(entity_id, 0)
        entity.state = UnitState.PATROLLING
    elif automation.kind is AutomationKind.DEFEND:
        defense = defend_parameters(automation)
        if entity_id not in defense.stations:
            defense.stations[entity_id] = next(iter(defense.stations.values()))
        entity.state = UnitState.DEFENDING
    elif automation.kind is AutomationKind.PRODUCTION:
        entity.state = UnitState.PRODUCING
    elif automation.kind is AutomationKind.CONSTRUCTION:
        entity.state = UnitState.MOVING
    elif automation.kind is AutomationKind.REPAIR_AND_RETURN:
        entity.state = UnitState.RETURNING
    elif automation.kind is AutomationKind.ECONOMY:
        entity.state = UnitState.IDLE


def refresh_gathering_formation(simulation: Simulation, automation: Automation) -> None:
    if not isinstance(automation.parameters, DefendParameters):
        return
    if simulation._removal_batch is not None:
        simulation._removal_formations[automation.owner_id, automation.parameters.target] = None
        return
    coordinate_shared_defend_stations(simulation, automation.parameters.target, automation.owner_id)


def coordinate_shared_defend_stations(
    simulation: Simulation, target: SpatialTarget, owner_id: str
) -> None:
    automations = [
        automation
        for automation in simulation._live_automation_candidates
        if isinstance(automation.parameters, DefendParameters)
        and automation.parameters.target == target
        and automation.owner_id == owner_id
        and automation.status
        in {AutomationStatus.ACTIVE, AutomationStatus.WAITING, AutomationStatus.PAUSED}
    ]
    ids = sorted(
        {
            key
            for automation in automations
            for key in automation.entity_ids
            if key in simulation.entities
            and simulation.assignments.get(key) == automation.automation_id
        }
    )
    if not ids:
        return
    id_set = frozenset(ids)
    previous = {
        key: point
        for automation in automations
        for key, point in defend_parameters(automation).stations.items()
        if key in id_set
    }
    if not any(
        defend_parameters(automation).deployment_slots
        or defend_parameters(automation).gathering_point
        for automation in automations
    ):
        stations = build_defend_stations(target, tuple(ids), simulation.game_map)
        slots: tuple[Point, ...] = ()
    else:
        slots = simulation._gathering_slots(
            target,
            len(ids),
            max(collision_radius(simulation.entities[key].kind) for key in ids),
            spread=not any(
                defend_parameters(automation).gathering_point for automation in automations
            )
            and not isinstance(target, PolylineTarget),
        )
        stations = assign_formation_slots(
            {key: simulation.entities[key].position for key in ids},
            slots,
            target_center(target),
            previous,
        )
    center = target_center(target)
    assembly_radius = max((point.distance_to(center) for point in slots), default=0.0)
    for automation in automations:
        parameters = defend_parameters(automation)
        parameters.stations = {
            key: stations[key] for key in automation.entity_ids if key in stations
        }
        parameters.deployment_slots = slots
        parameters.assembly_radius = assembly_radius
        for key, station in parameters.stations.items():
            if previous.get(key) != station:
                entity = simulation.entities[key]
                entity.clear_recovery_request()
                entity.defend_arrived = False
                entity.path.clear()
                entity.move_target = None
                movement.reset_movement_liveness(entity)
