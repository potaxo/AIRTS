"""Authoritative player-specific visibility and explored-cell state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from math import ceil, floor, sqrt

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
        self._visible_row_masks = current_row_masks
        previous_visible = self.visible
        current: set[Cell] = set()
        for y, row_mask in enumerate(row_masks):
            current.update(_cells_for_row(y, row_mask))
        self.visible = current
        newly_visible = current.difference(previous_visible)
        no_longer_visible = previous_visible.difference(current)
        newly_explored = current.difference(self.explored)
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
        """Union dense same-height source rows without rebuilding one circle per unit."""

        self._source_masks.clear()
        groups: dict[str, dict[tuple[float, float], list[float]]] = {}
        for entity in entities.values():
            position = entity.selection_position
            groups.setdefault(entity.owner_id, {}).setdefault(
                (position.y, entity.vision_range), []
            ).append(position.x)
        changes: dict[str, tuple[int, int, int]] = {}
        for player_id in sorted(set(self.players).union(groups)):
            row_masks = [0] * self.game_map.height
            for (source_y, radius), source_xs in groups.get(player_id, {}).items():
                _union_source_row_group(
                    row_masks,
                    tuple(sorted(source_xs)),
                    source_y,
                    radius,
                    self.game_map.width,
                    self.game_map.height,
                )
            visibility = self.players.setdefault(
                player_id,
                PlayerVisibility(self.game_map.width, self.game_map.height),
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


def _union_source_row_group(
    row_masks: list[int],
    source_xs: tuple[float, ...],
    source_y: float,
    radius: float,
    width: int,
    height: int,
) -> None:
    for y, mask in _source_group_row_masks(source_xs, source_y, radius, width, height):
        row_masks[y] |= mask


@lru_cache(maxsize=4_096)
def _source_group_row_masks(
    source_xs: tuple[float, ...],
    source_y: float,
    radius: float,
    width: int,
    height: int,
) -> tuple[tuple[int, int], ...]:
    """Return the exact visibility union for one reusable same-height source rank."""

    maximum_gap = max(
        (second - first for first, second in zip(source_xs, source_xs[1:], strict=False)),
        default=0.0,
    )
    masks: list[tuple[int, int]] = []
    for y, horizontal_radius in _source_row_extents(source_y, radius, height):
        if maximum_gap <= horizontal_radius * 2 + 1.0:
            minimum_x = max(0, ceil(source_xs[0] - horizontal_radius - 0.5))
            maximum_x = min(width - 1, floor(source_xs[-1] + horizontal_radius - 0.5))
            if minimum_x <= maximum_x:
                masks.append((y, ((1 << (maximum_x - minimum_x + 1)) - 1) << minimum_x))
            continue
        interval_start: int | None = None
        interval_end = -1
        mask = 0
        for source_x in source_xs:
            minimum_x = max(0, ceil(source_x - horizontal_radius - 0.5))
            maximum_x = min(width - 1, floor(source_x + horizontal_radius - 0.5))
            if minimum_x > maximum_x:
                continue
            if interval_start is None:
                interval_start = minimum_x
                interval_end = maximum_x
            elif minimum_x <= interval_end + 1:
                interval_end = max(interval_end, maximum_x)
            else:
                mask |= ((1 << (interval_end - interval_start + 1)) - 1) << interval_start
                interval_start = minimum_x
                interval_end = maximum_x
        if interval_start is not None:
            mask |= ((1 << (interval_end - interval_start + 1)) - 1) << interval_start
        if mask:
            masks.append((y, mask))
    return tuple(masks)


@lru_cache(maxsize=8_192)
def _source_row_extents(
    source_y: float,
    radius: float,
    height: int,
) -> tuple[tuple[int, float], ...]:
    """Cache the vertical circle slice shared by every source on one row."""

    squared_radius = radius * radius
    minimum_y = max(0, int(source_y - radius))
    maximum_y = min(height - 1, ceil(source_y + radius))
    extents: list[tuple[int, float]] = []
    for y in range(minimum_y, maximum_y + 1):
        offset_y = source_y - (y + 0.5)
        remaining = squared_radius - offset_y * offset_y
        if remaining >= 0:
            extents.append((y, sqrt(remaining)))
    return tuple(extents)
