"""Validated direct-control and automation command handlers."""

from __future__ import annotations

from math import floor
from typing import TYPE_CHECKING

from airts.automations import (
    Automation,
    AutomationKind,
    DefendParameters,
    EconomyParameters,
    PatrolParameters,
    ProductionParameters,
    ReinforcementParameters,
    RepairParameters,
    RepairPhase,
    assign_formation_slots,
    build_defend_stations,
    build_patrol_waypoints,
    minimum_cost_slot_assignment,
    target_center,
)
from airts.commands import (
    AttackCommand,
    CommandResult,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    StopCommand,
)
from airts.control import ControlAuthority
from airts.events import EventType
from airts.geometry import Point, PolygonRegion, PolylineTarget, SpatialTarget
from airts.navigation.movement import collision_radius
from airts.navigation.pathfinding import PathfindingError, PathResult
from airts.validation import (
    ValidationFailure,
    ValidationPhase,
    validate_positive,
    validate_priority,
)
from airts.world.entities import Entity, UnitState
from airts.world.map_model import Cell, EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def modify_automation(simulation: Simulation, command: ModifyAutomationCommand) -> CommandResult:
    automation, failure = simulation._owned_automation(command.automation_id, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("modify_automation", failure)
    assert automation is not None
    if automation.status.terminal:
        return simulation._reject_validation(
            "modify_automation",
            ValidationFailure(ValidationPhase.CAPABILITY, "AUTOMATION_TERMINAL", "automation_id"),
        )
    if all(
        value is None
        for value in (
            command.title,
            command.priority,
            command.target,
            command.minimum_units,
            command.target_count,
        )
    ):
        return simulation._reject_validation(
            "modify_automation",
            ValidationFailure(ValidationPhase.SCHEMA, "NO_CHANGES", "automation_id"),
        )
    if command.title is not None and not command.title.strip():
        return simulation._reject_validation(
            "modify_automation",
            ValidationFailure(ValidationPhase.SCHEMA, "TITLE_EMPTY", "title"),
        )
    if command.priority is not None:
        failure = validate_priority(command.priority)
        if failure is not None:
            return simulation._reject_validation("modify_automation", failure)
    new_parameters: object | None = None
    if command.target is not None:
        if automation.kind is AutomationKind.PATROL:
            try:
                waypoints = build_patrol_waypoints(command.target, simulation.game_map)
                simulation._validate_paths(tuple(automation.entity_ids), waypoints)
            except (ValueError, PathfindingError) as error:
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(ValidationPhase.PATH, reason(error), "target"),
                )
            indices = {entity_id: 0 for entity_id in automation.entity_ids}
            new_parameters = PatrolParameters(command.target, waypoints, indices)
        elif automation.kind is AutomationKind.DEFEND:
            existing_defend = defend_parameters(automation)
            try:
                stations, slots, _ = _allocate_defend_stations(
                    simulation,
                    command.target,
                    tuple(automation.entity_ids),
                    gathering_point=existing_defend.gathering_point,
                )
                simulation._validate_paths(tuple(automation.entity_ids), tuple(stations.values()))
            except (ValueError, PathfindingError) as error:
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(ValidationPhase.PATH, reason(error), "target"),
                )
            new_parameters = DefendParameters(
                command.target,
                stations,
                gathering_point=existing_defend.gathering_point,
                deployment_slots=slots,
                assembly_radius=(
                    max(point.distance_to(target_center(command.target)) for point in slots)
                    if slots
                    else 0.0
                ),
            )
        elif automation.kind is AutomationKind.PRODUCTION:
            parameters = production_parameters(automation)
            if not parameters.continuous:
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(
                        ValidationPhase.CAPABILITY,
                        "PRODUCTION_DEFENSE_REQUIRES_CONTINUOUS_LOOP",
                        "target",
                    ),
                )
            if not isinstance(command.target, PolygonRegion | PolylineTarget):
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(
                        ValidationPhase.SPATIAL,
                        "PRODUCTION_DEFENSE_REQUIRES_LINE_OR_AREA",
                        "target",
                    ),
                )
            geometry_failure = simulation._validate_geometry(command.target)
            if geometry_failure is not None:
                return simulation._reject_validation("modify_automation", geometry_failure)
            try:
                if isinstance(command.target, PolygonRegion):
                    simulation._gathering_slots(
                        command.target,
                        1,
                        collision_radius(parameters.current_unit_kind),
                    )
                else:
                    build_defend_stations(command.target, ("preview",), simulation.game_map)
            except ValueError as error:
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(ValidationPhase.SPATIAL, reason(error), "target"),
                )
            simulation._attach_production_defense(automation, command.target)
        else:
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(ValidationPhase.CAPABILITY, "TARGET_NOT_EDITABLE", "target"),
            )
    if command.minimum_units is not None:
        if automation.kind is not AutomationKind.REINFORCEMENT:
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "MINIMUM_UNITS_NOT_EDITABLE", "minimum_units"
                ),
            )
        failure = validate_positive(command.minimum_units, "minimum_units")
        if failure is not None:
            return simulation._reject_validation("modify_automation", failure)
    if command.target_count is not None:
        if automation.kind is not AutomationKind.PRODUCTION:
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "TARGET_COUNT_NOT_EDITABLE", "target_count"
                ),
            )
        parameters = production_parameters(automation)
        if command.target_count <= 0 or command.target_count < parameters.produced_count:
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(
                    ValidationPhase.SCHEMA, "TARGET_COUNT_BELOW_PRODUCED", "target_count"
                ),
            )
    if command.title is not None:
        automation.title = command.title.strip()
    if command.priority is not None:
        automation.priority = command.priority
    if new_parameters is not None:
        automation.parameters = new_parameters  # type: ignore[assignment]
    if command.minimum_units is not None:
        reinforcement_parameters(automation).minimum_units = command.minimum_units
    if command.target_count is not None:
        production_parameters(automation).target_count = command.target_count
    automation.modified_tick = simulation.tick
    simulation.events.record(
        simulation.tick,
        EventType.AUTOMATION_MODIFIED,
        automation.automation_id,
        title=automation.title,
        priority=automation.priority,
        parameters=automation.parameters.to_dict(),
    )
    return simulation._accept("modify_automation", automation.automation_id)


def move(simulation: Simulation, command: MoveCommand) -> CommandResult:
    failure = simulation._validate_entities(
        command.entity_ids, command.owner_id, require_movable=True
    )
    if failure is not None:
        return simulation._reject_validation("move", failure)
    if not simulation.game_map.is_passable(command.target):
        return simulation._reject_validation(
            "move",
            ValidationFailure(
                ValidationPhase.SPATIAL,
                "TARGET_NOT_PASSABLE",
                "target",
                {"target": [command.target.x, command.target.y]},
            ),
        )
    try:
        destinations = simulation._allocate_destinations(command.entity_ids, command.target)
        paths = simulation._plan_group_paths(command.entity_ids, destinations)
    except PathfindingError as error:
        return simulation._reject_validation(
            "move",
            ValidationFailure(
                ValidationPhase.PATH,
                str(error),
                "target",
                {"target": [command.target.x, command.target.y]},
            ),
        )
    simulation._manual_override_many(command.entity_ids)
    path_source = "human_group" if len(command.entity_ids) > 128 else "human"
    for entity_id in command.entity_ids:
        simulation.entities[entity_id].pursue_target = False
        simulation._start_path(
            simulation.entities[entity_id],
            destinations[entity_id],
            paths[entity_id],
            path_source,
            UnitState.MOVING,
        )
    if len(command.entity_ids) > 128:
        _prepend_coherent_staging_leg(simulation, command.entity_ids, command.target)
    return simulation._accept("move")


def _prepend_coherent_staging_leg(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    target: Point,
) -> None:
    center = Point(
        sum(simulation.entities[entity_id].position.x for entity_id in entity_ids)
        / len(entity_ids),
        sum(simulation.entities[entity_id].position.y for entity_id in entity_ids)
        / len(entity_ids),
    )
    offset_x = target.x - center.x
    offset_y = target.y - center.y
    length = (offset_x * offset_x + offset_y * offset_y) ** 0.5
    if length <= 1e-9:
        return
    stage_distance = min(12.0, length)
    offset_x *= stage_distance / length
    offset_y *= stage_distance / length
    blocked = _group_route_obstacles(simulation, entity_ids)
    for entity_id in entity_ids:
        entity = simulation.entities[entity_id]
        if not entity.path:
            continue
        stage = Point(entity.position.x + offset_x, entity.position.y + offset_y)
        if not _straight_segment_is_passable(simulation, entity.position, stage, blocked):
            continue
        entity.path.insert(0, stage)
        destination = entity.move_target
        if destination is not None and _straight_segment_is_passable(
            simulation,
            entity.position,
            destination,
            blocked,
        ):
            entity.path[:] = [stage, destination]
        entity.progress_target = stage
        entity.progress_distance = stage_distance


def _straight_segment_is_passable(
    simulation: Simulation,
    start: Point,
    end: Point,
    blocked: frozenset[Cell],
) -> bool:
    sample_count = max(1, int(start.distance_to(end) * 4))
    previous = simulation.game_map.cell_for(start)
    for index in range(1, sample_count + 1):
        fraction = index / sample_count
        point = Point(
            start.x + (end.x - start.x) * fraction,
            start.y + (end.y - start.y) * fraction,
        )
        cell = simulation.game_map.cell_for(point)
        if cell == previous:
            continue
        if cell[0] != previous[0] and cell[1] != previous[1]:
            corners = ((cell[0], previous[1]), (previous[0], cell[1]))
            if any(
                not simulation.game_map.is_cell_passable(corner) or corner in blocked
                for corner in corners
            ):
                return False
        if not simulation.game_map.is_cell_passable(cell) or cell in blocked:
            return False
        previous = cell
    return True


def _group_route_obstacles(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
) -> frozenset[Cell]:
    """Treat held and stationary hostile units as fixed route obstacles for large orders."""

    blocked = set(simulation._building_cells())
    if len(entity_ids) <= 128:
        return frozenset(blocked)
    selected = frozenset(entity_ids)
    owners = {simulation.entities[entity_id].owner_id for entity_id in entity_ids}
    for other_id, other in simulation.entities.items():
        if other_id in selected or not other.is_movable or other.path:
            continue
        if other.state is UnitState.HOLDING or other.owner_id not in owners:
            blocked.update(simulation._cells_at(other, other.position))
    return frozenset(blocked)


def plan_group_paths(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    destinations: dict[str, Point],
) -> dict[str, PathResult]:
    blocked = _group_route_obstacles(simulation, entity_ids)
    if len(entity_ids) <= 32:
        return {
            entity_id: simulation._routes.dynamic_path(
                simulation.entities[entity_id].position,
                destinations[entity_id],
                blocked,
            )
            for entity_id in entity_ids
        }
    center = Point(
        sum(point.x for point in destinations.values()) / len(destinations),
        sum(point.y for point in destinations.values()) / len(destinations),
    )
    anchor_id = min(
        entity_ids,
        key=lambda entity_id: (
            destinations[entity_id].distance_to(center),
            destinations[entity_id].y,
            destinations[entity_id].x,
            entity_id,
        ),
    )
    anchor = destinations[anchor_id]
    route_anchors: tuple[Point, ...] = (anchor,)
    route_anchor_by_entity = dict.fromkeys(entity_ids, anchor)
    if len(entity_ids) > 128 and (blocked or not simulation._all_terrain_passable):
        start_center = Point(
            sum(simulation.entities[entity_id].position.x for entity_id in entity_ids)
            / len(entity_ids),
            sum(simulation.entities[entity_id].position.y for entity_id in entity_ids)
            / len(entity_ids),
        )
        direction_x = center.x - start_center.x
        direction_y = center.y - start_center.y
        length = max((direction_x * direction_x + direction_y * direction_y) ** 0.5, 1.0)
        lateral_x = -direction_y / length
        lateral_y = direction_x / length
        ordered_destinations = sorted(
            set(destinations.values()),
            key=lambda point: (
                (point.x - center.x) * lateral_x + (point.y - center.y) * lateral_y,
                point.distance_to(center),
                point.y,
                point.x,
            ),
        )
        route_count = min(8, (len(entity_ids) + 99) // 100)
        route_anchors = tuple(
            ordered_destinations[
                min(
                    len(ordered_destinations) - 1,
                    (2 * route_index + 1) * len(ordered_destinations) // (2 * route_count),
                )
            ]
            for route_index in range(route_count)
        )
        ordered_route_entities = sorted(
            entity_ids,
            key=lambda entity_id: (
                (simulation.entities[entity_id].position.x - start_center.x) * lateral_x
                + (simulation.entities[entity_id].position.y - start_center.y) * lateral_y,
                entity_id,
            ),
        )
        route_anchor_by_entity = {
            entity_id: route_anchors[
                min(
                    len(route_anchors) - 1,
                    route_index * len(route_anchors) // len(ordered_route_entities),
                )
            ]
            for route_index, entity_id in enumerate(ordered_route_entities)
        }
    branch_distance = 8
    paths: dict[str, PathResult] = {}
    for entity_id in sorted(entity_ids):
        start = simulation.entities[entity_id].position
        route_anchor = route_anchor_by_entity[entity_id]
        shared = simulation._routes.shared_path(start, route_anchor, blocked)
        destination = destinations[entity_id]
        if destination == route_anchor:
            paths[entity_id] = shared
            continue
        destination_cell = simulation.game_map.cell_for(destination)
        branch_index = next(
            (
                index
                for index, cell in enumerate(shared.cells)
                if abs(cell[0] - destination_cell[0]) + abs(cell[1] - destination_cell[1])
                <= branch_distance
            ),
            len(shared.cells) - 1,
        )
        junction = start if branch_index == 0 else shared.waypoints[branch_index - 1]
        local = simulation._routes.local_path(junction, destination, blocked)
        prefix_cost = sum(
            simulation.game_map.terrain_at_cell(cell).movement_cost
            for cell in shared.cells[1 : branch_index + 1]
        )
        paths[entity_id] = PathResult(
            shared.cells[: branch_index + 1] + local.cells[1:],
            shared.waypoints[:branch_index] + local.waypoints,
            prefix_cost + local.cost,
        )
    return paths


def attack(simulation: Simulation, command: AttackCommand) -> CommandResult:
    failure = simulation._validate_entities(
        command.entity_ids, command.owner_id, require_movable=True
    )
    if failure is not None:
        return simulation._reject_validation("attack", failure)
    target = simulation.entities.get(command.target_entity_id)
    if target is None:
        return simulation._reject_validation(
            "attack",
            ValidationFailure(ValidationPhase.REFERENCE, "UNKNOWN_TARGET", "target_entity_id"),
        )
    if target.owner_id == command.owner_id:
        return simulation._reject_validation(
            "attack",
            ValidationFailure(ValidationPhase.OWNERSHIP, "TARGET_IS_FRIENDLY", "target_entity_id"),
        )
    for entity_id in command.entity_ids:
        entity = simulation.entities[entity_id]
        if entity.kind.profile.attack_damage <= 0:
            return simulation._reject_validation(
                "attack",
                ValidationFailure(ValidationPhase.CAPABILITY, "ENTITY_CANNOT_ATTACK", "entity_ids"),
            )
    simulation._manual_override_many(command.entity_ids)
    for entity_id in command.entity_ids:
        entity = simulation.entities[entity_id]
        entity.path.clear()
        entity.move_target = None
        simulation._reset_movement_liveness(entity, clear_stop=True)
        entity.attack_target_id = target.entity_id
        entity.pursue_target = True
        entity.state = UnitState.ATTACKING
    return simulation._accept("attack")


def stop(
    simulation: Simulation, command: StopCommand | HoldPositionCommand, *, hold: bool
) -> CommandResult:
    failure = simulation._validate_entities(command.entity_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("hold_position" if hold else "stop", failure)
    simulation._manual_override_many(command.entity_ids)
    for entity_id in command.entity_ids:
        entity = simulation.entities[entity_id]
        entity.path.clear()
        entity.move_target = None
        entity.pursue_target = False
        entity.state = UnitState.HOLDING if hold and entity.is_movable else UnitState.IDLE
        simulation._reset_movement_liveness(entity, clear_stop=True)
        simulation._movement_blocked.discard(entity_id)
    return simulation._accept("hold_position" if hold else "stop")


def create_patrol(simulation: Simulation, command: CreatePatrolCommand) -> CommandResult:
    failure = simulation._validate_automation_common(
        command.entity_ids,
        command.owner_id,
        command.priority,
        command.title,
        require_movable=True,
    )
    if failure is not None:
        return simulation._reject_validation("create_patrol", failure)
    try:
        waypoints = build_patrol_waypoints(command.target, simulation.game_map)
        simulation._validate_paths(command.entity_ids, waypoints)
    except (ValueError, PathfindingError) as error:
        return simulation._reject_validation(
            "create_patrol",
            ValidationFailure(ValidationPhase.PATH, reason(error), "target"),
        )
    automation = simulation._new_automation(
        AutomationKind.PATROL,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(command.entity_ids),
        PatrolParameters(command.target, waypoints),
    )
    failure = simulation._validate_claims(automation, command.entity_ids, replace_existing=True)
    if failure is not None:
        return simulation._reject_validation("create_patrol", failure)
    simulation._activate(automation, command.entity_ids)
    return simulation._accept("create_patrol", automation.automation_id)


def create_defend(simulation: Simulation, command: CreateDefendCommand) -> CommandResult:
    failure = simulation._validate_automation_common(
        command.entity_ids,
        command.owner_id,
        command.priority,
        command.title,
        require_movable=True,
    )
    if failure is not None:
        return simulation._reject_validation("create_defend", failure)
    geometry_failure = simulation._validate_geometry(command.target)
    if geometry_failure is not None:
        return simulation._reject_validation("create_defend", geometry_failure)
    try:
        stations, slots, expanded = _allocate_defend_stations(
            simulation,
            command.target,
            command.entity_ids,
            gathering_point=command.gathering_point,
        )
        if expanded:
            reachable = simulation._gathering_reachable_cache[command.target]
            if any(
                simulation.game_map.cell_for(simulation.entities[entity_id].position)
                not in reachable
                for entity_id in command.entity_ids
            ):
                raise PathfindingError("NO_PATH")
        else:
            simulation._validate_paths(command.entity_ids, tuple(stations.values()))
    except (ValueError, PathfindingError) as error:
        return simulation._reject_validation(
            "create_defend",
            ValidationFailure(ValidationPhase.PATH, reason(error), "target"),
        )
    automation = simulation._new_automation(
        AutomationKind.DEFEND,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(command.entity_ids),
        DefendParameters(
            command.target,
            stations,
            gathering_point=command.gathering_point,
            deployment_slots=slots,
            assembly_radius=(
                max(point.distance_to(target_center(command.target)) for point in slots)
                if slots
                else 0.0
            ),
        ),
    )
    failure = simulation._validate_claims(automation, command.entity_ids, replace_existing=True)
    if failure is not None:
        return simulation._reject_validation("create_defend", failure)
    simulation._activate(automation, command.entity_ids)
    coordinate_shared_defend_stations(simulation, command.target, command.owner_id)
    return simulation._accept("create_defend", automation.automation_id)


def _allocate_defend_stations(
    simulation: Simulation,
    target: SpatialTarget,
    entity_ids: tuple[str, ...],
    *,
    gathering_point: bool,
) -> tuple[dict[str, Point], tuple[Point, ...], bool]:
    """Expand an undersized defend target into deterministic collision-safe holding slots."""

    radius = max(collision_radius(simulation.entities[item].kind) for item in entity_ids)
    if not gathering_point:
        stations = build_defend_stations(target, entity_ids, simulation.game_map)
        if _stations_have_clearance(tuple(stations.values()), radius * 2):
            return stations, (), False
    slots = simulation._gathering_slots(target, len(entity_ids), radius)
    if 128 < len(entity_ids) <= 512:
        stations = minimum_cost_slot_assignment(
            {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids},
            slots,
        )
    elif len(entity_ids) > 512:
        stations = assign_formation_slots(
            {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids},
            slots,
            target_center(target),
        )
    else:
        ordered_ids = sorted(
            entity_ids,
            key=lambda entity_id: (
                simulation.entities[entity_id].position.y,
                simulation.entities[entity_id].position.x,
                entity_id,
            ),
        )
        ordered_slots = sorted(slots, key=lambda point: (point.y, point.x))
        stations = dict(zip(ordered_ids, ordered_slots, strict=True))
    return stations, slots, True


def _stations_have_clearance(stations: tuple[Point, ...], minimum_spacing: float) -> bool:
    bucket_size = minimum_spacing
    buckets: dict[tuple[int, int], list[Point]] = {}
    squared_spacing = (minimum_spacing - 1e-6) ** 2
    for station in stations:
        bucket_x = floor(station.x / bucket_size)
        bucket_y = floor(station.y / bucket_size)
        for neighbor_y in range(bucket_y - 1, bucket_y + 2):
            for neighbor_x in range(bucket_x - 1, bucket_x + 2):
                if any(
                    (station.x - other.x) ** 2 + (station.y - other.y) ** 2 < squared_spacing
                    for other in buckets.get((neighbor_x, neighbor_y), ())
                ):
                    return False
        buckets.setdefault((bucket_x, bucket_y), []).append(station)
    return True


def coordinate_shared_defend_stations(
    simulation: Simulation,
    target: SpatialTarget,
    owner_id: str,
) -> None:
    """Allocate one compact collision-safe station set across matching live defenses."""

    defenses = tuple(
        automation
        for automation in sorted(
            simulation.automations.values(),
            key=lambda item: item.automation_id,
        )
        if automation.kind is AutomationKind.DEFEND
        and automation.owner_id == owner_id
        and not automation.status.terminal
        and isinstance(automation.parameters, DefendParameters)
        and automation.parameters.target == target
    )
    if len(defenses) <= 1:
        return
    entity_ids = tuple(
        entity_id
        for automation in defenses
        for entity_id in automation.entity_ids
        if entity_id in simulation.entities
        and simulation.assignments.get(entity_id) == automation.automation_id
    )
    if not entity_ids:
        return
    radius = max(collision_radius(simulation.entities[entity_id].kind) for entity_id in entity_ids)
    slots = simulation._gathering_slots(target, len(entity_ids), radius)
    stations = _assign_nearest_shared_slots(
        {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids},
        slots,
        target_center(target),
    )
    center = target_center(target)
    for automation in defenses:
        parameters = defend_parameters(automation)
        previous = parameters.stations
        assigned_ids = tuple(
            entity_id
            for entity_id in automation.entity_ids
            if entity_id in stations
            and simulation.assignments.get(entity_id) == automation.automation_id
        )
        parameters.stations = {entity_id: stations[entity_id] for entity_id in assigned_ids}
        parameters.deployment_slots = tuple(
            parameters.stations[entity_id] for entity_id in assigned_ids
        )
        parameters.assembly_radius = max(
            (parameters.stations[entity_id].distance_to(center) for entity_id in assigned_ids),
            default=0.0,
        )
        for entity_id in assigned_ids:
            if previous.get(entity_id) == parameters.stations[entity_id]:
                continue
            entity = simulation.entities[entity_id]
            entity.path.clear()
            entity.move_target = None
            simulation._reset_movement_liveness(entity, clear_stop=True)


def _assign_nearest_shared_slots(
    entity_positions: dict[str, Point],
    slots: tuple[Point, ...],
    center: Point,
) -> dict[str, Point]:
    """Match a changing shared defense without leaving avoidable station permutations."""

    available_entities = set(entity_positions)
    available_slots = set(range(len(slots)))
    assigned: dict[str, Point] = {}
    for _, entity_id, slot_index in sorted(
        (
            (
                entity_positions[entity_id].distance_to(_expanded_shared_slot(slot, center, 0.95)),
                entity_id,
                slot_index,
            )
            for entity_id in entity_positions
            for slot_index, slot in enumerate(slots)
        ),
        key=lambda item: (item[0], item[1], item[2]),
    ):
        if entity_id not in available_entities or slot_index not in available_slots:
            continue
        assigned[entity_id] = slots[slot_index]
        available_entities.remove(entity_id)
        available_slots.remove(slot_index)
        if not available_entities:
            break
    if available_entities or available_slots:
        raise RuntimeError("shared defend station matching did not produce a bijection")
    return assigned


def _expanded_shared_slot(slot: Point, center: Point, radius: float) -> Point:
    offset_x = slot.x - center.x
    offset_y = slot.y - center.y
    distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
    if distance <= 1e-9:
        return Point(slot.x - radius, slot.y)
    return Point(
        slot.x + offset_x * radius / distance,
        slot.y + offset_y * radius / distance,
    )


def create_reinforcement(
    simulation: Simulation, command: CreateReinforcementCommand
) -> CommandResult:
    priority_failure = validate_priority(command.priority)
    count_failure = validate_positive(command.minimum_units, "minimum_units")
    if priority_failure or count_failure:
        failure = priority_failure if priority_failure is not None else count_failure
        assert failure is not None
        return simulation._reject_validation("create_reinforcement", failure)
    failure = simulation._validate_entities(
        command.candidate_entity_ids, command.owner_id, require_movable=True
    )
    if failure is not None:
        return simulation._reject_validation("create_reinforcement", failure)
    target = simulation.automations.get(command.target_automation_id)
    if target is None:
        return simulation._reject_validation(
            "create_reinforcement",
            ValidationFailure(
                ValidationPhase.REFERENCE,
                "UNKNOWN_AUTOMATION",
                "target_automation_id",
            ),
        )
    if target.owner_id != command.owner_id:
        return simulation._reject_validation(
            "create_reinforcement",
            ValidationFailure(
                ValidationPhase.OWNERSHIP,
                "AUTOMATION_NOT_OWNED",
                "target_automation_id",
            ),
        )
    if target.kind not in {AutomationKind.PATROL, AutomationKind.DEFEND} or target.status.terminal:
        return simulation._reject_validation(
            "create_reinforcement",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "INVALID_REINFORCEMENT_TARGET",
                "target_automation_id",
            ),
        )
    automation = simulation._new_automation(
        AutomationKind.REINFORCEMENT,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        [],
        ReinforcementParameters(
            command.target_automation_id,
            list(command.candidate_entity_ids),
            command.minimum_units,
        ),
    )
    simulation._activate(automation, ())
    return simulation._accept("create_reinforcement", automation.automation_id)


def create_repair(simulation: Simulation, command: CreateRepairAndReturnCommand) -> CommandResult:
    if not 0 < command.health_threshold <= 1:
        return simulation._reject_validation(
            "create_repair_and_return",
            ValidationFailure(
                ValidationPhase.SCHEMA,
                "HEALTH_THRESHOLD_OUT_OF_RANGE",
                "health_threshold",
            ),
        )
    rate_failure = validate_positive(command.repair_rate, "repair_rate")
    if rate_failure is not None:
        return simulation._reject_validation("create_repair_and_return", rate_failure)
    failure = simulation._validate_entities(
        command.entity_ids,
        command.owner_id,
        require_movable=True,
    )
    if failure is not None:
        return simulation._reject_validation("create_repair_and_return", failure)
    eligible_ids = tuple(
        entity_id
        for entity_id in command.entity_ids
        if simulation.entities[entity_id].health
        / simulation.entities[entity_id].kind.profile.max_health
        < command.health_threshold
    )
    if not eligible_ids:
        return simulation._reject_validation(
            "create_repair_and_return",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "NO_UNITS_BELOW_REPAIR_THRESHOLD",
                "entity_ids",
                {"health_threshold": command.health_threshold},
            ),
        )
    failure = simulation._validate_automation_common(
        eligible_ids,
        command.owner_id,
        command.priority,
        command.title,
        require_movable=True,
    )
    if failure is not None:
        return simulation._reject_validation("create_repair_and_return", failure)
    destinations: dict[str, str] = {}
    try:
        for entity_id in eligible_ids:
            destinations[entity_id] = simulation._nearest_repair_destination(
                simulation.entities[entity_id]
            )[0]
    except PathfindingError as error:
        return simulation._reject_validation(
            "create_repair_and_return",
            ValidationFailure(ValidationPhase.PATH, str(error), "entity_ids"),
        )
    automation = simulation._new_automation(
        AutomationKind.REPAIR_AND_RETURN,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(eligible_ids),
        RepairParameters(
            command.health_threshold,
            command.repair_rate,
            destinations,
            {
                entity_id: simulation.suspended_assignments.get(entity_id)
                or simulation.assignments.get(entity_id)
                for entity_id in eligible_ids
            },
            {entity_id: RepairPhase.TRAVELING for entity_id in eligible_ids},
            {entity_id: simulation.entities[entity_id].position for entity_id in eligible_ids},
        ),
    )
    failure = simulation._validate_claims(
        automation, eligible_ids, authority=ControlAuthority.EMERGENCY
    )
    if failure is not None:
        return simulation._reject_validation("create_repair_and_return", failure)
    simulation._activate(
        automation,
        eligible_ids,
        authority=ControlAuthority.EMERGENCY,
        suspend=True,
    )
    return simulation._accept("create_repair_and_return", automation.automation_id)


def create_economy(simulation: Simulation, command: CreateEconomyCommand) -> CommandResult:
    priority_failure = validate_priority(command.priority)
    target_failure = validate_positive(command.target_resources, "target_resources")
    if priority_failure or target_failure:
        return simulation._reject_validation("create_economy", priority_failure or target_failure)  # type: ignore[arg-type]
    failure = simulation._validate_entities(command.generator_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("create_economy", failure)
    if any(
        simulation.entities[entity_id].kind is not EntityKind.RESOURCE_GENERATOR
        for entity_id in command.generator_ids
    ):
        return simulation._reject_validation(
            "create_economy",
            ValidationFailure(
                ValidationPhase.CAPABILITY, "ENTITY_NOT_RESOURCE_GENERATOR", "generator_ids"
            ),
        )
    automation = simulation._new_automation(
        AutomationKind.ECONOMY,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(command.generator_ids),
        EconomyParameters(
            list(command.generator_ids),
            command.target_resources,
            starting_resources=simulation.resources.get(command.owner_id, 0),
        ),
    )
    failure = simulation._validate_claims(automation, command.generator_ids)
    if failure is not None:
        return simulation._reject_validation("create_economy", failure)
    simulation._activate(automation, command.generator_ids)
    return simulation._accept("create_economy", automation.automation_id)


def validate_automation_common(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    owner_id: str,
    priority: int,
    title: str,
    *,
    require_movable: bool,
) -> ValidationFailure | None:
    if not title.strip():
        return ValidationFailure(ValidationPhase.SCHEMA, "EMPTY_TITLE", "title")
    priority_failure = validate_priority(priority)
    if priority_failure is not None:
        return priority_failure
    return simulation._validate_entities(entity_ids, owner_id, require_movable=require_movable)


def validate_entities(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    owner_id: str,
    *,
    require_movable: bool = False,
) -> ValidationFailure | None:
    if not entity_ids:
        return ValidationFailure(ValidationPhase.REFERENCE, "NO_ENTITIES", "entity_ids")
    if len(set(entity_ids)) != len(entity_ids):
        return ValidationFailure(ValidationPhase.REFERENCE, "DUPLICATE_ENTITY", "entity_ids")
    unknown = next((item for item in entity_ids if item not in simulation.entities), None)
    if unknown is not None:
        return ValidationFailure(
            ValidationPhase.REFERENCE,
            f"UNKNOWN_ENTITY:{unknown}",
            "entity_ids",
            {"entity_id": unknown},
        )
    unowned = next(
        (item for item in entity_ids if simulation.entities[item].owner_id != owner_id), None
    )
    if unowned is not None:
        return ValidationFailure(
            ValidationPhase.OWNERSHIP,
            f"ENTITY_NOT_OWNED:{unowned}",
            "entity_ids",
            {"entity_id": unowned, "owner_id": simulation.entities[unowned].owner_id},
        )
    if require_movable:
        immovable = next(
            (item for item in entity_ids if not simulation.entities[item].is_movable), None
        )
        if immovable is not None:
            return ValidationFailure(
                ValidationPhase.CAPABILITY,
                f"ENTITY_NOT_MOVABLE:{immovable}",
                "entity_ids",
                {"entity_id": immovable},
            )
    return None


def validate_paths(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    waypoints: tuple[Point, ...],
) -> None:
    building_cells = simulation._building_cells()
    anchor = waypoints[0]
    for entity_id in entity_ids:
        simulation._routes.shared_path(
            simulation.entities[entity_id].position,
            anchor,
            building_cells,
        )
    for waypoint in waypoints[1:]:
        simulation._routes.shared_path(waypoint, anchor, building_cells)


def fail_movement(simulation: Simulation, entity: Entity, reason: str, position: Point) -> None:
    entity.move_target = None
    entity.path.clear()
    entity.state = UnitState.IDLE
    simulation._reset_movement_liveness(entity, clear_stop=True)
    simulation.events.record(
        simulation.tick,
        EventType.MOVEMENT_FAILED,
        entity.entity_id,
        reason=reason,
        position=[position.x, position.y],
    )


def production_parameters(automation: Automation) -> ProductionParameters:
    if not isinstance(automation.parameters, ProductionParameters):
        raise TypeError("automation does not have production parameters")
    return automation.parameters


def defend_parameters(automation: Automation) -> DefendParameters:
    if not isinstance(automation.parameters, DefendParameters):
        raise TypeError("automation does not have defend parameters")
    return automation.parameters


def reinforcement_parameters(automation: Automation) -> ReinforcementParameters:
    if not isinstance(automation.parameters, ReinforcementParameters):
        raise TypeError("automation does not have reinforcement parameters")
    return automation.parameters


def reason(error: Exception) -> str:
    return str(error).upper().replace(" ", "_")
