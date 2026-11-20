"""Shared physical spawn certification for production and ambient arrivals."""

from __future__ import annotations

from math import floor
from typing import TYPE_CHECKING

from airts.contracts.spatial import Point
from airts.navigation.collision.geometry import collision_radius
from airts.world.entities import Entity
from airts.world.map import Cell
from airts.world.units.catalog import EntityKind

if TYPE_CHECKING:
    from collections.abc import Iterator

    from airts.simulation import Simulation


def spawn_cell_available(simulation: Simulation, cell: Cell, kind: EntityKind) -> bool:
    """Certify a cell-centered spawn against authoritative nearby mobile discs."""

    blockers = spawn_cell_blockers(simulation, cell, kind)
    return blockers is not None and next(blockers, None) is None


def spawn_cell_blockers(
    simulation: Simulation, cell: Cell, kind: EntityKind
) -> Iterator[Entity] | None:
    """Return mobile blockers for a usable exit, or None for a static obstruction."""
    if not simulation.game_map.is_cell_passable(cell):
        return None
    occupants = simulation.occupancy.occupants(cell)
    if any(not simulation.entities[key].is_movable for key in occupants):
        return None
    return _mobile_blockers(simulation, cell, kind)


def _mobile_blockers(simulation: Simulation, cell: Cell, kind: EntityKind) -> Iterator[Entity]:
    point = Point(cell[0] + 0.5, cell[1] + 0.5)
    radius = collision_radius(kind)
    maximum_radius = max(collision_radius(item) for item in EntityKind if item.profile.movable)
    reach = radius + maximum_radius
    nearby_ids = simulation.occupancy.occupants_in(
        floor(point.x - reach),
        floor(point.y - reach),
        floor(point.x + reach) + 1,
        floor(point.y + reach) + 1,
    )
    for entity_id in sorted(nearby_ids):
        entity = simulation.entities[entity_id]
        if not entity.is_movable:
            continue
        simulation._collision_pair_check_count += 1
        contact = radius + collision_radius(entity.kind)
        dx = point.x - entity.position.x
        dy = point.y - entity.position.y
        if dx * dx + dy * dy < contact * contact - 1e-12:
            yield entity


def factory_exit_cells(source: Entity) -> tuple[Cell, ...]:
    occupied = source.occupied_cells
    candidates: set[Cell] = set()
    for x, y in occupied:
        candidates.update({(x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)})
    return tuple(sorted(candidates - occupied, key=lambda item: (item[1], item[0])))


def find_spawn_point(simulation: Simulation, source: Entity, kind: EntityKind) -> Point | None:
    for cell in factory_exit_cells(source):
        if spawn_cell_available(simulation, cell, kind):
            return Point(cell[0] + 0.5, cell[1] + 0.5)
    return None
