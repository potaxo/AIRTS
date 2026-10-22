"""Shared runtime initialization and defend-formation maintenance."""

from __future__ import annotations

from math import ceil, floor, hypot, sqrt
from typing import TYPE_CHECKING

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    area_coverage_order,
    assign_formation_slots,
    defend_parameters,
    line_formation_points,
    patrol_parameters,
    target_center,
)
from airts.contracts.spatial import Point, PolylineTarget, SpatialTarget
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.pathfinding import PathfindingError
from airts.systems import movement
from airts.world.entities import UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def allocate_line_defense(
    simulation: Simulation,
    target: PolylineTarget,
    entity_ids: tuple[str, ...],
    previous: dict[str, Point] | None = None,
    existing_slots: tuple[Point, ...] = (),
) -> tuple[dict[str, Point], tuple[Point, ...]]:
    """Keep line coverage, adding parallel ranks when one rank lacks safe capacity."""
    radius = max(collision_radius(simulation.entities[key].kind) for key in entity_ids)
    retained = {key: previous[key] for key in entity_ids if previous and key in previous}
    if existing_slots and len(retained) == len(entity_ids):
        retained_points = set(retained.values())
        blocked = simulation._building_cells() | simulation._terrain_obstacles
        if (
            len(retained_points) == len(entity_ids)
            and retained_points <= set(existing_slots)
            and all(
                movement.position_clear(
                    point, radius, simulation.game_map.width, simulation.game_map.height, blocked
                )
                for point in retained_points
            )
        ):
            # Membership contraction needs no new geometry or rematching: these
            # immutable shared slots were already separated for this formation.
            return retained, existing_slots
    points, expanded = line_defense_slots(
        simulation, target, len(entity_ids), radius, tuple(retained.values())
    )
    if not expanded:
        return dict(zip(entity_ids, points, strict=True)), ()
    return assign_formation_slots(
        {key: simulation.entities[key].position for key in entity_ids},
        points,
        target_center(target),
        previous,
    ), points


def line_defense_slots(
    simulation: Simulation,
    target: PolylineTarget,
    count: int,
    radius: float,
    retained: tuple[Point, ...] = (),
) -> tuple[tuple[Point, ...], bool]:
    """Canonical line geometry for actual armies and future production previews."""
    spacing = max(1.0, radius * 2 + 0.12)
    rank_spacing = radius * 4 + 0.12
    blocked = simulation._building_cells() | simulation._terrain_obstacles
    points = line_formation_points(target, count)
    if all(
        movement.position_clear(
            point, radius, simulation.game_map.width, simulation.game_map.height, blocked
        )
        for point in points
    ) and all(a.distance_to(b) >= spacing - 1e-9 for a, b in zip(points, points[1:], strict=False)):
        return points, False
    length = sum(a.distance_to(b) for a, b in zip(target.points, target.points[1:], strict=False))
    # Short lines may extend as well as thicken; keep the result elongated along
    # the player's line, rather than becoming a disk centered on its midpoint.
    span = max(length, sqrt(count * 2.5) * rank_spacing)
    columns = max(2, floor(span / rank_spacing) + 1)
    slots: list[Point] = []
    buckets: dict[tuple[int, int], list[Point]] = {}
    reachable = simulation._gathering_reachable(target)

    def admit(point: Point) -> None:
        if (
            not movement.position_clear(
                point, radius, simulation.game_map.width, simulation.game_map.height, blocked
            )
            or not reachable[floor(point.y), floor(point.x)]
        ):
            return
        bx, by = floor(point.x / spacing), floor(point.y / spacing)
        for y in range(by - 1, by + 2):
            for x in range(bx - 1, bx + 2):
                for other in buckets.get((x, y), ()):
                    simulation._collision_pair_check_count += 1
                    if point.distance_to(other) < spacing - 1e-8:
                        return
        slots.append(point)
        buckets.setdefault((bx, by), []).append(point)

    for point in retained:
        admit(point)
    if len(slots) == count:
        return tuple(slots), True
    for row in range(
        2 * ceil(hypot(simulation.game_map.width, simulation.game_map.height) / rank_spacing) + 1
    ):
        offset = ((row + 1) // 2) * rank_spacing * (1 if row % 2 else -1)
        candidates = line_formation_points(target, columns, offset=offset, span=span)
        # A partial rank spans the full line; obstacle/growth capacity then uses
        # the remaining evenly distributed candidates without evicting stations.
        preferred = line_formation_points(
            target, min(columns, count - len(slots)), offset=offset, span=span
        )
        for point in preferred + area_coverage_order(candidates):
            admit(point)
            if len(slots) == count:
                return tuple(slots), True
    raise PathfindingError("INSUFFICIENT_LINE_DEFENSE_CAPACITY")


def initialize_runtime_entity(
    simulation: Simulation, automation: Automation, entity_id: str
) -> None:
    entity = simulation.entities[entity_id]
    entity.formation_goal = entity.formation_leg = None
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
    entity.order_queue.clear()
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
    if isinstance(target, PolylineTarget):
        common_slots = defend_parameters(automations[0]).deployment_slots
        if any(
            defend_parameters(task).deployment_slots != common_slots for task in automations[1:]
        ):
            common_slots = ()
        stations, slots = allocate_line_defense(
            simulation, target, tuple(ids), previous, common_slots
        )
        if not slots and any(defend_parameters(task).gathering_point for task in automations):
            slots = tuple(stations.values())
    else:
        slots = simulation._gathering_slots(
            target,
            len(ids),
            max(collision_radius(simulation.entities[key].kind) for key in ids),
            spread=not any(
                defend_parameters(automation).gathering_point for automation in automations
            ),
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
