"""Shared command-boundary validation independent of concrete command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.commands import ValidationFailure, ValidationPhase
from airts.contracts.spatial import PointTarget, SpatialTarget

if TYPE_CHECKING:
    from airts.simulation import Simulation


def validate_geometry(simulation: Simulation, target: SpatialTarget) -> ValidationFailure | None:
    points = (target.point,) if isinstance(target, PointTarget) else target.points
    if any(not simulation.game_map.contains(point) for point in points):
        return ValidationFailure(ValidationPhase.SPATIAL, "TARGET_OUTSIDE_MAP", "target")
    return None


def validate_entities(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    owner_id: str,
    *,
    require_movable: bool = False,
) -> ValidationFailure | None:
    if not entity_ids:
        return ValidationFailure(ValidationPhase.REFERENCE, "NO_ENTITIES", "entity_ids")
    if len(set(entity_ids)) != len(entity_ids):
        return ValidationFailure(ValidationPhase.REFERENCE, "DUPLICATE_ENTITY", "entity_ids")
    unknown = next((item for item in entity_ids if item not in simulation.entities), None)
    if unknown is not None:
        return ValidationFailure(
            ValidationPhase.REFERENCE,
            f"UNKNOWN_ENTITY:{unknown}",
            "entity_ids",
            {"entity_id": unknown},
        )
    unowned = next(
        (item for item in entity_ids if simulation.entities[item].owner_id != owner_id), None
    )
    if unowned is not None:
        return ValidationFailure(
            ValidationPhase.OWNERSHIP,
            f"ENTITY_NOT_OWNED:{unowned}",
            "entity_ids",
            {"entity_id": unowned, "owner_id": simulation.entities[unowned].owner_id},
        )
    if require_movable:
        immovable = next(
            (item for item in entity_ids if not simulation.entities[item].is_movable), None
        )
        if immovable is not None:
            return ValidationFailure(
                ValidationPhase.CAPABILITY,
                f"ENTITY_NOT_MOVABLE:{immovable}",
                "entity_ids",
                {"entity_id": immovable},
            )
    return None
