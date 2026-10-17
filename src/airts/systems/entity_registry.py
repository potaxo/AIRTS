"""Shared authoritative entity identity allocation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from airts.simulation import Simulation


def allocate_entity_id(simulation: Simulation, prefix: str) -> str:
    """Allocate a stable unique entity ID from the simulation-owned sequence."""

    while True:
        entity_id = f"{prefix}_{simulation._next_entity_number:03d}"
        simulation._next_entity_number += 1
        if entity_id not in simulation.entities:
            return entity_id
