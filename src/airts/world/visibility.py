"""Authoritative player-specific visibility and explored-cell state."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from math import ceil, floor, sqrt
from operator import attrgetter

import numpy as np

from airts.contracts.spatial import Point
from airts.world._numeric import FloatArray, WordArray, compile_numeric, point_coordinates
from airts.world.entities import Entity
from airts.world.map import Cell, GameMap

PACKED_VISIBILITY_MIN_SOURCES = 1_024
_VisibilitySignature = bytes | tuple[tuple[float, float, float], ...]


class VisibilityState(StrEnum):
    UNEXPLORED = "unexplored"
    EXPLORED = "explored"
    VISIBLE = "visible"


class _ObservationTicks(Mapping[Cell, int]):
    """Visible cells share one clock; only cells losing sight need a stored timestamp."""

    def __init__(
        self, visible: set[Cell], explored: set[Cell], previous: Mapping[Cell, int]
    ) -> None:
        self.visible = visible
        self.explored = explored
        self.previous = dict(previous)
        self.tick: int | None = None

    def __getitem__(self, cell: Cell) -> int:
        if self.tick is not None and cell in self.visible:
            return self.tick
        return self.previous[cell]

    def __iter__(self) -> Iterator[Cell]:
        return iter(self.explored)

    def __len__(self) -> int:
        return len(self.explored)


@dataclass(slots=True)
class PlayerVisibility:
    width: int
    height: int
    visible: set[Cell] = field(default_factory=set)
    explored: set[Cell] = field(default_factory=set)
    last_observed_tick: Mapping[Cell, int] = field(default_factory=dict)
    _visible_row_masks: tuple[int, ...] = field(
        default_factory=tuple,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        self.last_observed_tick = _ObservationTicks(
            self.visible, self.explored, self.last_observed_tick
        )

    def state_at(self, cell: Cell) -> VisibilityState:
        if cell in self.visible:
            return VisibilityState.VISIBLE
        if cell in self.explored:
            return VisibilityState.EXPLORED
        return VisibilityState.UNEXPLORED

    def update(self, sources: tuple[tuple[Point, float], ...], tick: int) -> tuple[int, int, int]:
        return self.update_masks(
            tuple(
                _source_row_masks(position, radius, self.width, self.height)
                for position, radius in sources
            ),
            tick,
        )

    def update_masks(
        self,
        source_masks: tuple[tuple[tuple[int, int], ...], ...],
        tick: int,
    ) -> tuple[int, int, int]:
        row_masks = [0] * self.height
        for source in source_masks:
            for y, mask in source:
                row_masks[y] |= mask
        return self.update_combined_masks(row_masks, tick)

    def update_combined_masks(
        self,
        row_masks: list[int],
        tick: int,
    ) -> tuple[int, int, int]:
        current_row_masks = tuple(row_masks)
        observations = self.last_observed_tick
        assert isinstance(observations, _ObservationTicks)
        if current_row_masks == self._visible_row_masks:
            observations.tick = tick
            return 0, 0, 0
        newly_visible: set[Cell] = set()
        no_longer_visible: set[Cell] = set()
        previous_masks = self._visible_row_masks
        if not previous_masks:
            restored_masks = [0] * self.height
            for x, y in self.visible:
                restored_masks[y] |= 1 << x
            previous_masks = tuple(restored_masks)
        for y, mask in enumerate(row_masks):
            previous = previous_masks[y]
            newly_visible.update(_cells_for_row(y, mask & ~previous))
            no_longer_visible.update(_cells_for_row(y, previous & ~mask))
        if observations.tick is not None:
            observations.previous.update(dict.fromkeys(no_longer_visible, observations.tick))
        self.visible.difference_update(no_longer_visible)
        self.visible.update(newly_visible)
        self._visible_row_masks = current_row_masks
        newly_explored = newly_visible.difference(self.explored)
        self.explored.update(newly_visible)
        observations.tick = tick
        return len(newly_visible), len(newly_explored), len(no_longer_visible)

    def to_dict(self) -> dict[str, object]:
        ordered_explored = sorted(self.explored, key=lambda cell: (cell[1], cell[0]))
        ordered_visible = sorted(self.visible, key=lambda cell: (cell[1], cell[0]))
        return {
            "visible": [[x, y] for x, y in ordered_visible],
            "explored": [[x, y] for x, y in ordered_explored],
            "last_observed_tick": [
                [x, y, self.last_observed_tick[(x, y)]] for x, y in ordered_explored
            ],
        }


class VisibilitySystem:
    def __init__(self, game_map: GameMap) -> None:
        self.game_map = game_map
        _visibility_words(np.empty((0, 3), dtype=np.float64), game_map.width, game_map.height)
        self.players: dict[str, PlayerVisibility] = {}
        self._grouped_masks: dict[str, tuple[_VisibilitySignature, list[int]]] = {}
        self._source_masks: dict[
            str,
            tuple[str, Point, float, tuple[tuple[int, int], ...]],
        ] = {}

    def update(
        self,
        entities: dict[str, Entity],
        tick: int,
        *,
        positions_by_owner: dict[str, dict[str, Point]] | None = None,
    ) -> dict[str, tuple[int, int, int]]:
        if len(entities) > 128:
            return self._update_grouped(entities, tick, positions_by_owner)
        self._grouped_masks.clear()
        sources: dict[str, list[tuple[tuple[int, int], ...]]] = {}
        for entity_id, entity in entities.items():
            position = entity.selection_position
            radius = entity.vision_range
            cached = self._source_masks.get(entity_id)
            if (
                cached is None
                or cached[0] != entity.owner_id
                or cached[1] != position
                or cached[2] != radius
            ):
                masks = _source_row_masks(
                    position,
                    radius,
                    self.game_map.width,
                    self.game_map.height,
                )
                cached = (entity.owner_id, position, radius, masks)
                self._source_masks[entity_id] = cached
            sources.setdefault(entity.owner_id, []).append(cached[3])
        for removed_id in self._source_masks.keys() - entities.keys():
            del self._source_masks[removed_id]
        changes: dict[str, tuple[int, int, int]] = {}
        for player_id in sorted(set(self.players).union(sources)):
            visibility = self.players.setdefault(
                player_id, PlayerVisibility(self.game_map.width, self.game_map.height)
            )
            changes[player_id] = visibility.update_masks(tuple(sources.get(player_id, [])), tick)
        return changes

    def _update_grouped(
        self,
        entities: dict[str, Entity],
        tick: int,
        positions_by_owner: dict[str, dict[str, Point]] | None,
    ) -> dict[str, tuple[int, int, int]]:
        """Compute exact circle unions without rebuilding Python masks for every unit."""
        self._source_masks.clear()
        packed_positions = (
            positions_by_owner if len(entities) >= PACKED_VISIBILITY_MIN_SOURCES else None
        )
        if packed_positions is None and len(entities) >= PACKED_VISIBILITY_MIN_SOURCES:
            packed_positions = {}
            for key, entity in entities.items():
                packed_positions.setdefault(entity.owner_id, {})[key] = entity.selection_position
        sources: dict[str, list[tuple[float, float, float]]] = {}
        if packed_positions is None:
            for entity in entities.values():
                center = entity.selection_position
                sources.setdefault(entity.owner_id, []).append(
                    (center.x, center.y, entity.vision_range)
                )
        changes: dict[str, tuple[int, int, int]] = {}
        owners = sources if packed_positions is None else packed_positions
        for player_id in sorted(set(self.players).union(owners)):
            signature: _VisibilitySignature
            if packed_positions is None:
                signature = tuple(sources.get(player_id, ()))
                array = None
            else:
                points = packed_positions.get(player_id, {})
                array = np.empty((len(points), 3), dtype=np.float64)
                point_coordinates(tuple(points.values()), array[:, :2])
                array[:, 2] = np.fromiter(
                    map(attrgetter("vision_range"), map(entities.__getitem__, points)),
                    np.float64,
                    len(points),
                )
                signature = array.tobytes()
            cached = self._grouped_masks.get(player_id)
            if cached is None or cached[0] != signature:
                if array is None:
                    array = np.asarray(signature, dtype=np.float64).reshape((-1, 3))
                words = _visibility_words(array, self.game_map.width, self.game_map.height)
                if packed_positions is None:
                    row_masks = [
                        int.from_bytes(row.astype("<u8", copy=False).tobytes(), "little")
                        for row in words
                    ]
                else:
                    packed = words.astype("<u8", copy=False).tobytes()
                    stride = words.shape[1] * words.itemsize
                    row_masks = [
                        int.from_bytes(packed[offset : offset + stride], "little")
                        for offset in range(0, len(packed), stride)
                    ]
                self._grouped_masks[player_id] = signature, row_masks
            else:
                row_masks = cached[1]
            visibility = self.players.setdefault(
                player_id, PlayerVisibility(self.game_map.width, self.game_map.height)
            )
            changes[player_id] = visibility.update_combined_masks(row_masks, tick)
        return changes

    def for_player(self, player_id: str) -> PlayerVisibility:
        return self.players.setdefault(
            player_id, PlayerVisibility(self.game_map.width, self.game_map.height)
        )

    def to_dict(self) -> dict[str, object]:
        return {player_id: state.to_dict() for player_id, state in sorted(self.players.items())}


@lru_cache(maxsize=32_768)
def _source_row_masks(
    position: Point,
    radius: float,
    width: int,
    height: int,
) -> tuple[tuple[int, int], ...]:
    squared_radius = radius * radius
    minimum_y = max(0, int(position.y - radius))
    maximum_y = min(height - 1, ceil(position.y + radius))
    masks: list[tuple[int, int]] = []
    for y in range(minimum_y, maximum_y + 1):
        offset_y = position.y - (y + 0.5)
        remaining = squared_radius - offset_y * offset_y
        if remaining < 0:
            continue
        horizontal_radius = sqrt(remaining)
        minimum_x = max(0, ceil(position.x - horizontal_radius - 0.5))
        maximum_x = min(width - 1, floor(position.x + horizontal_radius - 0.5))
        if minimum_x <= maximum_x:
            mask_width = maximum_x - minimum_x + 1
            masks.append((y, ((1 << mask_width) - 1) << minimum_x))
    return tuple(masks)


@lru_cache(maxsize=8_192)
def _cells_for_row(y: int, row_mask: int) -> tuple[Cell, ...]:
    cells: list[Cell] = []
    while row_mask:
        bit = row_mask & -row_mask
        cells.append((bit.bit_length() - 1, y))
        row_mask ^= bit
    return tuple(cells)


@compile_numeric
def _visibility_words(sources: FloatArray, width: int, height: int) -> WordArray:
    """OR exact cell-center circle intervals into 64-bit row chunks at any map width."""
    words = np.zeros((height, (width + 63) // 64), dtype=np.uint64)
    for source in sources:
        x, y, radius = source
        for row in range(max(0, int(y - radius)), min(height - 1, ceil(y + radius)) + 1):
            offset = y - (row + 0.5)
            remaining = radius * radius - offset * offset
            if remaining < 0:
                continue
            extent = sqrt(remaining)
            left = max(0, ceil(x - extent - 0.5))
            right = min(width - 1, floor(x + extent - 0.5))
            if left > right:
                continue
            for column in range(left // 64, right // 64 + 1):
                low = max(left, column * 64) - column * 64
                high = min(right, column * 64 + 63) - column * 64
                count = high - low + 1
                mask = ~np.uint64(0) if count == 64 else (np.uint64(1) << count) - np.uint64(1)
                words[row, column] |= mask << low
    return words
