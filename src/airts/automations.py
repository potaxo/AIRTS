"""Serializable automation schemas, lifecycle, and geometry planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil, floor, hypot

from airts.geometry import (
    Point,
    PointTarget,
    PolygonRegion,
    PolylineTarget,
    SpatialTarget,
    target_to_dict,
)
from airts.world.map_model import EntityKind, GameMap


class AutomationStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATING = "validating"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in {
            AutomationStatus.COMPLETED,
            AutomationStatus.FAILED,
            AutomationStatus.CANCELED,
        }


class AutomationKind(StrEnum):
    PATROL = "patrol"
    DEFEND = "defend"
    PRODUCTION = "production"
    REINFORCEMENT = "reinforcement"
    REPAIR_AND_RETURN = "repair_and_return"
    ECONOMY = "economy"
    CONSTRUCTION = "construction"


class RepairPhase(StrEnum):
    TRAVELING = "traveling"
    REPAIRING = "repairing"
    RETURNING = "returning"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class AutomationTransition:
    tick: int
    previous: AutomationStatus | None
    current: AutomationStatus
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "previous": None if self.previous is None else self.previous.value,
            "current": self.current.value,
            "reason_code": self.reason_code,
        }


class AutomationTransitionError(ValueError):
    """Raised when an automation lifecycle transition is illegal."""


@dataclass(slots=True)
class PatrolParameters:
    target: SpatialTarget
    waypoints: tuple[Point, ...]
    waypoint_indices: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "target": target_to_dict(self.target),
            "waypoints": [[point.x, point.y] for point in self.waypoints],
            "waypoint_indices": dict(sorted(self.waypoint_indices.items())),
        }


@dataclass(slots=True)
class DefendParameters:
    target: SpatialTarget
    stations: dict[str, Point]
    gathering_point: bool = False
    deployment_slots: tuple[Point, ...] = ()
    assembly_radius: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "target": target_to_dict(self.target),
            "stations": {
                entity_id: [point.x, point.y] for entity_id, point in sorted(self.stations.items())
            },
            "gathering_point": self.gathering_point,
            "deployment_slots": [[point.x, point.y] for point in self.deployment_slots],
            "assembly_radius": self.assembly_radius,
        }


@dataclass(slots=True)
class ProductionParameters:
    factory_id: str
    unit_kind: EntityKind
    target_count: int
    build_ticks: int
    rally_point: Point | None
    produced_count: int = 0
    progress_ticks: int = 0
    produced_entity_ids: list[str] = field(default_factory=list)
    cost_paid: bool = False
    continuous: bool = False
    defend_target: SpatialTarget | None = None
    defend_automation_id: str | None = None
    patrol_target: SpatialTarget | None = None
    patrol_automation_id: str | None = None
    sequence: tuple[tuple[EntityKind, int], ...] = ()
    sequence_index: int = 0
    sequence_produced: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "factory_id": self.factory_id,
            "unit_kind": self.unit_kind.value,
            "target_count": self.target_count,
            "build_ticks": self.build_ticks,
            "rally_point": (
                None if self.rally_point is None else [self.rally_point.x, self.rally_point.y]
            ),
            "produced_count": self.produced_count,
            "progress_ticks": self.progress_ticks,
            "produced_entity_ids": list(self.produced_entity_ids),
            "cost_paid": self.cost_paid,
            "continuous": self.continuous,
            "defend_target": (
                None if self.defend_target is None else target_to_dict(self.defend_target)
            ),
            "defend_automation_id": self.defend_automation_id,
            "patrol_target": (
                None if self.patrol_target is None else target_to_dict(self.patrol_target)
            ),
            "patrol_automation_id": self.patrol_automation_id,
            "sequence": [[kind.value, quantity] for kind, quantity in self.sequence],
            "sequence_index": self.sequence_index,
            "sequence_produced": self.sequence_produced,
        }

    @property
    def current_unit_kind(self) -> EntityKind:
        return self.sequence[self.sequence_index][0] if self.sequence else self.unit_kind


@dataclass(slots=True)
class ConstructionParameters:
    builder_id: str
    building_kind: EntityKind
    position: Point
    required_value: float
    construction_value: float = 0.0
    cost_paid: bool = False
    constructed_entity_id: str | None = None
    builder_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "builder_id": self.builder_id,
            "building_kind": self.building_kind.value,
            "position": [self.position.x, self.position.y],
            "required_value": self.required_value,
            "construction_value": self.construction_value,
            "cost_paid": self.cost_paid,
            "constructed_entity_id": self.constructed_entity_id,
            "builder_ids": list(self.builder_ids),
        }


@dataclass(slots=True)
class ReinforcementParameters:
    target_automation_id: str
    candidate_entity_ids: list[str]
    minimum_units: int
    transferred_entity_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "target_automation_id": self.target_automation_id,
            "candidate_entity_ids": list(self.candidate_entity_ids),
            "minimum_units": self.minimum_units,
            "transferred_entity_ids": list(self.transferred_entity_ids),
        }


@dataclass(slots=True)
class RepairParameters:
    health_threshold: float
    repair_rate: int
    destinations: dict[str, str]
    resume_automation_ids: dict[str, str | None]
    phases: dict[str, RepairPhase]
    return_positions: dict[str, Point] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "health_threshold": self.health_threshold,
            "repair_rate": self.repair_rate,
            "destinations": dict(sorted(self.destinations.items())),
            "resume_automation_ids": dict(sorted(self.resume_automation_ids.items())),
            "phases": {entity_id: phase.value for entity_id, phase in sorted(self.phases.items())},
            "return_positions": {
                entity_id: [point.x, point.y]
                for entity_id, point in sorted(self.return_positions.items())
            },
        }


@dataclass(slots=True)
class EconomyParameters:
    generator_ids: list[str]
    target_resources: int
    income_per_cycle: int = 1000
    income_cycle_ticks: int = 10
    collected: int = 0
    starting_resources: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "generator_ids": list(self.generator_ids),
            "target_resources": self.target_resources,
            "income_per_cycle": self.income_per_cycle,
            "income_cycle_ticks": self.income_cycle_ticks,
            "collected": self.collected,
            "starting_resources": self.starting_resources,
        }


AutomationParameters = (
    PatrolParameters
    | DefendParameters
    | ProductionParameters
    | ReinforcementParameters
    | RepairParameters
    | EconomyParameters
    | ConstructionParameters
)


@dataclass(slots=True)
class Automation:
    automation_id: str
    title: str
    kind: AutomationKind
    owner_id: str
    priority: int
    created_tick: int
    modified_tick: int
    original_instruction: str
    entity_ids: list[str]
    parameters: AutomationParameters
    creation_source: str = "manual"
    model_provider: str | None = None
    status: AutomationStatus = AutomationStatus.PROPOSED
    reason_code: str = "CREATED"
    transition_history: list[AutomationTransition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.transition_history:
            self.transition_history.append(
                AutomationTransition(self.created_tick, None, self.status, self.reason_code)
            )
        if isinstance(self.parameters, PatrolParameters):
            for entity_id in self.entity_ids:
                self.parameters.waypoint_indices.setdefault(entity_id, 0)

    @property
    def template(self) -> str:
        return self.kind.value

    @property
    def has_future_source(self) -> bool:
        return self.kind in {AutomationKind.PRODUCTION, AutomationKind.REINFORCEMENT}

    def transition(self, status: AutomationStatus, tick: int, reason_code: str) -> None:
        if status is self.status:
            if reason_code == self.reason_code:
                return
            raise AutomationTransitionError("same-state transitions cannot change the reason")
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise AutomationTransitionError(
                f"illegal transition: {self.status.value} -> {status.value}"
            )
        previous = self.status
        self.status = status
        self.reason_code = reason_code
        self.modified_tick = tick
        self.transition_history.append(AutomationTransition(tick, previous, status, reason_code))

    def take_next_waypoint(self, entity_id: str) -> Point:
        if not isinstance(self.parameters, PatrolParameters):
            raise TypeError("only patrol automations have cyclic waypoints")
        index = self.parameters.waypoint_indices[entity_id]
        waypoint = self.parameters.waypoints[index]
        self.parameters.waypoint_indices[entity_id] = (index + 1) % len(self.parameters.waypoints)
        return waypoint

    def remove_entity(self, entity_id: str) -> None:
        self.remove_entities(frozenset({entity_id}))

    def remove_entities(self, entity_ids: frozenset[str]) -> None:
        """Detach a group without repeated linear scans of the assignment list."""

        self.entity_ids[:] = [
            entity_id for entity_id in self.entity_ids if entity_id not in entity_ids
        ]
        if isinstance(self.parameters, PatrolParameters):
            for entity_id in entity_ids:
                self.parameters.waypoint_indices.pop(entity_id, None)
        elif isinstance(self.parameters, DefendParameters):
            for entity_id in entity_ids:
                self.parameters.stations.pop(entity_id, None)
        elif isinstance(self.parameters, RepairParameters):
            for entity_id in entity_ids:
                self.parameters.destinations.pop(entity_id, None)
                self.parameters.resume_automation_ids.pop(entity_id, None)
                self.parameters.phases.pop(entity_id, None)
                self.parameters.return_positions.pop(entity_id, None)
        elif isinstance(self.parameters, EconomyParameters):
            self.parameters.generator_ids[:] = [
                entity_id
                for entity_id in self.parameters.generator_ids
                if entity_id not in entity_ids
            ]
        elif isinstance(self.parameters, ConstructionParameters):
            self.parameters.builder_ids[:] = [
                entity_id
                for entity_id in self.parameters.builder_ids
                if entity_id not in entity_ids
            ]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.automation_id,
            "title": self.title,
            "template": self.kind.value,
            "owner_id": self.owner_id,
            "priority": self.priority,
            "created_tick": self.created_tick,
            "modified_tick": self.modified_tick,
            "original_instruction": self.original_instruction,
            "creation_source": self.creation_source,
            "model_provider": self.model_provider,
            "entity_ids": list(self.entity_ids),
            "parameters": self.parameters.to_dict(),
            "status": self.status.value,
            "reason_code": self.reason_code,
            "transition_history": [item.to_dict() for item in self.transition_history],
        }


PatrolAutomation = Automation


_ALLOWED_TRANSITIONS: dict[AutomationStatus, frozenset[AutomationStatus]] = {
    AutomationStatus.PROPOSED: frozenset(
        {AutomationStatus.VALIDATING, AutomationStatus.CANCELED, AutomationStatus.FAILED}
    ),
    AutomationStatus.VALIDATING: frozenset(
        {
            AutomationStatus.ACTIVE,
            AutomationStatus.AWAITING_CONFIRMATION,
            AutomationStatus.FAILED,
            AutomationStatus.CANCELED,
        }
    ),
    AutomationStatus.AWAITING_CONFIRMATION: frozenset(
        {AutomationStatus.ACTIVE, AutomationStatus.CANCELED, AutomationStatus.FAILED}
    ),
    AutomationStatus.ACTIVE: frozenset(
        {
            AutomationStatus.WAITING,
            AutomationStatus.PAUSED,
            AutomationStatus.BLOCKED,
            AutomationStatus.COMPLETED,
            AutomationStatus.FAILED,
            AutomationStatus.CANCELED,
        }
    ),
    AutomationStatus.WAITING: frozenset(
        {
            AutomationStatus.ACTIVE,
            AutomationStatus.PAUSED,
            AutomationStatus.BLOCKED,
            AutomationStatus.COMPLETED,
            AutomationStatus.FAILED,
            AutomationStatus.CANCELED,
        }
    ),
    AutomationStatus.PAUSED: frozenset(
        {
            AutomationStatus.ACTIVE,
            AutomationStatus.WAITING,
            AutomationStatus.CANCELED,
            AutomationStatus.FAILED,
        }
    ),
    AutomationStatus.BLOCKED: frozenset(
        {
            AutomationStatus.ACTIVE,
            AutomationStatus.PAUSED,
            AutomationStatus.FAILED,
            AutomationStatus.CANCELED,
        }
    ),
    AutomationStatus.COMPLETED: frozenset(),
    AutomationStatus.FAILED: frozenset(),
    AutomationStatus.CANCELED: frozenset(),
}


def transition_is_allowed(previous: AutomationStatus, current: AutomationStatus) -> bool:
    return current in _ALLOWED_TRANSITIONS[previous]


def build_patrol_waypoints(target: SpatialTarget, game_map: GameMap) -> tuple[Point, ...]:
    candidates: tuple[Point, ...]
    if isinstance(target, PointTarget):
        center = target.point
        if not game_map.is_passable(center):
            raise ValueError("patrol point is not passable")
        radius = target.radius
        candidates = (
            Point(center.x, center.y - radius),
            Point(center.x + radius, center.y),
            Point(center.x, center.y + radius),
            Point(center.x - radius, center.y),
        )
    elif isinstance(target, PolylineTarget):
        if any(not game_map.is_passable(point) for point in target.points):
            raise ValueError("patrol line contains an invalid waypoint")
        reverse_interior = tuple(reversed(target.points[1:-1]))
        candidates = target.points + reverse_interior
    else:
        if any(not game_map.contains(point) for point in target.points):
            raise ValueError("patrol area extends outside the map")
        candidates = _area_waypoints(target)
    passable = tuple(point for point in candidates if game_map.is_passable(point))
    if not passable:
        raise ValueError("patrol target contains no passable waypoints")
    return passable


def build_defend_stations(
    target: SpatialTarget, entity_ids: tuple[str, ...], game_map: GameMap
) -> dict[str, Point]:
    if isinstance(target, PolylineTarget):
        line_stations = _evenly_spaced_polyline_points(target.points, len(entity_ids))
        if any(not game_map.is_passable(point) for point in line_stations):
            raise ValueError("defend line contains an invalid station")
        return {entity_id: line_stations[index] for index, entity_id in enumerate(entity_ids)}
    candidates = (
        tuple(
            point
            for point in _area_waypoints(target, maximum=max(24, len(entity_ids)))
            if game_map.is_passable(point)
        )
        if isinstance(target, PolygonRegion)
        else build_patrol_waypoints(target, game_map)
    )
    if not candidates:
        raise ValueError("defend target contains no passable stations")
    selected: tuple[Point, ...]
    if len(entity_ids) <= 1:
        selected = (candidates[len(candidates) // 2],)
    elif len(entity_ids) <= len(candidates):
        step = (len(candidates) - 1) / (len(entity_ids) - 1)
        selected = tuple(candidates[round(index * step)] for index in range(len(entity_ids)))
    else:
        selected = tuple(candidates[index % len(candidates)] for index in range(len(entity_ids)))
    return {entity_id: selected[index] for index, entity_id in enumerate(entity_ids)}


def _evenly_spaced_polyline_points(points: tuple[Point, ...], count: int) -> tuple[Point, ...]:
    if count <= 0:
        return ()
    segments = tuple(zip(points, points[1:], strict=False))
    lengths = tuple(first.distance_to(second) for first, second in segments)
    total_length = sum(lengths)
    if total_length <= 1e-9:
        return (points[0],) * count
    stations: list[Point] = []
    segment_index = 0
    distance_before = 0.0
    for index in range(count):
        requested = total_length / 2 if count == 1 else total_length * index / (count - 1)
        while (
            segment_index < len(segments) - 1
            and requested > distance_before + lengths[segment_index]
        ):
            distance_before += lengths[segment_index]
            segment_index += 1
        first, second = segments[segment_index]
        segment_length = lengths[segment_index]
        fraction = 0.0 if segment_length <= 1e-9 else (requested - distance_before) / segment_length
        stations.append(
            Point(
                first.x + (second.x - first.x) * fraction,
                first.y + (second.y - first.y) * fraction,
            )
        )
    return tuple(stations)


def target_center(target: SpatialTarget) -> Point:
    if isinstance(target, PointTarget):
        return target.point
    if isinstance(target, PolygonRegion):
        return target.centroid
    return Point(
        sum(point.x for point in target.points) / len(target.points),
        sum(point.y for point in target.points) / len(target.points),
    )


def assign_formation_slots(
    entity_positions: Mapping[str, Point],
    slots: tuple[Point, ...],
    center: Point,
) -> dict[str, Point]:
    """Match front-to-back unit order to far-to-near slots along the approach axis."""

    if len(entity_positions) != len(slots):
        raise ValueError("formation entity and slot counts must match")
    if not entity_positions:
        return {}
    group_center = Point(
        sum(point.x for point in entity_positions.values()) / len(entity_positions),
        sum(point.y for point in entity_positions.values()) / len(entity_positions),
    )
    direction_x = center.x - group_center.x
    direction_y = center.y - group_center.y
    if hypot(direction_x, direction_y) <= 1e-9:
        ordered_ids = sorted(
            entity_positions,
            key=lambda entity_id: (
                entity_positions[entity_id].y,
                entity_positions[entity_id].x,
                entity_id,
            ),
        )
        ordered_slots = sorted(slots, key=lambda point: (point.y, point.x))
    else:

        def formation_key(point: Point) -> tuple[float, float]:
            forward = point.x * direction_x + point.y * direction_y
            lateral = -point.x * direction_y + point.y * direction_x
            return -forward, lateral

        ordered_ids = sorted(
            entity_positions,
            key=lambda entity_id: (*formation_key(entity_positions[entity_id]), entity_id),
        )
        ordered_slots = sorted(slots, key=lambda point: (*formation_key(point), point.y, point.x))
    return dict(zip(ordered_ids, ordered_slots, strict=True))


def retain_formation_slots(
    entity_positions: Mapping[str, Point],
    slots: tuple[Point, ...],
    center: Point,
    previous_stations: Mapping[str, Point],
) -> dict[str, Point]:
    """Preserve valid unique stations and assign only genuinely displaced entities."""

    if len(entity_positions) != len(slots):
        raise ValueError("formation entity and slot counts must match")
    slot_indices = {slot: index for index, slot in enumerate(slots)}
    available_indices = set(range(len(slots)))
    retained: dict[str, Point] = {}
    displaced: dict[str, Point] = {}
    for entity_id, position in entity_positions.items():
        previous = previous_stations.get(entity_id)
        slot_index = None if previous is None else slot_indices.get(previous)
        if slot_index is None or slot_index not in available_indices:
            displaced[entity_id] = position
            continue
        assert previous is not None
        retained[entity_id] = previous
        available_indices.remove(slot_index)
    free_slots = tuple(slots[index] for index in sorted(available_indices))
    retained.update(assign_formation_slots(displaced, free_slots, center))
    if len(retained) != len(entity_positions) or len(set(retained.values())) != len(retained):
        raise RuntimeError("stable formation matching did not produce a bijection")
    return retained


def minimum_cost_slot_assignment(
    entity_positions: Mapping[str, Point],
    slots: tuple[Point, ...],
) -> dict[str, Point]:
    """Match entities to slots with deterministic minimum total squared travel."""

    count = len(entity_positions)
    if count != len(slots):
        raise ValueError("formation entity and slot counts must match")
    ordered_ids = tuple(sorted(entity_positions))
    ordered_slots = tuple(sorted(slots, key=lambda point: (point.y, point.x)))
    costs = tuple(
        tuple(
            (entity_positions[entity_id].x - slot.x) ** 2
            + (entity_positions[entity_id].y - slot.y) ** 2
            for slot in ordered_slots
        )
        for entity_id in ordered_ids
    )
    row_potential = [0.0] * (count + 1)
    column_potential = [0.0] * (count + 1)
    column_match = [0] * (count + 1)
    predecessor = [0] * (count + 1)
    for row in range(1, count + 1):
        column_match[0] = row
        minimum = [float("inf")] * (count + 1)
        used = [False] * (count + 1)
        column = 0
        while True:
            used[column] = True
            matched_row = column_match[column]
            delta = float("inf")
            next_column = 0
            for candidate_column in range(1, count + 1):
                if used[candidate_column]:
                    continue
                cost = (
                    costs[matched_row - 1][candidate_column - 1]
                    - row_potential[matched_row]
                    - column_potential[candidate_column]
                )
                if cost < minimum[candidate_column]:
                    minimum[candidate_column] = cost
                    predecessor[candidate_column] = column
                if minimum[candidate_column] < delta:
                    delta = minimum[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(count + 1):
                if used[candidate_column]:
                    row_potential[column_match[candidate_column]] += delta
                    column_potential[candidate_column] -= delta
                else:
                    minimum[candidate_column] -= delta
            column = next_column
            if column_match[column] == 0:
                break
        while True:
            previous_column = predecessor[column]
            column_match[column] = column_match[previous_column]
            column = previous_column
            if column == 0:
                break
    return {
        ordered_ids[column_match[column] - 1]: ordered_slots[column - 1]
        for column in range(1, count + 1)
    }


def patrol_formation_waypoint(
    parameters: PatrolParameters,
    entity_ids: tuple[str, ...],
    entity_id: str,
    waypoint_index: int,
    game_map: GameMap,
    slot_index: int | None = None,
) -> Point:
    """Give line patrols same-direction formation slots around each route vertex."""

    base = parameters.waypoints[waypoint_index]
    if not isinstance(parameters.target, PolylineTarget) or len(entity_ids) == 1:
        return base
    previous = parameters.waypoints[(waypoint_index - 1) % len(parameters.waypoints)]
    direction_x = base.x - previous.x
    direction_y = base.y - previous.y
    length = hypot(direction_x, direction_y)
    if length <= 1e-9:
        return base
    direction_x /= length
    direction_y /= length
    if slot_index is None:
        ordered_ids = tuple(sorted(entity_ids))
        slot_index = ordered_ids.index(entity_id)
    columns = min(5, len(entity_ids))
    column = slot_index % columns
    row = slot_index // columns
    lateral = (column - (columns - 1) / 2) * 0.9
    trailing = row * 0.95
    for scale in (1.0, 0.5):
        candidate = Point(
            base.x - direction_y * lateral * scale - direction_x * trailing * scale,
            base.y + direction_x * lateral * scale - direction_y * trailing * scale,
        )
        if game_map.is_passable(candidate):
            return candidate
    return base


def target_contains(target: SpatialTarget, point: Point) -> bool:
    if isinstance(target, PointTarget):
        return target.point.distance_to(point) <= target.radius
    if isinstance(target, PolygonRegion):
        return target.contains(point)
    return any(point.distance_to(candidate) <= 0.75 for candidate in target.points)


def _area_waypoints(region: PolygonRegion, maximum: int = 24) -> tuple[Point, ...]:
    minimum_x = floor(min(point.x for point in region.points))
    maximum_x = ceil(max(point.x for point in region.points))
    minimum_y = floor(min(point.y for point in region.points))
    maximum_y = ceil(max(point.y for point in region.points))
    rows: list[Point] = []
    for row_index, y in enumerate(range(minimum_y, maximum_y)):
        row = [
            Point(x + 0.5, y + 0.5)
            for x in range(minimum_x, maximum_x)
            if region.contains(Point(x + 0.5, y + 0.5))
        ]
        if row_index % 2:
            row.reverse()
        rows.extend(row)
    if not rows:
        return (region.centroid,)
    if len(rows) <= maximum:
        return tuple(rows)
    step = (len(rows) - 1) / (maximum - 1)
    return tuple(rows[round(index * step)] for index in range(maximum))
