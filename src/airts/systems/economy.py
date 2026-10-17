"""Deterministic resource income and ambient enemy generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import Automation, AutomationStatus, economy_parameters
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.systems import automation_assignments, automation_lifecycle
from airts.systems.entity_registry import allocate_entity_id
from airts.systems.spawning import spawn_cell_available
from airts.world.entities import Entity, UnitState
from airts.world.units.catalog import EntityKind

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
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.COMPLETED, "RESOURCE_TARGET_REACHED"
        )
        automation_assignments.release_automation(simulation, automation)
        return
    active = [
        generator_id
        for generator_id in parameters.generator_ids
        if simulation.assignments.get(generator_id) == automation.automation_id
        and generator_id in simulation.entities
    ]
    if not active:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.FAILED, "NO_RESOURCE_GENERATORS"
        )


def rebuild_income(simulation: Simulation) -> None:
    """Reconstruct derived owner totals at initialization and after loading."""

    simulation._income_per_second = {}
    for entity in simulation.entities.values():
        if entity.is_complete:
            change_income(simulation, entity.owner_id, entity.profile.income_per_second)


def change_income(simulation: Simulation, owner_id: str, amount: int) -> None:
    """Apply a producer's completion, upgrade delta or removal exactly once."""

    if not amount:
        return
    total = simulation._income_per_second.get(owner_id, 0) + amount
    if total < 0:
        raise ValueError(f"negative income total for {owner_id}: {total}")
    if total:
        simulation._income_per_second[owner_id] = total
    else:
        simulation._income_per_second.pop(owner_id, None)


def building_income_per_second(simulation: Simulation, owner_id: str) -> int:
    multiplier = simulation.ai_income_multiplier if owner_id != "player" else 1
    return simulation._income_per_second.get(owner_id, 0) * multiplier


def income_per_second(simulation: Simulation, owner_id: str) -> int:
    """Total advertised income per game second, including developer bonuses."""

    bonus = 2000 if owner_id == "player" and simulation.developer_mode else 0
    return building_income_per_second(simulation, owner_id) + bonus


def generate_income(simulation: Simulation) -> None:
    """Settle cached owner totals once per game second without scanning producers."""

    if simulation.tick % simulation.TICKS_PER_SECOND:
        return
    for owner_id in sorted(simulation._income_per_second):
        amount = building_income_per_second(simulation, owner_id)
        simulation.resources[owner_id] = simulation.resources.get(owner_id, 0) + amount
        simulation.events.record(
            simulation.tick,
            EventType.RESOURCE_CHANGED,
            owner_id,
            amount=amount,
            balance=simulation.resources[owner_id],
            reason="GENERATOR_INCOME",
        )


def generate_developer_income(simulation: Simulation) -> None:
    """Credit only the player after each full enabled second of simulation time."""

    if not simulation.developer_mode:
        return
    simulation.developer_income_ticks += 1
    if simulation.developer_income_ticks < simulation.TICKS_PER_SECOND:
        return
    simulation.developer_income_ticks = 0
    amount = 2000
    simulation.resources["player"] += amount
    simulation.events.record(
        simulation.tick,
        EventType.RESOURCE_CHANGED,
        "player",
        amount=amount,
        balance=simulation.resources["player"],
        reason="DEVELOPER_MODE_INCOME",
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
    kind = (EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK)[
        (random_value // max(1, len(candidates))) % 2
    ]
    start = random_value % len(candidates)
    cell = next(
        (
            candidates[(start + offset) % len(candidates)]
            for offset in range(len(candidates))
            if spawn_cell_available(
                simulation, candidates[(start + offset) % len(candidates)], kind
            )
        ),
        None,
    )
    if cell is None:
        return
    entity_id = allocate_entity_id(simulation, "enemy_tank")
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
