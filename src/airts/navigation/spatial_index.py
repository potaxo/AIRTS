"""Deterministic uniform-grid broadphase for local navigation queries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import chain
from math import floor

import numpy as np

from airts.contracts.spatial import Point
from airts.world._numeric import (
    BoolArray,
    FloatArray,
    IntArray,
    compile_numeric,
    compile_parallel_numeric,
    point_coordinates,
)


@dataclass(slots=True)
class _QueryIndex:
    keys: tuple[str, ...]
    positions: FloatArray
    links: IntArray
    neighbors: IntArray
    sparse: Mapping[tuple[int, int], int] | None = None
    dense: IntArray | None = None
    origin: tuple[int, int] = (0, 0)


class SpatialIndex:
    """Index points into fixed-size buckets while preserving stable query order."""

    def __init__(self, positions: dict[str, Point], bucket_size: float = 2.5) -> None:
        if bucket_size <= 0:
            raise ValueError("bucket_size must be positive")
        self.bucket_size = float(bucket_size)
        self._positions = dict(positions)
        self._keys = tuple(sorted(positions))
        self._coordinates: FloatArray | None = point_coordinates(
            list(map(positions.__getitem__, self._keys))
        )
        self._nearest_data: _QueryIndex | None = None
        if positions:
            x, y = self._coordinates.T
            self._bounds = (
                float(x.min()),
                float(y.min()),
                float(x.max()),
                float(y.max()),
            )
        else:
            self._bounds = (0.0, 0.0, 0.0, 0.0)
        self._buckets: dict[tuple[int, int], set[str]] | None = None

    def move(self, entity_id: str, position: Point) -> None:
        self._nearest_data = None
        self._coordinates = None
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
        data = self._nearest_index_data()
        if data.dense is not None:
            count = _nearby_dense(
                float(point.x),
                float(point.y),
                float(radius),
                data.positions,
                data.dense,
                data.links,
                data.origin[0],
                data.origin[1],
                self.bucket_size,
                data.neighbors,
            )
        else:
            assert data.sparse is not None
            count = _nearby_sparse(
                float(point.x),
                float(point.y),
                float(radius),
                data.positions,
                data.sparse,
                data.links,
                self.bucket_size,
                data.neighbors,
            )
        return tuple(data.keys[index] for index in data.neighbors[:count].tolist())

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
        data = self._nearest_index_data()
        if data.dense is not None:
            best = _nearest_dense_index(
                float(point.x),
                float(point.y),
                float(radius),
                data.positions,
                data.dense,
                data.links,
                data.origin[0],
                data.origin[1],
                self.bucket_size,
            )
        else:
            assert data.sparse is not None
            best = _nearest_index(
                float(point.x),
                float(point.y),
                float(radius),
                data.positions,
                data.sparse,
                data.links,
                self.bucket_size,
            )
        return data.keys[best] if best >= 0 else None

    def nearest_many(self, queries: tuple[tuple[Point, float], ...]) -> tuple[str | None, ...]:
        """Query a fixed snapshot in one numeric call, preserving query and ID order."""
        if any(radius < 0 for _, radius in queries):
            raise ValueError("radius cannot be negative")
        if not self._positions or not queries:
            return (None,) * len(queries)
        query_array = np.asarray([(p.x, p.y, r) for p, r in queries], dtype=np.float64)
        return self.nearest_many_array(query_array)

    def nearest_many_array(self, query_array: FloatArray) -> tuple[str | None, ...]:
        """Reuse one packed query batch across several candidate snapshots."""
        if query_array.ndim != 2 or query_array.shape[1] != 3:
            raise ValueError("nearest queries must have x, y and radius columns")
        if np.any(query_array[:, 2] < 0):
            raise ValueError("radius cannot be negative")
        if not self._positions or not len(query_array):
            return (None,) * len(query_array)
        data = self._nearest_index_data()
        if data.dense is not None:
            kernel = (
                _nearest_dense_many_parallel if len(query_array) >= 1_024 else _nearest_dense_many
            )
            results = kernel(
                query_array,
                data.positions,
                data.dense,
                data.links,
                data.origin[0],
                data.origin[1],
                self.bucket_size,
            )
        else:
            assert data.sparse is not None
            results = _nearest_many(
                query_array, data.positions, data.sparse, data.links, self.bucket_size, self._bounds
            )
        return tuple(data.keys[index] if index >= 0 else None for index in results.tolist())

    def any_many(self, queries: tuple[tuple[Point, float], ...]) -> BoolArray:
        """Return exact radius membership without finding or materializing a nearest ID."""
        if any(radius < 0 for _, radius in queries):
            raise ValueError("radius cannot be negative")
        if not self._positions or not queries:
            return np.zeros(len(queries), dtype=np.bool_)
        batch = np.asarray([(p.x, p.y, r) for p, r in queries], dtype=np.float64)
        data = self._nearest_index_data()
        if data.dense is None:
            # Sparse snapshots retain the bounded nearest scan for large radii.
            return np.asarray([key is not None for key in self.nearest_many_array(batch)])
        kernel = _any_dense_many_parallel if len(batch) >= 1_024 else _any_dense_many
        return kernel(
            batch,
            data.positions,
            data.dense,
            data.links,
            data.origin[0],
            data.origin[1],
            self.bucket_size,
        )

    def intersects(self, queries: tuple[tuple[Point, float], ...]) -> bool:
        """Whether any query disk contains a point; stop at the first exact match."""
        if any(radius < 0 for _, radius in queries):
            raise ValueError("radius cannot be negative")
        if not self._positions or not queries:
            return False
        data = self._nearest_index_data()
        if data.dense is None:
            return any(self.nearest(point, radius) is not None for point, radius in queries)
        batch = np.asarray([(p.x, p.y, r) for p, r in queries], dtype=np.float64)
        return _intersects_dense(
            batch, data.positions, data.dense, data.links, *data.origin, self.bucket_size
        )

    def _nearest_index_data(self) -> _QueryIndex:
        if self._nearest_data is None:
            keys = self._keys
            positions = self._coordinates
            if positions is None:
                positions = point_coordinates(list(map(self._positions.__getitem__, keys)))
                self._coordinates = positions
            left, top, right, bottom = self._bounds
            ox, oy = floor(left / self.bucket_size), floor(top / self.bucket_size)
            width = floor(right / self.bucket_size) - ox + 1
            height = floor(bottom / self.bucket_size) - oy + 1
            if width * height <= min(262_144, max(4_096, len(keys) * 16)):
                dense, links = _build_dense_index(
                    positions, self.bucket_size, ox, oy, width, height
                )
                data = _QueryIndex(
                    keys,
                    positions,
                    links,
                    np.empty(len(keys), dtype=np.int64),
                    dense=dense,
                    origin=(ox, oy),
                )
            else:
                heads, links = _build_nearest_index(positions, self.bucket_size)
                data = _QueryIndex(
                    keys, positions, links, np.empty(len(keys), dtype=np.int64), sparse=heads
                )
            self._nearest_data = data
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


class GroupedSpatialIndex:
    """Pack a position-stable population once; filter whole groups inside a batch."""

    def __init__(self, groups: dict[str, dict[str, Point]], bucket_size: float = 2.5) -> None:
        if bucket_size <= 0:
            raise ValueError("bucket_size must be positive")
        self.bucket_size = bucket_size
        self.groups: tuple[str, ...] = ()
        self.keys: tuple[str, ...] = ()
        self._membership: tuple[tuple[str, tuple[str, ...]], ...] | None = None
        self._order: list[int] = []
        self._labels = np.empty(0, dtype=np.int64)
        self._capacity = 0
        self._position_storage = np.empty((0, 2), dtype=np.float64)
        self._link_storage = np.empty(0, dtype=np.int64)
        self.positions = self._position_storage
        self.sparse: dict[str, SpatialIndex] = {}
        self.heads: IntArray | None = None
        self.links = self._link_storage
        self.origin = (0, 0)
        self.refresh(groups)

    def refresh(self, groups: dict[str, dict[str, Point]]) -> None:
        """Refresh between query batches; membership and bounded buffers persist."""
        membership = tuple((group, tuple(points)) for group, points in sorted(groups.items()))
        if membership != self._membership:
            self.groups = tuple(group for group, _ in membership)
            keys = list(chain.from_iterable(keys for _, keys in membership))
            self._order = sorted(range(len(keys)), key=keys.__getitem__)
            self.keys = tuple(map(keys.__getitem__, self._order))
            labels = np.repeat(
                np.arange(len(membership), dtype=np.int64),
                [len(members) for _, members in membership],
            )
            self._labels = labels[self._order]
            self._membership = membership
        count = len(self.keys)
        if count > self._capacity:
            self._capacity = max(128, count, self._capacity * 2)
            self._position_storage = np.empty((self._capacity, 2), dtype=np.float64)
            self._link_storage = np.empty(self._capacity, dtype=np.int64)
        self.positions, self.links = self._position_storage[:count], self._link_storage[:count]
        if not count:
            self.heads, self.sparse = None, {}
            return
        points = list(chain.from_iterable(groups[group].values() for group in self.groups))
        point_coordinates(list(map(points.__getitem__, self._order)), self.positions)
        x, y = self.positions.T
        ox, oy = floor(x.min() / self.bucket_size), floor(y.min() / self.bucket_size)
        right, bottom = floor(x.max() / self.bucket_size), floor(y.max() / self.bucket_size)
        width, height = right - ox + 1, bottom - oy + 1
        limit = min(262_144, max(4_096, count * 16))
        if len(groups) * width * height > limit:
            self.heads = None
            self.sparse = {
                group: SpatialIndex(points, self.bucket_size) for group, points in groups.items()
            }
            return
        # Bucket-aligned margins keep small movements from reallocating the grid.
        aligned_x, aligned_y = ox // 8 * 8, oy // 8 * 8
        aligned_width = (right - aligned_x + 8) // 8 * 8
        aligned_height = (bottom - aligned_y + 8) // 8 * 8
        if len(groups) * aligned_width * aligned_height <= limit:
            ox, oy, width, height = aligned_x, aligned_y, aligned_width, aligned_height
        shape = len(groups), height, width
        if self.heads is None or self.origin != (ox, oy) or self.heads.shape != shape:
            self.heads = np.empty(shape, dtype=np.int64)
        self.sparse = {}
        self.origin = ox, oy
        _fill_grouped_index(
            self.positions,
            self._labels,
            self.bucket_size,
            ox,
            oy,
            self.heads,
            self.links,
        )

    def nearest_many_array(
        self, queries: FloatArray, groups: tuple[str, ...]
    ) -> tuple[str | None, ...]:
        if queries.ndim != 2 or queries.shape[1] != 3 or np.any(queries[:, 2] < 0):
            raise ValueError("nearest queries require x, y and nonnegative radius")
        if not len(queries) or not self.keys or not groups:
            return (None,) * len(queries)
        if self.heads is None:
            candidates = [self.sparse[group].nearest_many_array(queries) for group in groups]
            coordinates = {key: self.positions[index] for index, key in enumerate(self.keys)}
            return tuple(
                min(
                    (key for batch in candidates if (key := batch[index]) is not None),
                    key=lambda key: (
                        float(np.sum((coordinates[key] - queries[index, :2]) ** 2)),
                        key,
                    ),
                    default=None,
                )
                for index in range(len(queries))
            )
        allowed = np.asarray([self.groups.index(group) for group in groups], dtype=np.int64)
        kernel = _nearest_grouped_parallel if len(queries) >= 1_024 else _nearest_grouped_serial
        results = kernel(
            queries, self.positions, self.heads, self.links, allowed, *self.origin, self.bucket_size
        )
        return tuple(self.keys[index] if index >= 0 else None for index in results.tolist())


@compile_numeric
def _fill_grouped_index(
    positions: FloatArray,
    groups: IntArray,
    size: float,
    ox: int,
    oy: int,
    heads: IntArray,
    links: IntArray,
) -> None:
    heads.fill(-1)
    for index in range(len(positions)):
        x, y = floor(positions[index, 0] / size) - ox, floor(positions[index, 1] / size) - oy
        group = groups[index]
        links[index] = heads[group, y, x]
        heads[group, y, x] = index


def _nearest_grouped_batch(
    queries: FloatArray,
    positions: FloatArray,
    heads: IntArray,
    links: IntArray,
    allowed: IntArray,
    ox: int,
    oy: int,
    size: float,
) -> IntArray:
    results = np.full(len(queries), -1, dtype=np.int64)
    for row in range(len(queries)):
        x, y, radius = queries[row]
        best = -1
        distance = radius * radius
        for group in allowed:
            candidate = _nearest_dense_index(
                x, y, radius, positions, heads[group], links, ox, oy, size
            )
            if candidate >= 0:
                dx, dy = x - positions[candidate, 0], y - positions[candidate, 1]
                candidate_distance = dx * dx + dy * dy
                if (
                    candidate_distance < distance
                    or candidate_distance == distance
                    and (best < 0 or candidate < best)
                ):
                    best, distance = candidate, candidate_distance
        results[row] = best
    return results


_nearest_grouped_serial = compile_numeric(_nearest_grouped_batch)
_nearest_grouped_parallel = compile_parallel_numeric(_nearest_grouped_batch)


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
    if (2 * radius / bucket_size + 2) ** 2 > 4 * len(positions):
        return _nearest_scan(x, y, radius, positions)
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
def _nearest_scan(x: float, y: float, radius: float, positions: FloatArray) -> int:
    best = -1
    best_distance = radius * radius
    for index in range(len(positions)):
        dx, dy = x - positions[index, 0], y - positions[index, 1]
        distance = dx * dx + dy * dy
        if distance <= best_distance and (distance < best_distance or best < 0):
            best, best_distance = index, distance
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


@compile_numeric
def _build_dense_index(
    positions: FloatArray, bucket_size: float, ox: int, oy: int, width: int, height: int
) -> tuple[IntArray, IntArray]:
    heads = np.full((height, width), -1, dtype=np.int64)
    links = np.full(len(positions), -1, dtype=np.int64)
    for index in range(len(positions)):
        x, y = (
            floor(positions[index, 0] / bucket_size) - ox,
            floor(positions[index, 1] / bucket_size) - oy,
        )
        links[index] = heads[y, x]
        heads[y, x] = index
    return heads, links


@compile_numeric
def _nearest_dense_index(
    x: float,
    y: float,
    radius: float,
    positions: FloatArray,
    heads: IntArray,
    links: IntArray,
    ox: int,
    oy: int,
    bucket_size: float,
    first_only: bool = False,
) -> int:
    best = -1
    best_distance = radius * radius
    left = max(ox, floor((x - radius) / bucket_size))
    right = min(ox + heads.shape[1] - 1, floor((x + radius) / bucket_size))
    top = max(oy, floor((y - radius) / bucket_size))
    bottom = min(oy + heads.shape[0] - 1, floor((y + radius) / bucket_size))
    for by in range(top, bottom + 1):
        for bx in range(left, right + 1):
            index = heads[by - oy, bx - ox]
            if index == -1:
                continue
            dx = max(bx * bucket_size - x, 0.0, x - (bx + 1) * bucket_size)
            dy = max(by * bucket_size - y, 0.0, y - (by + 1) * bucket_size)
            if dx * dx + dy * dy > best_distance + 1e-12 * max(1.0, x * x + y * y):
                continue
            while index != -1:
                dx, dy = x - positions[index, 0], y - positions[index, 1]
                distance = dx * dx + dy * dy
                if distance <= best_distance and (
                    distance < best_distance or best < 0 or index < best
                ):
                    if first_only:
                        return int(index)
                    best, best_distance = index, distance
                index = links[index]
    return best


def _nearest_dense_batch(
    queries: FloatArray,
    positions: FloatArray,
    heads: IntArray,
    links: IntArray,
    ox: int,
    oy: int,
    bucket_size: float,
) -> IntArray:
    results = np.full(len(queries), -1, dtype=np.int64)
    for index in range(len(queries)):
        x, y, radius = queries[index]
        results[index] = _nearest_dense_index(
            x, y, radius, positions, heads, links, ox, oy, bucket_size
        )
    return results


_nearest_dense_many = compile_numeric(_nearest_dense_batch)
_nearest_dense_many_parallel = compile_parallel_numeric(_nearest_dense_batch)


def _any_dense_batch(
    queries: FloatArray,
    positions: FloatArray,
    heads: IntArray,
    links: IntArray,
    ox: int,
    oy: int,
    bucket_size: float,
) -> BoolArray:
    results = np.zeros(len(queries), dtype=np.bool_)
    for index in range(len(queries)):
        x, y, radius = queries[index]
        results[index] = (
            _nearest_dense_index(x, y, radius, positions, heads, links, ox, oy, bucket_size, True)
            >= 0
        )
    return results


_any_dense_many = compile_numeric(_any_dense_batch)
_any_dense_many_parallel = compile_parallel_numeric(_any_dense_batch)


@compile_numeric
def _intersects_dense(
    queries: FloatArray,
    positions: FloatArray,
    heads: IntArray,
    links: IntArray,
    ox: int,
    oy: int,
    size: float,
) -> bool:
    for row in range(len(queries)):
        x, y, radius = queries[row]
        if _nearest_dense_index(x, y, radius, positions, heads, links, ox, oy, size, True) >= 0:
            return True
    return False


@compile_numeric
def _nearby_dense(
    x: float,
    y: float,
    radius: float,
    positions: FloatArray,
    heads: IntArray,
    links: IntArray,
    ox: int,
    oy: int,
    bucket_size: float,
    output: IntArray,
) -> int:
    count = 0
    left = max(ox, floor((x - radius) / bucket_size))
    right = min(ox + heads.shape[1] - 1, floor((x + radius) / bucket_size))
    top = max(oy, floor((y - radius) / bucket_size))
    bottom = min(oy + heads.shape[0] - 1, floor((y + radius) / bucket_size))
    for by in range(top, bottom + 1):
        for bx in range(left, right + 1):
            index = heads[by - oy, bx - ox]
            while index != -1:
                dx, dy = x - positions[index, 0], y - positions[index, 1]
                if dx * dx + dy * dy <= radius * radius:
                    output[count] = index
                    count += 1
                index = links[index]
    output[:count].sort()
    return count


@compile_numeric
def _nearby_sparse(
    x: float,
    y: float,
    radius: float,
    positions: FloatArray,
    heads: Mapping[tuple[int, int], int],
    links: IntArray,
    bucket_size: float,
    output: IntArray,
) -> int:
    count = 0
    if (2 * radius / bucket_size + 2) ** 2 > 4 * len(positions):
        for index in range(len(positions)):
            dx, dy = x - positions[index, 0], y - positions[index, 1]
            if dx * dx + dy * dy <= radius * radius:
                output[count] = index
                count += 1
        return count
    for by in range(floor((y - radius) / bucket_size), floor((y + radius) / bucket_size) + 1):
        for bx in range(floor((x - radius) / bucket_size), floor((x + radius) / bucket_size) + 1):
            bucket = bx, by
            index = heads[bucket] if bucket in heads else -1
            while index != -1:
                dx, dy = x - positions[index, 0], y - positions[index, 1]
                if dx * dx + dy * dy <= radius * radius:
                    output[count] = index
                    count += 1
                index = links[index]
    output[:count].sort()
    return count


def warm_spatial_index() -> None:
    positions = np.empty((0, 2), dtype=np.float64)
    heads, links = _build_nearest_index(positions, 2.5)
    _nearest_index(0.0, 0.0, 0.0, positions, heads, links, 2.5)
    output = np.empty(0, dtype=np.int64)
    dense, dense_links = _build_dense_index(positions, 2.5, 0, 0, 1, 1)
    _nearest_dense_index(0.0, 0.0, 0.0, positions, dense, dense_links, 0, 0, 2.5)
    _intersects_dense(np.empty((0, 3), dtype=np.float64), positions, dense, dense_links, 0, 0, 2.5)
    _nearest_dense_many(
        np.empty((0, 3), dtype=np.float64), positions, dense, dense_links, 0, 0, 2.5
    )
    _nearest_dense_many_parallel(
        np.empty((0, 3), dtype=np.float64), positions, dense, dense_links, 0, 0, 2.5
    )
    for kernel in (_any_dense_many, _any_dense_many_parallel):
        kernel(np.empty((0, 3), dtype=np.float64), positions, dense, dense_links, 0, 0, 2.5)
    _nearby_dense(0.0, 0.0, 0.0, positions, dense, dense_links, 0, 0, 2.5, output)
    _nearby_sparse(0.0, 0.0, 0.0, positions, heads, links, 2.5, output)
    _nearest_many(np.empty((0, 3), dtype=np.float64), positions, heads, links, 2.5, (0.0,) * 4)
    grouped, grouped_links = np.empty((1, 1, 1), dtype=np.int64), np.empty(0, dtype=np.int64)
    _fill_grouped_index(positions, np.empty(0, dtype=np.int64), 2.5, 0, 0, grouped, grouped_links)
    for grouped_kernel in (_nearest_grouped_serial, _nearest_grouped_parallel):
        grouped_kernel(
            np.empty((0, 3), dtype=np.float64),
            positions,
            grouped,
            grouped_links,
            np.zeros(1, dtype=np.int64),
            0,
            0,
            2.5,
        )
