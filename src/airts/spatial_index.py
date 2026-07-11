"""Deterministic uniform-grid broadphase for local simulation queries."""

from __future__ import annotations

from collections import defaultdict
from math import floor

from airts.geometry import Point


class SpatialIndex:
    """Index points into fixed-size buckets while preserving stable query order."""

    def __init__(self, positions: dict[str, Point], bucket_size: float = 2.5) -> None:
        if bucket_size <= 0:
            raise ValueError("bucket_size must be positive")
        self.bucket_size = bucket_size
        self._positions = dict(positions)
        self._buckets: dict[tuple[int, int], set[str]] = defaultdict(set)
        for entity_id, position in positions.items():
            self._buckets[self._bucket(position)].add(entity_id)

    def move(self, entity_id: str, position: Point) -> None:
        previous = self._positions[entity_id]
        previous_bucket = self._bucket(previous)
        next_bucket = self._bucket(position)
        self._positions[entity_id] = position
        if previous_bucket == next_bucket:
            return
        self._buckets[previous_bucket].remove(entity_id)
        if not self._buckets[previous_bucket]:
            del self._buckets[previous_bucket]
        self._buckets[next_bucket].add(entity_id)

    def nearby(self, point: Point, radius: float) -> tuple[str, ...]:
        if radius < 0:
            raise ValueError("radius cannot be negative")
        minimum_x = floor((point.x - radius) / self.bucket_size)
        maximum_x = floor((point.x + radius) / self.bucket_size)
        minimum_y = floor((point.y - radius) / self.bucket_size)
        maximum_y = floor((point.y + radius) / self.bucket_size)
        squared_radius = radius * radius
        candidates: set[str] = set()
        for bucket_y in range(minimum_y, maximum_y + 1):
            for bucket_x in range(minimum_x, maximum_x + 1):
                candidates.update(self._buckets.get((bucket_x, bucket_y), ()))
        return tuple(
            entity_id
            for entity_id in sorted(candidates)
            if _squared_distance(point, self._positions[entity_id]) <= squared_radius
        )

    def candidate_pairs(self, radius: float) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for first_id in sorted(self._positions):
            for second_id in self.nearby(self._positions[first_id], radius):
                if first_id < second_id:
                    pairs.append((first_id, second_id))
        return tuple(pairs)

    def _bucket(self, point: Point) -> tuple[int, int]:
        return floor(point.x / self.bucket_size), floor(point.y / self.bucket_size)


def _squared_distance(first: Point, second: Point) -> float:
    offset_x = first.x - second.x
    offset_y = first.y - second.y
    return offset_x * offset_x + offset_y * offset_y
