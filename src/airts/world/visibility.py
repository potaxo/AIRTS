"""Authoritative player-specific visibility and explored-cell state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
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

    def state_at(self, cell: Cell) -> VisibilityState:
        if cell in self.visible:
            return VisibilityState.VISIBLE
        if cell in self.explored:
            return VisibilityState.EXPLORED
        return VisibilityState.UNEXPLORED

    def update(self, sources: tuple[tuple[Point, float], ...], tick: int) -> tuple[int, int, int]:
        previous_visible = self.visible
        current: set[Cell] = set()
        row_masks = [0] * self.height
        for position, radius in sources:
            squared_radius = radius * radius
            minimum_y = max(0, int(position.y - radius))
            maximum_y = min(self.height - 1, ceil(position.y + radius))
            for y in range(minimum_y, maximum_y + 1):
                offset_y = position.y - (y + 0.5)
                remaining = squared_radius - offset_y * offset_y
                if remaining < 0:
                    continue
                horizontal_radius = sqrt(remaining)
                minimum_x = max(0, ceil(position.x - horizontal_radius - 0.5))
                maximum_x = min(
                    self.width - 1,
                    floor(position.x + horizontal_radius - 0.5),
                )
                if minimum_x <= maximum_x:
                    width = maximum_x - minimum_x + 1
                    row_masks[y] |= ((1 << width) - 1) << minimum_x
        for y, row_mask in enumerate(row_masks):
            while row_mask:
                bit = row_mask & -row_mask
                current.add((bit.bit_length() - 1, y))
                row_mask ^= bit
        self.visible = current
        newly_visible = current.difference(previous_visible)
        no_longer_visible = previous_visible.difference(current)
        newly_explored = current.difference(self.explored)
        self.explored.update(current)
        for cell in current:
            self.last_observed_tick[cell] = tick
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

    def update(self, entities: dict[str, Entity], tick: int) -> dict[str, tuple[int, int, int]]:
        sources: dict[str, list[tuple[Point, float]]] = {}
        for entity in entities.values():
            sources.setdefault(entity.owner_id, []).append(
                (entity.selection_position, entity.vision_range)
            )
        changes: dict[str, tuple[int, int, int]] = {}
        for player_id in sorted(set(self.players).union(sources)):
            visibility = self.players.setdefault(
                player_id, PlayerVisibility(self.game_map.width, self.game_map.height)
            )
            changes[player_id] = visibility.update(tuple(sources.get(player_id, [])), tick)
        return changes

    def for_player(self, player_id: str) -> PlayerVisibility:
        return self.players.setdefault(
            player_id, PlayerVisibility(self.game_map.width, self.game_map.height)
        )

    def to_dict(self) -> dict[str, object]:
        return {player_id: state.to_dict() for player_id, state in sorted(self.players.items())}
