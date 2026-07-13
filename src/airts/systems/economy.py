"""Deterministic resource income and ambient enemy generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.automations import Automation, AutomationStatus, EconomyParameters
from airts.events import EventType
from airts.geometry import Point
from airts.world.entities import Entity, UnitState
from airts.world.map_model import EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def drive_economy(simulation: Simulation, automation: Automation) -> None:
    """Update an economy automation from the authoritative owner balance."""

    parameters = economy_parameters(automation)
    parameters.collected = max(
        parameters.collected,
        simulation.resources.get(automation.owner_id, 0) - parameters.starting_resources,
    )
    if simulation.resources.get(automation.owner_id, 0) >= parameters.target_resources:
        simulation._transition(automation, AutomationStatus.COMPLETED, "RESOURCE_TARGET_REACHED")
        simulation._release_automation(automation)
        return
    active = [
        generator_id
        for generator_id in parameters.generator_ids
        if simulation.assignments.get(generator_id) == automation.automation_id
        and generator_id in simulation.entities
    ]
    if not active:
        simulation._transition(automation, AutomationStatus.FAILED, "NO_RESOURCE_GENERATORS")


def generate_income(simulation: Simulation) -> None:
    """Credit the fixed generator income interval in stable owner order."""

    if simulation.tick % 10:
        return
    generators: dict[str, int] = {}
    for entity in simulation.entities.values():
        if entity.kind is EntityKind.RESOURCE_GENERATOR:
            generators[entity.owner_id] = generators.get(entity.owner_id, 0) + 1
    for owner_id, count in sorted(generators.items()):
        amount = 1000 * count
        simulation.resources[owner_id] = simulation.resources.get(owner_id, 0) + amount
        simulation.events.record(
            simulation.tick,
            EventType.RESOURCE_CHANGED,
            owner_id,
            amount=amount,
            balance=simulation.resources[owner_id],
            reason="GENERATOR_INCOME",
        )


def spawn_ambient_enemy(simulation: Simulation) -> None:
    """Create a seeded enemy tank on the right side at the configured interval."""

    if (
        not simulation.ambient_enemy_spawns
        or simulation.tick % simulation.enemy_spawn_interval_ticks
        or sum(
            entity.owner_id == "enemy" and entity.is_movable
            for entity in simulation.entities.values()
        )
        >= simulation.enemy_spawn_cap
    ):
        return
    minimum_x = max(0, int(simulation.game_map.width * 0.7))
    candidates = [
        (x, y)
        for y in range(simulation.game_map.height)
        for x in range(minimum_x, simulation.game_map.width)
        if simulation.game_map.is_cell_passable((x, y))
        and not simulation.occupancy.occupants((x, y))
    ]
    if not candidates:
        return
    random_value = (
        simulation.random_seed * 1_103_515_245
        + simulation.tick * 12_345
        + simulation._next_entity_number * 2_654_435_761
    ) & 0x7FFFFFFF
    cell = candidates[random_value % len(candidates)]
    kind = (EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK)[
        (random_value // max(1, len(candidates))) % 2
    ]
    while True:
        entity_id = f"enemy_tank_{simulation._next_entity_number:03d}"
        simulation._next_entity_number += 1
        if entity_id not in simulation.entities:
            break
    position = Point(cell[0] + 0.5, cell[1] + 0.5)
    entity = Entity(
        entity_id=entity_id,
        kind=kind,
        owner_id="enemy",
        position=position,
        health=kind.profile.max_health,
    )
    targets = [target for target in simulation.entities.values() if target.owner_id == "player"]
    if targets:
        target = min(
            targets,
            key=lambda item: (
                position.distance_to(item.selection_position),
                item.entity_id,
            ),
        )
        entity.attack_target_id = target.entity_id
        entity.pursue_target = True
        entity.state = UnitState.ATTACKING
    simulation.entities[entity_id] = entity
    simulation.occupancy.place(entity_id, entity.occupied_cells)
    simulation.resources.setdefault("enemy", 500)
    simulation.events.record(
        simulation.tick,
        EventType.ENEMY_REINFORCEMENT_SPAWNED,
        entity_id,
        kind=kind.value,
        position=[position.x, position.y],
        target_id=entity.attack_target_id,
    )


def economy_parameters(automation: Automation) -> EconomyParameters:
    """Narrow a generic automation to its economy parameters."""

    if not isinstance(automation.parameters, EconomyParameters):
        raise TypeError("automation does not have economy parameters")
    return automation.parameters
