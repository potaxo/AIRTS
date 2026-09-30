"""Serializable automation schemas, lifecycle, and geometry planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil, floor

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


def target_center(target: SpatialTarget) -> Point:
    if isinstance(target, PointTarget):
        return target.point
    if isinstance(target, PolygonRegion):
        return target.centroid
    return Point(
        sum(point.x for point in target.points) / len(target.points),
        sum(point.y for point in target.points) / len(target.points),
    )


def build_defend_stations(
    target: SpatialTarget, entity_ids: tuple[str, ...], game_map: GameMap
) -> dict[str, Point]:
    """Evenly distribute stations along a line or the target's patrol perimeter."""
    points = (
        target.points
        if isinstance(target, PolylineTarget)
        else build_patrol_waypoints(target, game_map)
    )
    if not isinstance(target, PolylineTarget):
        return {
            key: points[round(i * (len(points) - 1) / max(1, len(entity_ids) - 1))]
            for i, key in enumerate(entity_ids)
        }
    segments = tuple(zip(points, points[1:], strict=False))
    lengths = tuple(a.distance_to(b) for a, b in segments)
    stations: dict[str, Point] = {}
    for i, entity_id in enumerate(entity_ids):
        distance = sum(lengths) * (i / (len(entity_ids) - 1) if len(entity_ids) > 1 else 0.5)
        for (a, b), length in zip(segments, lengths, strict=True):
            if distance <= length and length > 0:
                point = Point(
                    a.x + (b.x - a.x) * distance / length, a.y + (b.y - a.y) * distance / length
                )
                break
            distance -= length
        else:
            point = points[-1]
        if not game_map.is_passable(point):
            raise ValueError("defend station is not passable")
        stations[entity_id] = point
    return stations


def assign_formation_slots(
    positions: Mapping[str, Point],
    slots: tuple[Point, ...],
    center: Point,
    previous: Mapping[str, Point] | None = None,
) -> dict[str, Point]:
    """Retain stations, then recursively match nearby spatial halves."""
    if len(positions) != len(slots) or len(set(slots)) != len(slots):
        raise ValueError("formation requires one unique slot per unit")
    free = set(slots)
    assigned: dict[str, Point] = {}
    for entity_id in sorted(positions):
        point = (previous or {}).get(entity_id)
        if point in free:
            assert point is not None
            assigned[entity_id] = point
            free.remove(point)
    if center in free:
        nearest = min(
            (key for key in positions if key not in assigned),
            key=lambda key: (positions[key].distance_to(center), key),
        )
        assigned[nearest] = center
        free.remove(center)

    def match(ids: list[str], points: list[Point]) -> None:
        if not ids:
            return
        if len(ids) == 1:
            assigned[ids[0]] = points[0]
            return
        span_x = max(p.x for p in points) - min(p.x for p in points)
        span_y = max(p.y for p in points) - min(p.y for p in points)

        def rank(point: Point) -> tuple[float, float]:
            return (point.x, point.y) if span_x >= span_y else (point.y, point.x)

        ids.sort(key=lambda key: (rank(positions[key]), key))
        points.sort(key=rank)
        middle = len(ids) // 2
        match(ids[:middle], points[:middle])
        match(ids[middle:], points[middle:])

    match([key for key in positions if key not in assigned], list(free))
    return assigned


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
