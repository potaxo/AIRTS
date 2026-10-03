"""Deterministic uniform-grid broadphase for local navigation queries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from math import floor

import numpy as np

from airts.contracts.spatial import Point
from airts.world._numeric import FloatArray, IntArray, compile_numeric


class SpatialIndex:
    """Index points into fixed-size buckets while preserving stable query order."""

    def __init__(self, positions: dict[str, Point], bucket_size: float = 2.5) -> None:
        if bucket_size <= 0:
            raise ValueError("bucket_size must be positive")
        self.bucket_size = float(bucket_size)
        self._positions = dict(positions)
        self._nearest_data: (
            tuple[tuple[str, ...], FloatArray, Mapping[tuple[int, int], int], IntArray] | None
        ) = None
        self._bounds = (
            float(min((p.x for p in positions.values()), default=0.0)),
            float(min((p.y for p in positions.values()), default=0.0)),
            float(max((p.x for p in positions.values()), default=0.0)),
            float(max((p.y for p in positions.values()), default=0.0)),
        )
        self._buckets: dict[tuple[int, int], set[str]] | None = None

    def move(self, entity_id: str, position: Point) -> None:
        self._nearest_data = None
        previous = self._positions[entity_id]
        previous_bucket = self._bucket(previous)
        next_bucket = self._bucket(position)
        self._positions[entity_id] = position
        left, top, right, bottom = self._bounds
        self._bounds = (
            float(min(left, position.x)),
            float(min(top, position.y)),
            float(max(right, position.x)),
            float(max(bottom, position.y)),
        )
        if self._buckets is None:
            return
        if previous_bucket == next_bucket:
            return
        self._buckets[previous_bucket].remove(entity_id)
        if not self._buckets[previous_bucket]:
            del self._buckets[previous_bucket]
        self._buckets[next_bucket].add(entity_id)

    def nearby(self, point: Point, radius: float) -> tuple[str, ...]:
        if radius < 0:
            raise ValueError("radius cannot be negative")
        left, top, right, bottom = self._bounds
        if not self._positions or (
            point.x + radius < left
            or point.x - radius > right
            or point.y + radius < top
            or point.y - radius > bottom
        ):
            return ()
        minimum_x = floor((point.x - radius) / self.bucket_size)
        maximum_x = floor((point.x + radius) / self.bucket_size)
        minimum_y = floor((point.y - radius) / self.bucket_size)
        maximum_y = floor((point.y + radius) / self.bucket_size)
        squared_radius = radius * radius
        candidates: list[str] = []
        positions = self._positions
        buckets = self._point_buckets()
        for bucket_y in range(minimum_y, maximum_y + 1):
            for bucket_x in range(minimum_x, maximum_x + 1):
                for entity_id in buckets.get((bucket_x, bucket_y), ()):
                    position = positions[entity_id]
                    dx = point.x - position.x
                    dy = point.y - position.y
                    if dx * dx + dy * dy <= squared_radius:
                        candidates.append(entity_id)
        candidates.sort()
        return tuple(candidates)

    def nearest(self, point: Point, radius: float) -> str | None:
        """Return the nearest in-range ID with deterministic ID tie-breaking."""

        if radius < 0:
            raise ValueError("radius cannot be negative")
        left, top, right, bottom = self._bounds
        if (
            not self._positions
            or point.x + radius < left
            or point.x - radius > right
            or point.y + radius < top
            or point.y - radius > bottom
        ):
            return None
        keys, positions, heads, links = self._nearest_index_data()
        best = _nearest_index(
            float(point.x), float(point.y), float(radius), positions, heads, links, self.bucket_size
        )
        return keys[best] if best >= 0 else None

    def nearest_many(self, queries: tuple[tuple[Point, float], ...]) -> tuple[str | None, ...]:
        """Query a fixed snapshot in one numeric call, preserving query and ID order."""
        if any(radius < 0 for _, radius in queries):
            raise ValueError("radius cannot be negative")
        if not self._positions or not queries:
            return (None,) * len(queries)
        keys, positions, heads, links = self._nearest_index_data()
        query_array = np.asarray([(p.x, p.y, r) for p, r in queries], dtype=np.float64)
        results = _nearest_many(
            query_array, positions, heads, links, self.bucket_size, self._bounds
        )
        return tuple(keys[index] if index >= 0 else None for index in results.tolist())

    def _nearest_index_data(
        self,
    ) -> tuple[tuple[str, ...], FloatArray, Mapping[tuple[int, int], int], IntArray]:
        if self._nearest_data is None:
            keys = tuple(sorted(self._positions))
            positions = np.asarray(
                [(self._positions[key].x, self._positions[key].y) for key in keys], dtype=np.float64
            ).reshape((-1, 2))
            heads, links = _build_nearest_index(positions, self.bucket_size)
            self._nearest_data = keys, positions, heads, links
        return self._nearest_data

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
        buckets = self._point_buckets()
        for first_id in sorted(active_set):
            position = self._positions[first_id]
            minimum_x = floor((position.x - radius) / self.bucket_size)
            maximum_x = floor((position.x + radius) / self.bucket_size)
            minimum_y = floor((position.y - radius) / self.bucket_size)
            maximum_y = floor((position.y + radius) / self.bucket_size)
            for bucket_y in range(minimum_y, maximum_y + 1):
                for bucket_x in range(minimum_x, maximum_x + 1):
                    for second_id in buckets.get((bucket_x, bucket_y), ()):
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

    def _point_buckets(self) -> dict[tuple[int, int], set[str]]:
        if self._buckets is None:
            self._buckets = defaultdict(set)
            for key, point in self._positions.items():
                self._buckets[self._bucket(point)].add(key)
        return self._buckets

    def _bucket(self, point: Point) -> tuple[int, int]:
        return floor(point.x / self.bucket_size), floor(point.y / self.bucket_size)


def _squared_distance(first: Point, second: Point) -> float:
    offset_x = first.x - second.x
    offset_y = first.y - second.y
    return offset_x * offset_x + offset_y * offset_y


@compile_numeric
def _build_nearest_index(
    positions: FloatArray, bucket_size: float
) -> tuple[Mapping[tuple[int, int], int], IntArray]:
    heads: dict[tuple[int, int], int] = {(0, 0): -1}
    heads.clear()
    links = np.full(len(positions), -1, dtype=np.int64)
    for index in range(len(positions)):
        bucket = floor(positions[index, 0] / bucket_size), floor(positions[index, 1] / bucket_size)
        links[index] = heads[bucket] if bucket in heads else -1
        heads[bucket] = index
    return heads, links


@compile_numeric
def _nearest_index(
    x: float,
    y: float,
    radius: float,
    positions: FloatArray,
    heads: Mapping[tuple[int, int], int],
    links: IntArray,
    bucket_size: float,
) -> int:
    best = -1
    best_distance = radius * radius
    for by in range(floor((y - radius) / bucket_size), floor((y + radius) / bucket_size) + 1):
        for bx in range(floor((x - radius) / bucket_size), floor((x + radius) / bucket_size) + 1):
            bucket = bx, by
            index = heads[bucket] if bucket in heads else -1
            while index != -1:
                dx, dy = x - positions[index, 0], y - positions[index, 1]
                distance = dx * dx + dy * dy
                if distance <= best_distance and (
                    distance < best_distance or best < 0 or index < best
                ):
                    best, best_distance = index, distance
                index = links[index]
    return best


@compile_numeric
def _nearest_many(
    queries: FloatArray,
    positions: FloatArray,
    heads: Mapping[tuple[int, int], int],
    links: IntArray,
    bucket_size: float,
    bounds: tuple[float, float, float, float],
) -> IntArray:
    results = np.full(len(queries), -1, dtype=np.int64)
    left, top, right, bottom = bounds
    for index in range(len(queries)):
        x, y, radius = queries[index]
        if x + radius < left or x - radius > right or y + radius < top or y - radius > bottom:
            continue
        results[index] = _nearest_index(x, y, radius, positions, heads, links, bucket_size)
    return results


def warm_spatial_index() -> None:
    positions = np.empty((0, 2), dtype=np.float64)
    heads, links = _build_nearest_index(positions, 2.5)
    _nearest_index(0.0, 0.0, 0.0, positions, heads, links, 2.5)
    _nearest_many(np.empty((0, 3), dtype=np.float64), positions, heads, links, 2.5, (0.0,) * 4)
