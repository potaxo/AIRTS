"""Authoritative player-specific visibility and explored-cell state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from math import ceil, floor, sqrt

import numpy as np

from airts._numeric import FloatArray, WordArray, compile_numeric
from airts.geometry import Point
from airts.world.entities import Entity
from airts.world.map_model import Cell, GameMap


class VisibilityState(StrEnum):
    UNEXPLORED = "unexplored"
    EXPLORED = "explored"
    VISIBLE = "visible"


@dataclass(slots=True)
class PlayerVisibility:
    width: int
    height: int
    visible: set[Cell] = field(default_factory=set)
    explored: set[Cell] = field(default_factory=set)
    last_observed_tick: dict[Cell, int] = field(default_factory=dict)
    _visible_row_masks: tuple[int, ...] = field(
        default_factory=tuple,
        repr=False,
        compare=False,
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
        if current_row_masks == self._visible_row_masks:
            self.last_observed_tick.update(dict.fromkeys(self.visible, tick))
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
        current = self.visible.difference(no_longer_visible)
        current.update(newly_visible)
        self._visible_row_masks = current_row_masks
        self.visible = current
        newly_explored = newly_visible.difference(self.explored)
        self.explored.update(current)
        self.last_observed_tick.update(dict.fromkeys(current, tick))
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
        self._source_masks: dict[
            str,
            tuple[str, Point, float, tuple[tuple[int, int], ...]],
        ] = {}

    def update(self, entities: dict[str, Entity], tick: int) -> dict[str, tuple[int, int, int]]:
        if len(entities) > 128:
            return self._update_grouped(entities, tick)
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
    ) -> dict[str, tuple[int, int, int]]:
        """Compute exact circle unions without rebuilding Python masks for every unit."""
        self._source_masks.clear()
        sources: dict[str, list[tuple[float, float, float]]] = {}
        for entity in entities.values():
            position = entity.selection_position
            sources.setdefault(entity.owner_id, []).append(
                (position.x, position.y, entity.vision_range)
            )
        changes: dict[str, tuple[int, int, int]] = {}
        for player_id in sorted(set(self.players).union(sources)):
            array = np.asarray(sources.get(player_id, []), dtype=np.float64).reshape((-1, 3))
            words = _visibility_words(array, self.game_map.width, self.game_map.height)
            row_masks = [
                sum(int(word) << (64 * index) for index, word in enumerate(row)) for row in words
            ]
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
