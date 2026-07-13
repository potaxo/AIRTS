"""Authoritative dynamic occupancy for entities and building footprints."""

from __future__ import annotations

from airts.world.map_model import Cell


class OccupancyError(ValueError):
    """Raised when an entity cannot atomically occupy requested cells."""


class OccupancyGrid:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._cells: dict[Cell, set[str]] = {}
        self._entity_cells: dict[str, frozenset[Cell]] = {}

    def place(self, entity_id: str, cells: frozenset[Cell]) -> None:
        if entity_id in self._entity_cells:
            raise OccupancyError(f"entity already placed: {entity_id}")
        self._validate_available(entity_id, cells)
        self._set_cells(entity_id, cells)

    def move(
        self,
        entity_id: str,
        cells: frozenset[Cell],
        allowed_conflicts: frozenset[str] = frozenset(),
    ) -> None:
        if entity_id not in self._entity_cells:
            raise OccupancyError(f"entity is not placed: {entity_id}")
        self._validate_available(entity_id, cells, allowed_conflicts)
        self.remove(entity_id)
        self._set_cells(entity_id, cells)

    def remove(self, entity_id: str) -> None:
        cells = self._entity_cells.pop(entity_id, frozenset())
        for cell in cells:
            occupants = self._cells[cell]
            occupants.remove(entity_id)
            if not occupants:
                del self._cells[cell]

    def occupants(self, cell: Cell) -> frozenset[str]:
        return frozenset(self._cells.get(cell, set()))

    def cells_for(self, entity_id: str) -> frozenset[Cell]:
        return self._entity_cells.get(entity_id, frozenset())

    def blocked_cells(self, excluding: frozenset[str] = frozenset()) -> frozenset[Cell]:
        return frozenset(
            cell for cell, occupants in self._cells.items() if occupants.difference(excluding)
        )

    def snapshot(self) -> dict[str, list[list[int]]]:
        return {
            entity_id: [[x, y] for x, y in sorted(cells, key=lambda cell: (cell[1], cell[0]))]
            for entity_id, cells in sorted(self._entity_cells.items())
        }

    def _validate_available(
        self,
        entity_id: str,
        cells: frozenset[Cell],
        allowed_conflicts: frozenset[str] = frozenset(),
    ) -> None:
        if not cells:
            raise OccupancyError("an entity must occupy at least one cell")
        for cell in cells:
            if not (0 <= cell[0] < self.width and 0 <= cell[1] < self.height):
                raise OccupancyError(f"cell outside occupancy grid: {cell}")
            conflicts = self._cells.get(cell, set()).difference({entity_id}, allowed_conflicts)
            if conflicts:
                conflict = min(conflicts)
                raise OccupancyError(f"cell {cell} is occupied by {conflict}")

    def _set_cells(self, entity_id: str, cells: frozenset[Cell]) -> None:
        self._entity_cells[entity_id] = cells
        for cell in cells:
            self._cells.setdefault(cell, set()).add(entity_id)
