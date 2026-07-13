"""Deterministic uniform-grid broadphase for local navigation queries."""

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
        candidates: list[str] = []
        for bucket_y in range(minimum_y, maximum_y + 1):
            for bucket_x in range(minimum_x, maximum_x + 1):
                candidates.extend(self._buckets.get((bucket_x, bucket_y), ()))
        return tuple(
            sorted(
                entity_id
                for entity_id in candidates
                if _squared_distance(point, self._positions[entity_id]) <= squared_radius
            )
        )

    def nearest(self, point: Point, radius: float) -> str | None:
        """Return the nearest in-range ID with deterministic ID tie-breaking."""

        if radius < 0:
            raise ValueError("radius cannot be negative")
        minimum_x = floor((point.x - radius) / self.bucket_size)
        maximum_x = floor((point.x + radius) / self.bucket_size)
        minimum_y = floor((point.y - radius) / self.bucket_size)
        maximum_y = floor((point.y + radius) / self.bucket_size)
        squared_radius = radius * radius
        nearest_key: tuple[float, str] | None = None
        for bucket_y in range(minimum_y, maximum_y + 1):
            for bucket_x in range(minimum_x, maximum_x + 1):
                for entity_id in self._buckets.get((bucket_x, bucket_y), ()):
                    distance = _squared_distance(point, self._positions[entity_id])
                    candidate = (distance, entity_id)
                    if distance <= squared_radius and (
                        nearest_key is None or candidate < nearest_key
                    ):
                        nearest_key = candidate
        return nearest_key[1] if nearest_key is not None else None

    def candidate_pairs(self, radius: float) -> tuple[tuple[str, str], ...]:
        return self.candidate_pairs_for(tuple(self._positions), radius)

    def candidate_pairs_for(
        self, active_ids: tuple[str, ...], radius: float
    ) -> tuple[tuple[str, str], ...]:
        """Return nearby pairs where at least one member is active."""

        if radius < 0:
            raise ValueError("radius cannot be negative")
        active_set = frozenset(
            entity_id for entity_id in active_ids if entity_id in self._positions
        )
        pairs: list[tuple[str, str]] = []
        squared_radius = radius * radius
        for first_id in sorted(active_set):
            position = self._positions[first_id]
            minimum_x = floor((position.x - radius) / self.bucket_size)
            maximum_x = floor((position.x + radius) / self.bucket_size)
            minimum_y = floor((position.y - radius) / self.bucket_size)
            maximum_y = floor((position.y + radius) / self.bucket_size)
            for bucket_y in range(minimum_y, maximum_y + 1):
                for bucket_x in range(minimum_x, maximum_x + 1):
                    for second_id in self._buckets.get((bucket_x, bucket_y), ()):
                        if (
                            first_id == second_id
                            or (second_id in active_set and first_id > second_id)
                            or _squared_distance(position, self._positions[second_id])
                            > squared_radius
                        ):
                            continue
                        pairs.append(
                            (first_id, second_id) if first_id < second_id else (second_id, first_id)
                        )
        return tuple(sorted(pairs))

    def _bucket(self, point: Point) -> tuple[int, int]:
        return floor(point.x / self.bucket_size), floor(point.y / self.bucket_size)


def _squared_distance(first: Point, second: Point) -> float:
    offset_x = first.x - second.x
    offset_y = first.y - second.y
    return offset_x * offset_x + offset_y * offset_y
