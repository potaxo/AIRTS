"""Inspectable patrol automation state and deterministic waypoint planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil, floor

from airts.geometry import Point, PointTarget, PolygonRegion, PolylineTarget, SpatialTarget
from airts.map_model import GameMap


class AutomationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELED = "canceled"


@dataclass(slots=True)
class PatrolAutomation:
    automation_id: str
    title: str
    target: SpatialTarget
    entity_ids: list[str]
    waypoints: tuple[Point, ...]
    created_tick: int
    status: AutomationStatus = AutomationStatus.ACTIVE
    reason_code: str = "CREATED"
    waypoint_indices: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for entity_id in self.entity_ids:
            self.waypoint_indices.setdefault(entity_id, 0)

    def take_next_waypoint(self, entity_id: str) -> Point:
        index = self.waypoint_indices[entity_id]
        waypoint = self.waypoints[index]
        self.waypoint_indices[entity_id] = (index + 1) % len(self.waypoints)
        return waypoint

    def remove_entity(self, entity_id: str) -> None:
        if entity_id in self.entity_ids:
            self.entity_ids.remove(entity_id)
            self.waypoint_indices.pop(entity_id, None)

    def to_dict(self) -> dict[str, object]:
        from airts.geometry import target_to_dict

        return {
            "id": self.automation_id,
            "title": self.title,
            "template": "patrol",
            "target": target_to_dict(self.target),
            "entity_ids": list(self.entity_ids),
            "status": self.status.value,
            "reason_code": self.reason_code,
            "created_tick": self.created_tick,
            "waypoints": [[point.x, point.y] for point in self.waypoints],
            "waypoint_indices": dict(sorted(self.waypoint_indices.items())),
        }


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
