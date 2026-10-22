"""Read-only work connections shared by visual facing and both renderers."""

from __future__ import annotations

from collections.abc import Iterator
from math import cos, sin
from typing import TYPE_CHECKING

from airts.contracts.automations import (
    AutomationStatus,
    ConstructionParameters,
    RepairParameters,
    RepairPhase,
)
from airts.presentation.visuals.catalog import APPEARANCES
from airts.systems.construction import construction_distance
from airts.systems.repair import building_distance
from airts.systems.repair_positions import in_service_range
from airts.world.entities import Entity, UnitState
from airts.world.units.schema import EntityCategory

if TYPE_CHECKING:
    from airts.simulation import Simulation


def work_sources(simulation: Simulation) -> Iterator[tuple[Entity, Entity]]:
    """Yield only active, in-range construction/repair sources and their targets."""
    source: Entity | None
    for worker in simulation.entities.values():
        if not worker.profile.can_repair or not worker.is_complete:
            continue
        target = simulation.entities.get(worker.repair_target_id or "")
        if (
            target is not None
            and target.owner_id == worker.owner_id
            and (not target.is_complete or target.health < target.kind.profile.max_health)
            and (
                not worker.is_movable
                and target is not worker
                and worker.selection_position.distance_to(target.selection_position)
                <= worker.profile.repair_range
                or worker.is_movable
                and target.category is EntityCategory.BUILDING
                and worker.state is UnitState.REPAIRING
                and not worker.path
                and building_distance(worker.position, target) <= worker.profile.repair_range
            )
        ):
            yield worker, target
    for automation in simulation.live_automations:
        parameters = automation.parameters
        if (
            isinstance(parameters, ConstructionParameters)
            and automation.status is AutomationStatus.ACTIVE
        ):
            target = simulation.entities.get(parameters.site_entity_id or "")
            if target is None or target.is_complete:
                continue
            for key in automation.entity_ids:
                source = simulation.entities.get(key)
                if (
                    source is not None
                    and simulation.assignments.get(key) == automation.automation_id
                    and source.state is UnitState.BUILDING
                    and construction_distance(source.position, parameters)
                    <= source.profile.build_range
                ):
                    yield source, target
        elif isinstance(parameters, RepairParameters):
            for key, phase in parameters.phases.items():
                target = simulation.entities.get(key)
                source = simulation.entities.get(parameters.destinations.get(key, ""))
                if (
                    phase is RepairPhase.REPAIRING
                    and simulation.assignments.get(key) == automation.automation_id
                    and source is not None
                    and target is not None
                    and not source.profile.can_repair
                    and source.is_complete
                    and source.owner_id == target.owner_id
                    and target.state is UnitState.REPAIRING
                    and target.health < target.kind.profile.max_health
                    and not target.path
                    and in_service_range(simulation, target, source)
                ):
                    yield source, target


def tool_offset(entity: Entity, angle: float, size: tuple[float, float]) -> tuple[float, float]:
    """Rotate the authored tool port using the displayed sprite size."""
    port = APPEARANCES[entity.kind].work_port
    if port is None:
        return 0.0, 0.0
    x, y = port[0] * size[0], port[1] * size[1]
    c, s = cos(angle), sin(angle)
    return c * x - s * y, s * x + c * y
