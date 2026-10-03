"""Geometry targets, spatial references, and selection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import hypot


@dataclass(frozen=True, slots=True)
class Point:
    """A position in continuous map coordinates."""

    x: float
    y: float

    def distance_to(self, other: Point) -> float:
        return hypot(other.x - self.x, other.y - self.y)


@dataclass(frozen=True, slots=True)
class PointTarget:
    point: Point
    radius: float = 2.0

    def __post_init__(self) -> None:
        if self.radius <= 0:
            raise ValueError("point patrol radius must be positive")


@dataclass(frozen=True, slots=True)
class PolylineTarget:
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("a polyline requires at least two points")
        if all(point == self.points[0] for point in self.points[1:]):
            raise ValueError("a polyline must contain distinct points")


@dataclass(frozen=True, slots=True)
class PolygonRegion:
    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("a polygon requires at least three points")
        if abs(self.signed_area) < 1e-9:
            raise ValueError("a polygon must have non-zero area")
        if _polygon_self_intersects(self.points):
            raise ValueError("polygon edges must not intersect")

    @property
    def signed_area(self) -> float:
        total = 0.0
        for current, following in zip(self.points, self.points[1:] + self.points[:1], strict=True):
            total += current.x * following.y - following.x * current.y
        return total / 2.0

    @property
    def centroid(self) -> Point:
        area_factor = self.signed_area * 6.0
        x_total = 0.0
        y_total = 0.0
        for current, following in zip(self.points, self.points[1:] + self.points[:1], strict=True):
            cross = current.x * following.y - following.x * current.y
            x_total += (current.x + following.x) * cross
            y_total += (current.y + following.y) * cross
        return Point(x_total / area_factor, y_total / area_factor)

    def contains(self, point: Point) -> bool:
        """Return whether a point lies inside the polygon, including its boundary."""

        inside = False
        previous = self.points[-1]
        for current in self.points:
            if _point_on_segment(point, previous, current):
                return True
            crosses = (current.y > point.y) != (previous.y > point.y)
            if crosses:
                intersection_x = (previous.x - current.x) * (point.y - current.y) / (
                    previous.y - current.y
                ) + current.x
                if point.x < intersection_x:
                    inside = not inside
            previous = current
        return inside


SpatialTarget = PointTarget | PolylineTarget | PolygonRegion


def rectangle_region(start: Point, end: Point) -> PolygonRegion:
    if start.x == end.x or start.y == end.y:
        raise ValueError("a rectangle must have non-zero width and height")
    left, right = sorted((start.x, end.x))
    top, bottom = sorted((start.y, end.y))
    return PolygonRegion(
        (
            Point(left, top),
            Point(right, top),
            Point(right, bottom),
            Point(left, bottom),
        )
    )


def simplify_freehand(points: tuple[Point, ...], tolerance: float = 0.25) -> PolygonRegion:
    """Simplify a freehand stroke and normalize it into a polygon."""

    if tolerance < 0:
        raise ValueError("simplification tolerance cannot be negative")
    deduplicated: list[Point] = []
    for point in points:
        if not deduplicated or point.distance_to(deduplicated[-1]) > 1e-9:
            deduplicated.append(point)
    if len(deduplicated) > 1 and deduplicated[0] == deduplicated[-1]:
        deduplicated.pop()
    if len(deduplicated) < 3:
        raise ValueError("a freehand area requires at least three distinct points")
    simplified = _ramer_douglas_peucker(tuple(deduplicated), tolerance)
    if len(simplified) < 3:
        simplified = tuple(deduplicated)
    return PolygonRegion(simplified)


def target_to_dict(target: SpatialTarget) -> dict[str, object]:
    if isinstance(target, PointTarget):
        return {
            "type": "point",
            "point": [target.point.x, target.point.y],
            "radius": target.radius,
        }
    if isinstance(target, PolylineTarget):
        return {"type": "polyline", "points": [[point.x, point.y] for point in target.points]}
    return {"type": "polygon", "points": [[point.x, point.y] for point in target.points]}


def target_from_dict(raw_data: object) -> SpatialTarget:
    if not isinstance(raw_data, dict) or not all(isinstance(key, str) for key in raw_data):
        raise ValueError("spatial target must be an object")
    target_type = raw_data.get("type")
    if target_type == "point":
        point = _point_from_data(raw_data.get("point"), "point")
        radius = _number_from_data(raw_data.get("radius", 2.0), "radius")
        return PointTarget(point, radius)
    if target_type not in {"polyline", "polygon"}:
        raise ValueError(f"unsupported spatial target type: {target_type}")
    raw_points = raw_data.get("points")
    if not isinstance(raw_points, list):
        raise ValueError("spatial target points must be a list")
    points = tuple(
        _point_from_data(raw_point, f"points[{index}]")
        for index, raw_point in enumerate(raw_points)
    )
    if target_type == "polyline":
        return PolylineTarget(points)
    return PolygonRegion(points)


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    cross = (point.y - start.y) * (end.x - start.x) - (point.x - start.x) * (end.y - start.y)
    if abs(cross) > 1e-9:
        return False
    return (
        min(start.x, end.x) - 1e-9 <= point.x <= max(start.x, end.x) + 1e-9
        and min(start.y, end.y) - 1e-9 <= point.y <= max(start.y, end.y) + 1e-9
    )


def _polygon_self_intersects(points: tuple[Point, ...]) -> bool:
    edge_count = len(points)
    for first_index in range(edge_count):
        first_start = points[first_index]
        first_end = points[(first_index + 1) % edge_count]
        for second_index in range(first_index + 1, edge_count):
            if second_index in {
                first_index,
                (first_index + 1) % edge_count,
                (first_index - 1) % edge_count,
            }:
                continue
            second_start = points[second_index]
            second_end = points[(second_index + 1) % edge_count]
            if _segments_intersect(first_start, first_end, second_start, second_end):
                return True
    return False


def _segments_intersect(first: Point, second: Point, third: Point, fourth: Point) -> bool:
    orientations = (
        _orientation(first, second, third),
        _orientation(first, second, fourth),
        _orientation(third, fourth, first),
        _orientation(third, fourth, second),
    )
    if orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0:
        return True
    return (
        (orientations[0] == 0 and _point_on_segment(third, first, second))
        or (orientations[1] == 0 and _point_on_segment(fourth, first, second))
        or (orientations[2] == 0 and _point_on_segment(first, third, fourth))
        or (orientations[3] == 0 and _point_on_segment(second, third, fourth))
    )


def _orientation(first: Point, second: Point, third: Point) -> int:
    cross = (second.x - first.x) * (third.y - first.y) - (second.y - first.y) * (third.x - first.x)
    if abs(cross) < 1e-9:
        return 0
    return 1 if cross > 0 else -1


def _ramer_douglas_peucker(points: tuple[Point, ...], tolerance: float) -> tuple[Point, ...]:
    if len(points) <= 2:
        return points
    start, end = points[0], points[-1]
    max_distance = -1.0
    split_index = 0
    for index, point in enumerate(points[1:-1], start=1):
        distance = distance_to_segment(point, start, end)
        if distance > max_distance:
            max_distance = distance
            split_index = index
    if max_distance <= tolerance:
        return (start, end)
    left = _ramer_douglas_peucker(points[: split_index + 1], tolerance)
    right = _ramer_douglas_peucker(points[split_index:], tolerance)
    return left[:-1] + right


def distance_to_segment(point: Point, start: Point, end: Point) -> float:
    """Distance to a closed segment, including a segment with coincident endpoints."""

    dx = end.x - start.x
    dy = end.y - start.y
    if dx == 0 and dy == 0:
        return point.distance_to(start)
    fraction = ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)
    fraction = max(0.0, min(1.0, fraction))
    projection = Point(start.x + fraction * dx, start.y + fraction * dy)
    return point.distance_to(projection)


def _point_from_data(raw_data: object, field: str) -> Point:
    if not isinstance(raw_data, list) or len(raw_data) != 2:
        raise ValueError(f"{field} must contain two numbers")
    return Point(
        _number_from_data(raw_data[0], f"{field}.x"),
        _number_from_data(raw_data[1], f"{field}.y"),
    )


def _number_from_data(raw_data: object, field: str) -> float:
    if isinstance(raw_data, bool) or not isinstance(raw_data, int | float):
        raise ValueError(f"{field} must be a number")
    return float(raw_data)


class SpatialKind(StrEnum):
    POINT = "point"
    ROUTE = "route"
    REGION = "region"


def spatial_kind(target: SpatialTarget) -> SpatialKind:
    if isinstance(target, PointTarget):
        return SpatialKind.POINT
    if isinstance(target, PolylineTarget):
        return SpatialKind.ROUTE
    if isinstance(target, PolygonRegion):
        return SpatialKind.REGION
    raise TypeError(f"unsupported spatial target: {type(target).__name__}")


@dataclass(slots=True)
class SpatialReference:
    reference_id: str
    kind: SpatialKind
    geometry: SpatialTarget
    created_tick: int
    modified_tick: int
    name: str | None = None

    @property
    def persistent(self) -> bool:
        return self.kind is SpatialKind.REGION and self.name is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.reference_id,
            "kind": self.kind.value,
            "geometry": target_to_dict(self.geometry),
            "name": self.name,
            "persistent": self.persistent,
            "created_tick": self.created_tick,
            "modified_tick": self.modified_tick,
        }


@dataclass(slots=True)
class SpatialStore:
    references: dict[str, SpatialReference] = field(default_factory=dict)
    next_numbers: dict[SpatialKind, int] = field(
        default_factory=lambda: {kind: 1 for kind in SpatialKind}
    )

    def create(
        self, geometry: SpatialTarget, tick: int, name: str | None = None
    ) -> SpatialReference:
        kind = spatial_kind(geometry)
        normalized_name = self._validated_name(kind, name, None)
        number = self.next_numbers[kind]
        self.next_numbers[kind] += 1
        reference_id = f"{kind.value}_{number:03d}"
        reference = SpatialReference(reference_id, kind, geometry, tick, tick, normalized_name)
        self.references[reference_id] = reference
        return reference

    def edit(self, reference_id: str, geometry: SpatialTarget, tick: int) -> SpatialReference:
        reference = self.get(reference_id)
        if spatial_kind(geometry) is not reference.kind:
            raise ValueError("SPATIAL_KIND_CANNOT_CHANGE")
        reference.geometry = geometry
        reference.modified_tick = tick
        return reference

    def rename_region(self, reference_id: str, name: str, tick: int) -> SpatialReference:
        reference = self.get(reference_id)
        reference.name = self._validated_name(reference.kind, name, reference_id)
        reference.modified_tick = tick
        return reference

    def delete_region(self, reference_id: str) -> SpatialReference:
        reference = self.get(reference_id)
        if reference.kind is not SpatialKind.REGION:
            raise ValueError("ONLY_REGIONS_CAN_BE_DELETED")
        return self.delete(reference_id)

    def delete(self, reference_id: str) -> SpatialReference:
        reference = self.get(reference_id)
        del self.references[reference_id]
        return reference

    def get(self, reference_id: str) -> SpatialReference:
        try:
            return self.references[reference_id]
        except KeyError as error:
            raise ValueError("UNKNOWN_SPATIAL_REFERENCE") from error

    def _validated_name(
        self, kind: SpatialKind, name: str | None, current_id: str | None
    ) -> str | None:
        if name is None:
            return None
        if kind is not SpatialKind.REGION:
            raise ValueError("ONLY_REGIONS_CAN_BE_NAMED")
        normalized = name.strip()
        if not normalized:
            raise ValueError("REGION_NAME_EMPTY")
        if any(
            reference.reference_id != current_id
            and reference.name is not None
            and reference.name.casefold() == normalized.casefold()
            for reference in self.references.values()
        ):
            raise ValueError("REGION_NAME_NOT_UNIQUE")
        return normalized

    def to_dict(self) -> dict[str, object]:
        return {
            "references": {
                reference_id: reference.to_dict()
                for reference_id, reference in sorted(self.references.items())
            },
            "next_numbers": {kind.value: self.next_numbers[kind] for kind in SpatialKind},
        }


@dataclass(frozen=True, slots=True)
class GroundingSelection:
    entity_ids: tuple[str, ...] = ()
    point_ids: tuple[str, ...] = ()
    route_ids: tuple[str, ...] = ()
    region_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_ids": list(self.entity_ids),
            "point_ids": list(self.point_ids),
            "route_ids": list(self.route_ids),
            "region_ids": list(self.region_ids),
        }
