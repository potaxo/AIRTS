"""Read current production and upgrade progress for scene and panel feedback."""

from airts.contracts.automations import ProductionParameters
from airts.simulation import Simulation
from airts.world.entities import Entity


def current_production(simulation: Simulation, entity_id: str) -> ProductionParameters | None:
    """Use the source's assigned job, excluding queued or preempted work."""
    job = simulation.automations.get(simulation.assignments.get(entity_id, ""))
    if job is None or job.status.terminal or not isinstance(job.parameters, ProductionParameters):
        return None
    return job.parameters


def building_progress(simulation: Simulation, entity: Entity) -> tuple[float, ...]:
    """Keep paid spawn-blocked work full and give concurrent work separate tracks."""
    if not entity.is_complete:
        return ()
    progress = []
    if entity.kind.production_kinds:
        production = current_production(simulation, entity.entity_id)
        if production is not None:
            progress.append(min(1.0, production.progress_ticks / production.build_ticks))
    if entity.upgrade.remaining_ticks and (definition := entity.next_upgrade) is not None:
        progress.append(1 - entity.upgrade.remaining_ticks / definition.duration_ticks)
    return tuple(progress)
