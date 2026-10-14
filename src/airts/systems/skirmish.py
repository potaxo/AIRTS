"""Team result evaluation and saved match validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import ConstructionParameters
from airts.world.units.catalog import EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def owner_status(
    simulation: Simulation, owner_id: str, *, command_centers_produce: bool = True
) -> str:
    assets = [entity for entity in simulation.entities.values() if entity.owner_id == owner_id]
    if not assets:
        return "out"
    if any(
        (
            entity.kind in {EntityKind.FACTORY, EntityKind.BUILDER}
            or command_centers_produce
            and entity.kind is EntityKind.COMMAND_CENTER
        )
        and entity.is_complete
        for entity in assets
    ):
        return "active"
    return "eliminated"


def evaluate_result(simulation: Simulation, *, command_centers_produce: bool = True) -> int | None:
    """None means ongoing; zero is a draw; positive values identify the winning team."""

    surviving = {
        slot.team
        for slot in simulation.game_map.match_slots
        if owner_status(simulation, slot.owner_id, command_centers_produce=command_centers_produce)
        != "out"
    }
    if len(surviving) <= 1:
        return next(iter(surviving)) if surviving else 0
    if not any(
        entity.is_complete
        and (
            entity.kind.production_kinds
            or entity.kind is EntityKind.BUILDER
            or entity.kind.profile.attack_damage > 0
        )
        for entity in simulation.entities.values()
    ):
        return 0
    return None


def validate_match_state(simulation: Simulation) -> None:
    """Check saved match ownership and resource-site authority."""

    owners = {slot.owner_id for slot in simulation.game_map.match_slots}
    if any(entity.owner_id not in owners for entity in simulation.entities.values()):
        raise ValueError("saved match entity has an unknown owner")
    for entity in simulation.entities.values():
        if entity.kind is EntityKind.RESOURCE_GENERATOR and entity.position not in (
            simulation.game_map.resource_sites or ()
        ):
            raise ValueError("saved extractor is outside a resource site")
    for automation in simulation.live_automations:
        if automation.owner_id not in owners:
            raise ValueError("saved match automation has an unknown owner")
        if isinstance(automation.parameters, ConstructionParameters) and (
            automation.parameters.building_kind is EntityKind.RESOURCE_GENERATOR
            and automation.parameters.position not in (simulation.game_map.resource_sites or ())
        ):
            raise ValueError("saved extractor construction is outside a resource site")
