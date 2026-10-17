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
    from airts.simulation import Simulation


def spawn_cell_available(simulation: Simulation, cell: Cell, kind: EntityKind) -> bool:
    """Certify a cell-centered spawn against authoritative nearby mobile discs."""

    if not simulation.game_map.is_cell_passable(cell):
        return False
    occupants = simulation.occupancy.occupants(cell)
    if any(not simulation.entities[key].is_movable for key in occupants):
        return False
    point = Point(cell[0] + 0.5, cell[1] + 0.5)
    radius = collision_radius(kind)
    maximum_radius = max(collision_radius(item) for item in EntityKind if item.profile.movable)
    reach = radius + maximum_radius
    nearby_ids: set[str] = set()
    for y in range(floor(point.y - reach), floor(point.y + reach) + 1):
        for x in range(floor(point.x - reach), floor(point.x + reach) + 1):
            if simulation.game_map.contains_cell((x, y)):
                nearby_ids.update(simulation.occupancy.occupants((x, y)))
    for entity_id in sorted(nearby_ids):
        entity = simulation.entities[entity_id]
        if not entity.is_movable:
            continue
        simulation._collision_pair_check_count += 1
        contact = radius + collision_radius(entity.kind)
        dx = point.x - entity.position.x
        dy = point.y - entity.position.y
        if dx * dx + dy * dy < contact * contact - 1e-12:
            return False
    return True


def find_spawn_point(simulation: Simulation, source: Entity, kind: EntityKind) -> Point | None:
    occupied = source.occupied_cells
    candidates: set[Cell] = set()
    for x, y in occupied:
        candidates.update({(x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)})
    for cell in sorted(candidates, key=lambda item: (item[1], item[0])):
        if cell not in occupied and spawn_cell_available(simulation, cell, kind):
            return Point(cell[0] + 0.5, cell[1] + 0.5)
    return None
