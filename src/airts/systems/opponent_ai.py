"""CPU-only economy planning and persistent squads through validated commands.

The force estimate is an aggregate short-horizon forecast, not a second simulation:
it considers current health, damage, range and approach time, without changing units.
Spatial queries are shared within a decision; expensive orders have fixed budgets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil, sqrt
from typing import TYPE_CHECKING

from airts.contracts.automations import (
    AutomationStatus,
    ConstructionParameters,
    ProductionParameters,
)
from airts.contracts.commands import (
    AttackCommand,
    Command,
    CommandResult,
    CreateConstructionCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
)
from airts.contracts.spatial import Point
from airts.navigation.spatial_index import SpatialIndex
from airts.systems.combat import _firing_approach_points
from airts.systems.construction import validate_building_placement
from airts.world.entities import Entity
from airts.world.map import footprint_cells
from airts.world.opponents import OpponentState, Squad, SquadPhase
from airts.world.units.catalog import EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation

DECISION_TICKS = 10
ECONOMY_TICKS = 20
MAX_ORDERS = 3
MAX_SQUAD_SIZE = 32
MAX_SQUAD_UPDATES = 2
MAX_TARGET_CANDIDATES = 4
MAX_PLACEMENT_CANDIDATES = 64
LOCAL_RADIUS = 18.0
ASSEMBLY_RADIUS = 7.0
RETREAT_TICKS = 80
ORDER_RETRY_TICKS = 50
FORECAST_ROUNDS = 12
TARGET_VALUES = {
    EntityKind.COMMAND_CENTER: 550.0,
    EntityKind.FACTORY: 500.0,
    EntityKind.BUILDER: 350.0,
    EntityKind.RESOURCE_GENERATOR: 320.0,
    EntityKind.MATTER_SYNTHESIZER: 300.0,
    EntityKind.TURRET: 180.0,
}


def _damage_per_exchange(unit: Entity) -> float:
    """Each aggregate exchange spans one decision interval of fixed game ticks."""
    if not unit.is_complete:
        return 0.0
    return (
        unit.kind.profile.attack_damage * DECISION_TICKS / unit.kind.profile.attack_interval_ticks
    )


def force_strength(units: Sequence[Entity]) -> float:
    """Health-weighted firepower; count alone cannot compare scouts and heavy tanks."""

    return sum(
        _damage_per_exchange(unit)
        * sqrt(max(0, unit.health))
        * (1 + unit.kind.profile.attack_range / 20)
        for unit in units
    )


def engagement_margin(own: Sequence[Entity], enemy: Sequence[Entity]) -> float:
    """Positive means favorable, negative means retreat; work is linear in force size.

    Clear advantages use a strength estimate. Close fights use twelve aggregate
    exchanges, discounting outgoing damage while either force approaches range.
    This deliberately does not model collision, focus fire or future reinforcements.
    """

    if not enemy:
        return 1.0
    if not own:
        return -1.0
    own_strength, enemy_strength = force_strength(own), force_strength(enemy)
    ratio = own_strength / max(1.0, enemy_strength)
    if ratio >= 1.5 or ratio <= 0.65:
        return ratio - 1
    own_center, enemy_center = _center(own), _center(enemy)
    distance = own_center.distance_to(enemy_center)

    def profile(units: Sequence[Entity], opposing: Sequence[Entity]) -> tuple[float, float, float]:
        health = sum(unit.health for unit in units)
        damage = sum(_damage_per_exchange(unit) for unit in units)
        incoming_speed = sum(unit.kind.profile.movement_speed or 0 for unit in opposing) / len(
            opposing
        )
        # Fixed guns wait for an approaching force rather than acquiring a speed or
        # pretending to chase it. Two fixed out-of-range forces never close the gap.
        delay = sum(
            (
                max(0.0, distance - unit.kind.profile.attack_range)
                / max(0.1, unit.kind.profile.movement_speed or incoming_speed)
            )
            if unit.is_movable or incoming_speed or distance <= unit.kind.profile.attack_range
            else float("inf")
            for unit in units
        ) / len(units)
        return float(health), float(damage), delay

    own_hp, own_damage, own_delay = profile(own, enemy)
    enemy_hp, enemy_damage, enemy_delay = profile(enemy, own)
    initial_own, initial_enemy = own_hp, enemy_hp
    for step in range(FORECAST_ROUNDS):
        incoming = enemy_damage * (enemy_hp / initial_enemy) if step >= enemy_delay else 0.0
        outgoing = own_damage * (own_hp / initial_own) if step >= own_delay else 0.0
        own_hp = max(0.0, own_hp - incoming)
        enemy_hp = max(0.0, enemy_hp - outgoing)
    # Retain the strength signal when neither group can reach firing range yet.
    return 0.5 * (ratio - 1) + own_hp / initial_own - enemy_hp / initial_enemy


@dataclass(slots=True)
class _Decision:
    simulation: Simulation
    owner_id: str
    memory: OpponentState
    owned: list[Entity]
    enemies: list[Entity]
    hostile_index: SpatialIndex
    home: Point
    rally: Point
    orders: int = 0

    def submit(self, command: Command) -> CommandResult | None:
        if self.orders >= MAX_ORDERS:
            return None
        self.orders += 1
        self.simulation._opponent_command_count += 1
        return self.simulation._execute_command(command, record=False)

    def threats(self, point: Point, radius: float = LOCAL_RADIUS) -> list[Entity]:
        return [self.simulation.entities[key] for key in self.hostile_index.nearby(point, radius)]

    def margin(self, own: Sequence[Entity], enemy: Sequence[Entity]) -> float:
        self.simulation._opponent_forecast_count += 1
        return engagement_margin(own, enemy)


def drive_opponents(simulation: Simulation) -> None:
    """Stagger owners and rotate squads; never wait for wall-clock work or a model."""

    slots = [slot for slot in simulation.game_map.match_slots if slot.controller == "ai"]
    due = [
        slot for index, slot in enumerate(slots) if simulation.tick % DECISION_TICKS == index * 3
    ]
    if not due:
        return
    entities = sorted(simulation.entities.values(), key=lambda entity: entity.entity_id)
    for slot in due:
        owned = [entity for entity in entities if entity.owner_id == slot.owner_id]
        if not owned:
            continue
        opening_ids = {entity.entity_id for entity in simulation.game_map.entities}
        anchor = min(
            owned,
            key=lambda entity: (
                entity.kind not in {EntityKind.COMMAND_CENTER, EntityKind.FACTORY},
                entity.entity_id not in opening_ids,
                entity.kind is not EntityKind.FACTORY,
                entity.entity_id,
            ),
        )
        home = anchor.selection_position
        enemies = [
            entity for entity in entities if simulation.are_hostile(slot.owner_id, entity.owner_id)
        ]
        armed_enemies = [
            entity
            for entity in enemies
            if entity.kind.profile.attack_damage > 0 and entity.is_complete
        ]
        index = SpatialIndex(
            {entity.entity_id: entity.selection_position for entity in armed_enemies}
        )
        memory = simulation.opponents[slot.owner_id]
        decision = _Decision(simulation, slot.owner_id, memory, owned, enemies, index, home, home)
        frontier = _frontier(decision)
        decision.rally = _safe_point(decision, frontier, 8) or home
        _repair_wounded(decision)
        _assign_squads(decision)
        threat = _base_threat(decision)
        _drive_reserve(decision, threat)
        if simulation.tick % ECONOMY_TICKS < DECISION_TICKS:
            _plan_construction(decision)
            _plan_production(decision)
        squads = memory.squads
        offset = (simulation.tick // DECISION_TICKS) % max(1, len(squads))
        for squad in (squads[offset:] + squads[:offset])[:MAX_SQUAD_UPDATES]:
            _drive_squad(decision, squad, threat)


def _center(units: Sequence[Entity]) -> Point:
    return Point(
        sum(unit.selection_position.x for unit in units) / len(units),
        sum(unit.selection_position.y for unit in units) / len(units),
    )


def _frontier(decision: _Decision) -> Point:
    anchors = [
        entity.selection_position
        for entity in decision.owned
        if not entity.is_movable
        and entity.is_complete
        and not decision.threats(entity.selection_position, 12)
    ]
    anchors.extend(
        job.position
        for job in _pending_construction(decision)
        if job.building_kind is EntityKind.RESOURCE_GENERATOR
        and not decision.threats(job.position, 12)
    )
    if not anchors or not decision.enemies:
        return decision.home
    enemy = min(
        decision.enemies,
        key=lambda unit: (unit.selection_position.distance_to(decision.home), unit.entity_id),
    ).selection_position
    return min(anchors, key=lambda point: (point.distance_to(enemy), point.x, point.y))


def _safe_point(decision: _Decision, desired: Point, radius: int) -> Point | None:
    simulation = decision.simulation
    blocked = simulation._building_cells()
    candidates = (
        Point(float(int(desired.x) + dx) + 0.5, float(int(desired.y) + dy) + 0.5)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
    )
    for point in sorted(candidates, key=lambda item: (item.distance_to(desired), item.x, item.y)):
        if (
            simulation.game_map.is_passable(point)
            and simulation.game_map.cell_for(point) not in blocked
        ):
            return point
    return None


def _members(decision: _Decision, squad: Squad) -> list[Entity]:
    return [decision.simulation.entities[key] for key in squad.entity_ids]


def _repair_wounded(decision: _Decision) -> None:
    simulation = decision.simulation
    wounded = tuple(
        entity.entity_id
        for entity in decision.owned
        if entity.is_movable
        and entity.health / entity.kind.profile.max_health <= 0.30
        and entity.entity_id not in simulation.assignments
    )[:MAX_SQUAD_SIZE]
    if wounded and any(not entity.is_movable and entity.is_complete for entity in decision.owned):
        decision.submit(
            CreateRepairAndReturnCommand(wounded, owner_id=decision.owner_id, title="AI repair")
        )


def _assign_squads(decision: _Decision) -> None:
    simulation, memory = decision.simulation, decision.memory
    army = {
        entity.entity_id: entity
        for entity in decision.owned
        if entity.is_movable
        and entity.kind.profile.attack_damage > 0
        and entity.entity_id not in simulation.assignments
    }
    assigned: set[str] = set()
    for squad in (memory.reserve, *memory.squads):
        squad.entity_ids = [key for key in squad.entity_ids if key in army]
        assigned.update(squad.entity_ids)
    memory.squads = [squad for squad in memory.squads if squad.entity_ids]
    available = sorted(
        (unit for key, unit in army.items() if key not in assigned),
        key=lambda unit: (unit.position.distance_to(decision.rally), unit.entity_id),
    )
    reserve_size = min(4, len(army) // 4)
    if not any(not entity.is_movable for entity in decision.owned):
        reserve_size = 0
    while len(memory.reserve.entity_ids) > reserve_size:
        available.append(army[memory.reserve.entity_ids.pop()])
    while available and len(memory.reserve.entity_ids) < reserve_size:
        memory.reserve.entity_ids.append(available.pop(0).entity_id)
    for unit in available:
        gathering_squad = next(
            (
                squad
                for squad in memory.squads
                if squad.phase
                in {SquadPhase.GATHERING, SquadPhase.RETREATING, SquadPhase.DEFENDING}
                and len(squad.entity_ids) < MAX_SQUAD_SIZE
            ),
            None,
        )
        if gathering_squad is None:
            gathering_squad = Squad(phase_tick=simulation.tick)
            memory.squads.append(gathering_squad)
        gathering_squad.entity_ids.append(unit.entity_id)


def _base_threat(decision: _Decision) -> Entity | None:
    assets = [
        entity
        for entity in decision.owned
        if not entity.is_movable or entity.kind is EntityKind.BUILDER
    ]
    threats: dict[str, Entity] = {}
    for asset in assets:
        for enemy in decision.threats(asset.selection_position):
            threats[enemy.entity_id] = enemy
    return min(
        threats.values(),
        key=lambda entity: (entity.position.distance_to(decision.home), entity.entity_id),
        default=None,
    )


def _set_phase(decision: _Decision, squad: Squad, phase: SquadPhase) -> None:
    if squad.phase is not phase:
        squad.phase = phase
        squad.phase_tick = decision.simulation.tick
        if phase is SquadPhase.RETREATING:
            squad.retry_tick = 0
            squad.destination = None


def _retreat_point(decision: _Decision, enemies: list[Entity]) -> Point:
    if not enemies:
        return decision.rally
    center = _center(enemies)
    dx, dy = decision.home.x - center.x, decision.home.y - center.y
    length = max(1.0, sqrt(dx * dx + dy * dy))
    desired = Point(
        min(decision.simulation.game_map.width - 1.5, max(1.5, decision.home.x + dx / length * 12)),
        min(
            decision.simulation.game_map.height - 1.5, max(1.5, decision.home.y + dy / length * 12)
        ),
    )
    return _safe_point(decision, desired, 4) or decision.rally


def _move_squad(decision: _Decision, squad: Squad, destination: Point) -> None:
    tick = decision.simulation.tick
    units = _members(decision, squad)
    if not units or tick < squad.retry_tick:
        return
    if squad.destination == destination and any(unit.move_target is not None for unit in units):
        return
    if all(
        unit.position.distance_to(destination) <= ASSEMBLY_RADIUS and not unit.pursue_target
        for unit in units
    ):
        return
    result = decision.submit(
        MoveCommand(
            tuple(squad.entity_ids), destination, owner_id=decision.owner_id, gathering=True
        )
    )
    if result is None:
        return
    squad.last_order_tick = tick
    squad.retry_tick = tick + ORDER_RETRY_TICKS
    squad.last_failure = None if result.accepted else result.reason
    if result.accepted:
        squad.destination = destination
        squad.target_id = None


def _attack_squad(decision: _Decision, squad: Squad, target: Entity) -> None:
    if squad.last_failure is not None and decision.simulation.tick < squad.retry_tick:
        return
    units = _members(decision, squad)
    dispatch = tuple(
        unit.entity_id
        for unit in units
        if not unit.pursue_target or unit.attack_target_id != target.entity_id
    )
    if not dispatch:
        return
    result = decision.submit(AttackCommand(dispatch, target.entity_id, owner_id=decision.owner_id))
    if result is not None:
        squad.last_order_tick = decision.simulation.tick
        squad.last_failure = None if result.accepted else result.reason
        squad.retry_tick = 0 if result.accepted else decision.simulation.tick + ORDER_RETRY_TICKS
        if result.accepted:
            squad.target_id = target.entity_id
            squad.destination = None


def _drive_reserve(decision: _Decision, threat: Entity | None) -> None:
    squad = decision.memory.reserve
    units = _members(decision, squad)
    if not units:
        return
    if threat is not None and decision.margin(units, decision.threats(threat.position)) >= -0.1:
        _set_phase(decision, squad, SquadPhase.DEFENDING)
        _attack_squad(decision, squad, threat)
    else:
        _set_phase(decision, squad, SquadPhase.GATHERING)
        _move_squad(decision, squad, _safe_point(decision, decision.home, 8) or decision.home)


def _choose_target(decision: _Decision, units: list[Entity]) -> Entity | None:
    center = _center(units)
    ranked = sorted(
        decision.enemies,
        key=lambda enemy: (
            -(
                TARGET_VALUES.get(enemy.kind, 100.0)
                / (20 + center.distance_to(enemy.selection_position))
            ),
            enemy.entity_id,
        ),
    )
    nearest = sorted(
        decision.enemies,
        key=lambda enemy: (center.distance_to(enemy.selection_position), enemy.entity_id),
    )
    # Alternate strategic and nearby targets so guarded high-value buildings do
    # not hide vulnerable raiders or isolated remnants behind the candidate cap.
    candidates: list[Entity] = []
    seen: set[str] = set()
    for valuable, close in zip(ranked, nearest, strict=True):
        for enemy in (valuable, close):
            if enemy.entity_id not in seen and len(candidates) < MAX_TARGET_CANDIDATES:
                candidates.append(enemy)
                seen.add(enemy.entity_id)
        if len(candidates) == MAX_TARGET_CANDIDATES:
            break
    scored: list[tuple[float, str, Entity]] = []
    for enemy in candidates:
        decision.simulation._opponent_candidate_count += 1
        if not decision.simulation._routes.can_reach_any(
            units[0].position,
            _firing_approach_points(decision.simulation, units[0], enemy),
            decision.simulation._building_cells(),
        ):
            continue
        guards = decision.threats(enemy.selection_position)
        margin = decision.margin(units, guards)
        if margin < 0.15:
            continue
        distance = center.distance_to(enemy.selection_position)
        committed = sum(
            len(squad.entity_ids)
            for squad in decision.memory.squads
            if squad.target_id == enemy.entity_id
            and not any(unit.entity_id in squad.entity_ids for unit in units)
        )
        score = (
            TARGET_VALUES.get(enemy.kind, 100.0)
            / (20 + distance)
            * min(2.0, 1 + margin)
            / (1 + committed / MAX_SQUAD_SIZE)
        )
        scored.append((score, enemy.entity_id, enemy))
    return max(scored, key=lambda item: (item[0], item[1]))[2] if scored else None


def _pressure_point(decision: _Decision, units: list[Entity]) -> Point:
    center = _center(units)
    target = min(
        decision.enemies,
        key=lambda enemy: (center.distance_to(enemy.selection_position), enemy.entity_id),
        default=None,
    )
    if target is None:
        return decision.rally
    dx, dy = target.selection_position.x - center.x, target.selection_position.y - center.y
    distance = sqrt(dx * dx + dy * dy)
    step = min(24.0, max(0.0, distance - 2 * LOCAL_RADIUS))
    if step == 0:
        return _safe_point(decision, center, 4) or decision.rally
    desired = Point(center.x + dx / distance * step, center.y + dy / distance * step)
    point = _safe_point(decision, desired, 4)
    if (
        point is not None
        and not decision.threats(point)
        and decision.simulation._routes.can_reach_any(
            units[0].position, (point,), decision.simulation._building_cells()
        )
    ):
        return point
    return decision.rally


def _drive_squad(decision: _Decision, squad: Squad, threat: Entity | None) -> None:
    units = _members(decision, squad)
    center = _center(units)
    local = decision.threats(center)
    margin = decision.margin(units, local)
    if local and margin < -0.2:
        _set_phase(decision, squad, SquadPhase.RETREATING)
        _move_squad(decision, squad, squad.destination or _retreat_point(decision, local))
        return
    if squad.phase is SquadPhase.RETREATING:
        if decision.simulation.tick - squad.phase_tick < RETREAT_TICKS:
            _move_squad(decision, squad, squad.destination or decision.rally)
            return
        _set_phase(decision, squad, SquadPhase.GATHERING)
    if threat is not None:
        reserve = _members(decision, decision.memory.reserve)
        defenders = decision.threats(threat.position)
        responding = min(
            decision.memory.squads,
            key=lambda item: (
                _center(_members(decision, item)).distance_to(threat.position),
                item.entity_ids[0],
            ),
        )
        if decision.margin(reserve, defenders) < 0 and squad is responding:
            if decision.margin(units, defenders) >= -0.1:
                _set_phase(decision, squad, SquadPhase.DEFENDING)
                _attack_squad(decision, squad, threat)
                return
    elif squad.phase is SquadPhase.DEFENDING:
        _set_phase(decision, squad, SquadPhase.GATHERING)
    if squad.phase is SquadPhase.GATHERING:
        assembly = squad.destination or decision.rally
        assembled = sum(unit.position.distance_to(assembly) <= ASSEMBLY_RADIUS for unit in units)
        if assembled < ceil(len(units) * 0.75):
            _move_squad(decision, squad, assembly)
            return
        target = _choose_target(decision, units)
        if target is None:
            _move_squad(decision, squad, _pressure_point(decision, units))
            return
        squad.target_id = target.entity_id
        _set_phase(decision, squad, SquadPhase.ADVANCING)
    target = decision.simulation.entities.get(squad.target_id or "")
    if target is None:
        target = _choose_target(decision, units)
        if target is None:
            _set_phase(decision, squad, SquadPhase.GATHERING)
            _move_squad(decision, squad, _pressure_point(decision, units))
            return
        squad.target_id = target.entity_id
        _set_phase(decision, squad, SquadPhase.ADVANCING)
    if squad.phase is SquadPhase.ADVANCING:
        # An attack order uses the existing budgeted pursuit routing. Keep one
        # target until contact; repeated decisions must not clear ongoing paths.
        _attack_squad(decision, squad, target)
        if center.distance_to(target.selection_position) <= LOCAL_RADIUS:
            _set_phase(decision, squad, SquadPhase.ATTACKING)
    elif squad.phase is SquadPhase.ATTACKING:
        nearby_target = min(
            local,
            key=lambda enemy: (
                enemy.health / max(1, _damage_per_exchange(enemy)),
                enemy.entity_id,
            ),
            default=target,
        )
        _attack_squad(decision, squad, nearby_target)


def _pending_construction(decision: _Decision) -> list[ConstructionParameters]:
    return [
        automation.parameters
        for automation in decision.simulation.live_automations
        if automation.owner_id == decision.owner_id
        and isinstance(automation.parameters, ConstructionParameters)
    ]


def _plan_construction(decision: _Decision) -> None:
    simulation = decision.simulation
    pending = _pending_construction(decision)
    busy = {key for job in pending for key in job.builder_ids}
    builders = [
        entity
        for entity in decision.owned
        if entity.kind is EntityKind.BUILDER
        and entity.entity_id not in busy
        and entity.entity_id not in simulation.assignments
    ]
    if not builders or len(pending) >= 2:
        return
    counts = {
        kind: sum(entity.kind is kind for entity in decision.owned)
        + sum(job.building_kind is kind and job.site_entity_id is None for job in pending)
        for kind in EntityKind
    }
    funds = simulation.resources.get(decision.owner_id, 0) - sum(
        job.building_kind.profile.construction_cost for job in pending if not job.cost_paid
    )
    income = sum(
        entity.kind.profile.income_per_second for entity in decision.owned if entity.is_complete
    )
    frontier = _frontier(decision)
    exposed = [
        entity.selection_position
        for entity in decision.owned
        if entity.kind is EntityKind.RESOURCE_GENERATOR
        and entity.is_complete
        and entity.selection_position.distance_to(decision.home) > LOCAL_RADIUS
    ]
    if _base_threat(decision) is not None:
        exposed.append(decision.home)
    defenses = [
        entity.selection_position for entity in decision.owned if entity.kind is EntityKind.TURRET
    ]
    defenses.extend(job.position for job in pending if job.building_kind is EntityKind.TURRET)
    exposed = [point for point in exposed if all(point.distance_to(gun) > 14 for gun in defenses)]
    options: list[tuple[EntityKind, Point]] = []
    if not counts[EntityKind.FACTORY] and (
        counts[EntityKind.RESOURCE_GENERATOR]
        or counts[EntityKind.MATTER_SYNTHESIZER]
        or funds >= EntityKind.FACTORY.profile.construction_cost
    ):
        options.append((EntityKind.FACTORY, decision.home))
    if exposed and funds >= 350:
        point = min(exposed, key=lambda point: (point.distance_to(frontier), point.x, point.y))
        enemy = min(
            decision.enemies,
            key=lambda unit: unit.selection_position.distance_to(point),
            default=None,
        )
        if enemy is not None:
            dx, dy = enemy.selection_position.x - point.x, enemy.selection_position.y - point.y
            length = max(1.0, sqrt(dx * dx + dy * dy))
            point = Point(point.x + 6 * dx / length, point.y + 6 * dy / length)
        options.append((EntityKind.TURRET, point))
    if counts[EntityKind.FACTORY] < min(6, 1 + income // 40) and funds >= 550:
        options.append((EntityKind.FACTORY, frontier))
    if (
        not counts[EntityKind.REPAIR_HUB]
        and sum(
            entity.is_movable and entity.health < entity.kind.profile.max_health
            for entity in decision.owned
        )
        >= 3
        and funds >= 350
    ):
        options.append((EntityKind.REPAIR_HUB, frontier))
    options.append((EntityKind.RESOURCE_GENERATOR, frontier))
    if funds >= 800 and counts[EntityKind.MATTER_SYNTHESIZER] < max(
        1, ceil(counts[EntityKind.FACTORY] / 2)
    ):
        options.append((EntityKind.MATTER_SYNTHESIZER, decision.home))
    for kind, center in options:
        if funds < kind.profile.construction_cost and any(not job.cost_paid for job in pending):
            continue
        site = _construction_site(decision, kind, center, builders, pending)
        if site is None:
            continue
        builder, point = site
        decision.submit(
            CreateConstructionCommand(
                builder.entity_id, kind, point, owner_id=decision.owner_id, title="AI expansion"
            )
        )
        return


def _construction_site(
    decision: _Decision,
    kind: EntityKind,
    center: Point,
    builders: list[Entity],
    pending: list[ConstructionParameters],
) -> tuple[Entity, Point] | None:
    simulation = decision.simulation
    claimed = {
        job.position
        for automation in simulation.live_automations
        if isinstance(job := automation.parameters, ConstructionParameters)
    }
    reserved = set().union(*(footprint_cells(job.building_kind, job.position) for job in pending))
    if kind is EntityKind.RESOURCE_GENERATOR:
        candidates = (
            list(simulation.game_map.resource_sites or ())
            if simulation.game_map.resource_sites is not None
            else [
                Point(float(int(center.x) + dx), float(int(center.y) + dy))
                for dy in range(-10, 11)
                for dx in range(-10, 11)
            ]
        )
    else:
        # Spread the bounded search across the outpost instead of exhausting it
        # on origins whose large footprints all overlap the central extractor.
        candidates = [
            Point(float(int(center.x) + dx), float(int(center.y) + dy))
            for dy in range(-10, 11, 2)
            for dx in range(-10, 11, 2)
        ]
    candidates.sort(key=lambda point: (point.distance_to(center), point.x, point.y))
    for point in candidates[:MAX_PLACEMENT_CANDIDATES]:
        if (
            point in claimed
            or reserved & footprint_cells(kind, point)
            or decision.threats(point, 12)
        ):
            continue
        if any(
            enemy.kind in {EntityKind.FACTORY, EntityKind.COMMAND_CENTER}
            and enemy.selection_position.distance_to(point) < 24
            for enemy in decision.enemies
        ):
            continue
        if (
            validate_building_placement(simulation, kind, point, owner_id=decision.owner_id)
            is not None
        ):
            continue
        blocked = simulation._building_cells() | footprint_cells(kind, point)
        width, height = kind.profile.footprint
        approaches = tuple(
            Point(float(x) + 0.5, float(y) + 0.5)
            for y in range(int(point.y) - 1, int(point.y) + height + 1)
            for x in range(int(point.x) - 1, int(point.x) + width + 1)
            if (x, y) not in blocked
        )
        for builder in sorted(
            builders, key=lambda unit: (unit.position.distance_to(point), unit.entity_id)
        ):
            if simulation._routes.can_reach_any(builder.position, approaches, blocked):
                return builder, point
    return None


def _plan_production(decision: _Decision) -> None:
    simulation, memory = decision.simulation, decision.memory
    reserve = sum(
        job.building_kind.profile.construction_cost
        for job in _pending_construction(decision)
        if not job.cost_paid
    )
    memory.budget_paused_ids = [
        key
        for key in memory.budget_paused_ids
        if key in simulation.automations and not simulation.automations[key].status.terminal
    ]
    counts = {kind: sum(entity.kind is kind for entity in decision.owned) for kind in EntityKind}
    sources = [
        entity for entity in decision.owned if entity.kind.production_kinds and entity.is_complete
    ]
    for factory in sources:
        for job in simulation.production_queue(factory.entity_id):
            parameters = job.parameters
            assert isinstance(parameters, ProductionParameters)
            if not parameters.continuous:
                counts[parameters.unit_kind] += parameters.target_count - parameters.produced_count
    offset = (simulation.tick // ECONOMY_TICKS) % max(1, len(sources))
    for factory in sources[offset:] + sources[:offset]:
        jobs = simulation.production_queue(factory.entity_id)
        finite = any(
            isinstance(job.parameters, ProductionParameters) and not job.parameters.continuous
            for job in jobs
        )
        loop = simulation.continuous_production(factory.entity_id)
        if (
            loop is not None
            and loop.status is AutomationStatus.PAUSED
            and loop.automation_id not in memory.budget_paused_ids
        ):
            continue
        desired_builders = min(4, 1 + counts[EntityKind.RESOURCE_GENERATOR] // 2)
        kind: EntityKind | None = None
        if not finite:
            if counts[EntityKind.BUILDER] < desired_builders:
                kind = EntityKind.BUILDER
            elif factory.kind is EntityKind.COMMAND_CENTER:
                continue
            elif counts[EntityKind.SCOUT] < max(1, ceil(counts[EntityKind.LIGHT_TANK] / 12)):
                kind = EntityKind.SCOUT
            elif counts[EntityKind.HEAVY_TANK] < ceil(
                counts[EntityKind.LIGHT_TANK] / 3
            ) and not any(enemy.kind is EntityKind.TURRET for enemy in decision.enemies):
                kind = EntityKind.HEAVY_TANK
            elif counts[EntityKind.LIGHT_TANK] >= 4 and counts[EntityKind.ARTILLERY] < ceil(
                counts[EntityKind.LIGHT_TANK]
                / (4 if any(enemy.kind is EntityKind.TURRET for enemy in decision.enemies) else 8)
            ):
                kind = EntityKind.ARTILLERY
            elif counts[EntityKind.HEAVY_TANK] < ceil(counts[EntityKind.LIGHT_TANK] / 3):
                kind = EntityKind.HEAVY_TANK
        funds = simulation.resources.get(decision.owner_id, 0)
        # Without saving for a heavy tank, a cheap continuous loop can spend
        # every income payment before the more expensive unit becomes affordable.
        saving = reserve + (kind.profile.production_cost if kind is not None else 0)
        if loop is not None:
            key = loop.automation_id
            if key in memory.budget_paused_ids and loop.status is not AutomationStatus.PAUSED:
                memory.budget_paused_ids.remove(key)
            if (
                saving
                and funds < saving + EntityKind.LIGHT_TANK.profile.production_cost
                and loop.status is not AutomationStatus.PAUSED
            ):
                result = decision.submit(PauseAutomationCommand(key, owner_id=decision.owner_id))
                if result is not None and result.accepted:
                    memory.budget_paused_ids.append(key)
            elif key in memory.budget_paused_ids and (
                not saving or funds >= saving + EntityKind.LIGHT_TANK.profile.production_cost
            ):
                result = decision.submit(ResumeAutomationCommand(key, owner_id=decision.owner_id))
                if result is not None and result.accepted:
                    memory.budget_paused_ids.remove(key)
        elif (
            factory.kind is EntityKind.FACTORY
            and counts[EntityKind.BUILDER]
            and funds >= saving + EntityKind.LIGHT_TANK.profile.production_cost
        ):
            result = decision.submit(
                CreateProductionCommand(
                    factory.entity_id,
                    EntityKind.LIGHT_TANK,
                    1,
                    continuous=True,
                    owner_id=decision.owner_id,
                    title="AI production",
                )
            )
            if result is not None and result.accepted:
                loop = simulation.automations[result.automation_id or ""]
        if kind is None or funds - reserve < kind.profile.production_cost:
            continue
        result = decision.submit(
            CreateProductionCommand(
                factory.entity_id,
                kind,
                1,
                owner_id=decision.owner_id,
                title="AI production",
            )
        )
        if result is not None and result.accepted:
            counts[kind] += 1
            if loop is not None and loop.automation_id in memory.budget_paused_ids:
                memory.budget_paused_ids.remove(loop.automation_id)
