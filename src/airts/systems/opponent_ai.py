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
    UpgradeEntityCommand,
)
from airts.contracts.spatial import Point
from airts.navigation.spatial_index import SpatialIndex
from airts.systems.combat_geometry import firing_approach_points
from airts.systems.construction import validate_building_placement
from airts.systems.economy import building_income_per_second
from airts.world.entities import Entity
from airts.world.map import Cell, footprint_cells
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
ECONOMY_HORIZON_SECONDS = 20
MAX_SUPPORT_UNITS = 3 * MAX_SQUAD_SIZE
PRODUCTION_CLEARANCE = 3.0
ATTACK_WAVE_SIZE = 20
REINFORCEMENT_SIZE = 6
ARMY_RADIUS = 14.0
GROWTH_SHARE = 0.4
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
    production_lanes: frozenset[Cell] = frozenset()

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
        decision.production_lanes = _production_lanes(decision)
        frontier = _frontier(decision)
        decision.rally = _staging_point(decision, frontier)
        threat = _base_threat(decision)
        if threat is None:
            _repair_wounded(decision)
        _assign_squads(decision)
        _clear_production_exits(decision)
        _drive_reserve(decision, threat)
        _drive_army(decision, threat)
        if simulation.tick % ECONOMY_TICKS < DECISION_TICKS:
            _refresh_growth_credit(decision)
            _plan_growth(decision, threatened=threat is not None)
            _plan_production(decision, threatened=threat is not None)


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
    for job in _pending_construction(decision):
        width, height = job.building_kind.profile.footprint
        blocked = blocked | frozenset(
            (x, y)
            for x in range(int(job.position.x) - 3, int(job.position.x) + width + 3)
            for y in range(int(job.position.y) - 3, int(job.position.y) + height + 3)
        )
    candidates = (
        Point(float(int(desired.x) + dx) + 0.5, float(int(desired.y) + dy) + 0.5)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
    )
    for point in sorted(candidates, key=lambda item: (item.distance_to(desired), item.x, item.y)):
        if (
            simulation.game_map.is_passable(point)
            and simulation.game_map.cell_for(point) not in blocked
            and not _near_producer(decision, point)
        ):
            return point
    return None


def _production_lanes(decision: _Decision) -> frozenset[Cell]:
    cells: set[Cell] = set()
    sources = [
        (entity.kind, entity.position) for entity in decision.owned if entity.kind.production_kinds
    ]
    sources.extend(
        (job.building_kind, job.position)
        for job in _pending_construction(decision)
        if job.building_kind.production_kinds
    )
    for kind, position in sources:
        width, height = kind.profile.footprint
        cells.update(
            (x, y)
            for y in range(int(position.y) - 1, int(position.y) + height + 1)
            for x in range(int(position.x) - 1, int(position.x) + width + 1)
        )
    return frozenset(cells)


def _near_producer(decision: _Decision, point: Point) -> bool:
    for source in decision.owned:
        if not source.kind.production_kinds:
            continue
        width, height = source.kind.profile.footprint
        dx = max(source.position.x - point.x, 0.0, point.x - source.position.x - width)
        dy = max(source.position.y - point.y, 0.0, point.y - source.position.y - height)
        if dx * dx + dy * dy < PRODUCTION_CLEARANCE * PRODUCTION_CLEARANCE:
            return True
    return False


def _staging_point(decision: _Decision, anchor: Point) -> Point:
    enemy = min(
        decision.enemies,
        key=lambda unit: (anchor.distance_to(unit.selection_position), unit.entity_id),
        default=None,
    )
    if enemy is None:
        desired = anchor
    else:
        dx, dy = enemy.selection_position.x - anchor.x, enemy.selection_position.y - anchor.y
        length = max(1.0, sqrt(dx * dx + dy * dy))
        direction = -1 if length < 2 * LOCAL_RADIUS else 1
        desired = Point(
            anchor.x + direction * 12 * dx / length, anchor.y + direction * 12 * dy / length
        )
    point = _safe_point(decision, desired, 10)
    return point or _safe_point(decision, anchor, 12) or anchor


def _clear_production_exits(decision: _Decision) -> None:
    blocked = any(
        job.reason_code == "SPAWN_BLOCKED"
        for source in decision.owned
        if source.kind.production_kinds
        for job in decision.simulation.production_queue(source.entity_id)
    )
    clearance = set(decision.production_lanes) if blocked else set()
    for job in decision.simulation.live_automations:
        if (
            job.owner_id == decision.owner_id
            and job.reason_code == "SITE_OCCUPIED"
            and isinstance(job.parameters, ConstructionParameters)
        ):
            clearance.update(footprint_cells(job.parameters.building_kind, job.parameters.position))
    if not clearance:
        return
    units = tuple(
        unit.entity_id
        for unit in decision.owned
        if unit.is_movable
        and unit.entity_id not in decision.simulation.assignments
        and decision.simulation.game_map.cell_for(unit.position) in clearance
        and not unit.pursue_target
        and (
            unit.move_target is None
            or decision.simulation.game_map.cell_for(unit.move_target) in clearance
        )
    )[:MAX_SQUAD_SIZE]
    if units:
        decision.submit(
            MoveCommand(units, decision.rally, owner_id=decision.owner_id, gathering=True)
        )


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
    waiting = [squad for squad in memory.squads if squad.phase is SquadPhase.GATHERING]
    for index, squad in enumerate(waiting):
        if not squad.entity_ids:
            continue
        for other in waiting[index + 1 :]:
            if not other.entity_ids or len(squad.entity_ids) >= MAX_SQUAD_SIZE:
                continue
            if (
                _center(_members(decision, squad)).distance_to(_center(_members(decision, other)))
                > 2 * ARMY_RADIUS
            ):
                continue
            count = min(MAX_SQUAD_SIZE - len(squad.entity_ids), len(other.entity_ids))
            squad.entity_ids.extend(other.entity_ids[:count])
            del other.entity_ids[:count]
    memory.squads = [squad for squad in memory.squads if squad.entity_ids]
    available = sorted(
        (unit for key, unit in army.items() if key not in assigned),
        key=lambda unit: (unit.position.distance_to(decision.rally), unit.entity_id),
    )
    reserve_size = min(4, len(army) // 16) if len(army) >= 32 else 0
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
                if squad.phase in {SquadPhase.GATHERING, SquadPhase.RETREATING}
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
        entity for entity in decision.owned if not entity.is_movable or entity.profile.can_build
    ]
    threats: dict[str, Entity] = {}
    under_fire: dict[str, int] = {}
    for asset in assets:
        for enemy in decision.threats(asset.selection_position):
            threats[enemy.entity_id] = enemy
        if (
            asset.last_attacker_id is not None
            and asset.last_attacked_tick is not None
            and decision.simulation.tick - asset.last_attacked_tick <= 30
        ):
            priority = (
                0 if asset.kind.production_kinds else 1 if asset.profile.income_per_second else 2
            )
            under_fire[asset.last_attacker_id] = min(
                under_fire.get(asset.last_attacker_id, 3), priority
            )
    return min(
        threats.values(),
        key=lambda entity: (
            under_fire.get(entity.entity_id, 3),
            entity.position.distance_to(decision.home),
            entity.entity_id,
        ),
        default=None,
    )


def _set_phase(decision: _Decision, squad: Squad, phase: SquadPhase) -> None:
    if squad.phase is not phase:
        squad.phase = phase
        squad.phase_tick = decision.simulation.tick
        if phase is SquadPhase.RETREATING:
            squad.retry_tick = 0
            squad.destination = None


def _move_squad(decision: _Decision, squad: Squad, destination: Point) -> None:
    tick = decision.simulation.tick
    units = _members(decision, squad)
    work_sites = set().union(
        *(
            footprint_cells(job.building_kind, job.position)
            for job in _pending_construction(decision)
        )
    )
    if _near_producer(decision, destination):
        destination = _safe_point(decision, destination, 12) or decision.rally
    if not units or squad.last_failure is not None and tick < squad.retry_tick:
        return
    if squad.destination == destination and all(
        unit.move_target is not None or unit.position.distance_to(destination) <= ASSEMBLY_RADIUS
        for unit in units
    ):
        return
    if all(
        unit.position.distance_to(destination) <= ASSEMBLY_RADIUS
        and not unit.pursue_target
        and (
            unit.move_target is None or unit.move_target.distance_to(destination) <= ASSEMBLY_RADIUS
        )
        and decision.simulation.game_map.cell_for(unit.position) not in decision.production_lanes
        and decision.simulation.game_map.cell_for(unit.position) not in work_sites
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


def _defense_order(decision: _Decision, threat: Entity) -> tuple[Point, bool]:
    assets = [unit for unit in decision.owned if unit.is_complete and not unit.is_movable]
    anchor = min(
        assets, key=lambda unit: unit.selection_position.distance_to(threat.position), default=None
    )
    if anchor is None:
        return threat.position, True
    forts = [
        unit
        for unit in assets
        if unit.profile.attack_damage > 0
        and unit.selection_position.distance_to(anchor.selection_position) <= LOCAL_RADIUS
    ]
    fort = min(
        forts, key=lambda unit: unit.selection_position.distance_to(threat.position), default=None
    )
    point = _safe_point(decision, (fort or anchor).selection_position, 8) or decision.rally
    raiders = {unit.entity_id for unit in decision.threats(threat.position)}
    under_fire = any(
        unit.last_attacker_id in raiders
        and unit.last_attacked_tick is not None
        and decision.simulation.tick - unit.last_attacked_tick <= 20
        for unit in decision.owned
    )
    attack = (
        under_fire
        or anchor.selection_position.distance_to(threat.position) <= threat.profile.attack_range + 3
        or fort is not None
        and fort.selection_position.distance_to(threat.position) <= fort.profile.attack_range + 0.5
    )
    return point, attack


def _drive_reserve(decision: _Decision, threat: Entity | None) -> None:
    squad = decision.memory.reserve
    units = _members(decision, squad)
    if not units:
        return
    if threat is not None:
        _set_phase(decision, squad, SquadPhase.DEFENDING)
        point, attack = _defense_order(decision, threat)
        if attack:
            _attack_squad(decision, squad, threat)
        else:
            _move_squad(decision, squad, point)
    else:
        _set_phase(decision, squad, SquadPhase.GATHERING)
        _move_squad(decision, squad, _staging_point(decision, decision.home))


def _choose_target(decision: _Decision, units: list[Entity]) -> Entity | None:
    center = _center(units)
    ranked = sorted(
        decision.enemies,
        key=lambda enemy: (
            -TARGET_VALUES.get(enemy.kind, 100.0)
            / (20 + center.distance_to(enemy.selection_position)),
            enemy.entity_id,
        ),
    )
    nearest = sorted(
        decision.enemies,
        key=lambda enemy: (center.distance_to(enemy.selection_position), enemy.entity_id),
    )
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
            firing_approach_points(decision.simulation, units[0], enemy),
            decision.simulation._building_cells(),
        ):
            continue
        margin = decision.margin(units, decision.threats(enemy.selection_position))
        if margin < -0.1:
            continue
        score = (
            TARGET_VALUES.get(enemy.kind, 100.0)
            / (20 + center.distance_to(enemy.selection_position))
            * min(2.0, 1 + margin)
        )
        scored.append((score, enemy.entity_id, enemy))
    return max(scored, key=lambda item: (item[0], item[1]))[2] if scored else None


def _order_groups(
    decision: _Decision,
    groups: list[Squad],
    phase: SquadPhase,
    point: Point,
    target: Entity | None,
    ceiling: int,
    *,
    attack: bool = False,
) -> None:
    for squad in groups:
        if decision.orders >= ceiling:
            return
        if (
            phase is SquadPhase.DEFENDING
            and target is not None
            and squad.target_id != target.entity_id
        ):
            squad.last_failure = None
            squad.retry_tick = 0
        _set_phase(decision, squad, phase)
        if attack and target is not None:
            _attack_squad(decision, squad, target)
        else:
            _move_squad(decision, squad, point)
        # Movement keeps the shared strategic objective, not a new per-squad raid.
        squad.target_id = None if target is None else target.entity_id


def _assembly_point(decision: _Decision, squad: Squad) -> Point:
    center = _center(_members(decision, squad))
    sources = [unit for unit in decision.owned if unit.kind.production_kinds and unit.is_complete]
    if not sources:
        return decision.rally
    source = min(
        sources, key=lambda unit: (center.distance_to(unit.selection_position), unit.entity_id)
    )
    return _staging_point(decision, source.selection_position)


def _drive_army(decision: _Decision, threat: Entity | None) -> None:
    groups = decision.memory.squads
    if not groups:
        return
    active = [
        squad for squad in groups if squad.phase in {SquadPhase.ADVANCING, SquadPhase.ATTACKING}
    ]
    leader = active[0] if active else groups[0]
    center = _center(_members(decision, leader))
    ordered = sorted(
        groups,
        key=lambda squad: (
            center.distance_to(_center(_members(decision, squad))),
            squad.entity_ids[0],
        ),
    )
    force = sorted(
        (
            unit
            for squad in groups
            for unit in _members(decision, squad)
            if unit.position.distance_to(center) <= 2 * ARMY_RADIUS
        ),
        key=lambda unit: (unit.position.distance_to(center), unit.entity_id),
    )[:MAX_SUPPORT_UNITS]
    if not force:
        force = _members(decision, leader)
    guarded: set[str] = set()
    if threat is not None:
        guards = _members(decision, decision.memory.reserve)
        raiders = decision.threats(threat.position)
        if guards and force_strength(guards) >= force_strength(raiders) * 1.3:
            guarded = {unit.entity_id for unit in raiders}
            threat = None
    contacts = {unit.entity_id: unit for unit in decision.threats(center)}
    for squad in ordered[:3]:
        for enemy in decision.threats(_center(_members(decision, squad))):
            contacts[enemy.entity_id] = enemy
    local = [unit for key, unit in contacts.items() if key not in guarded]
    margin = decision.margin(force, local)
    ceiling = min(
        MAX_ORDERS,
        decision.orders
        + (
            MAX_SQUAD_UPDATES
            if threat is not None or decision.simulation.tick % ECONOMY_TICKS >= DECISION_TICKS
            else 1
        ),
    )
    if threat is not None:
        # A threatened base is a rescue obligation, not an optional favorable raid.
        responding = sorted(
            groups,
            key=lambda squad: (
                _center(_members(decision, squad)).distance_to(threat.position),
                squad.entity_ids[0],
            ),
        )
        needed = force_strength(decision.threats(threat.position)) * 1.6
        defenders: list[Squad] = []
        strength = 0.0
        for squad in responding:
            defenders.append(squad)
            strength += force_strength(_members(decision, squad))
            if strength >= needed:
                break
        point, attack = _defense_order(decision, threat)
        _order_groups(
            decision,
            defenders,
            SquadPhase.DEFENDING,
            point,
            threat,
            ceiling,
            attack=attack,
        )
        return
    for squad in groups:
        if squad.phase is SquadPhase.DEFENDING:
            _set_phase(decision, squad, SquadPhase.GATHERING)
            squad.target_id = None
    target: Entity | None = None
    if local:
        near = [
            squad
            for squad in ordered
            if _center(_members(decision, squad)).distance_to(center) <= 2 * ARMY_RADIUS
        ]
        if margin < -0.4:
            point = _assembly_point(decision, leader)
            _order_groups(decision, near, SquadPhase.RETREATING, point, None, ceiling)
        else:
            target = min(
                local,
                key=lambda unit: (center.distance_to(unit.position), unit.health, unit.entity_id),
            )
            _order_groups(
                decision,
                near,
                SquadPhase.ATTACKING,
                target.position,
                target,
                ceiling,
                attack=True,
            )
        return
    if leader.phase is SquadPhase.RETREATING:
        if decision.simulation.tick - leader.phase_tick < RETREAT_TICKS:
            _order_groups(
                decision,
                ordered,
                SquadPhase.RETREATING,
                leader.destination or _assembly_point(decision, leader),
                None,
                ceiling,
            )
            return
        for squad in groups:
            if squad.phase is SquadPhase.RETREATING:
                _set_phase(decision, squad, SquadPhase.GATHERING)
    can_develop = any(
        unit.kind.production_kinds and unit.is_complete or unit.profile.can_build
        for unit in decision.owned
    )
    enemy_can_develop = any(
        unit.kind.production_kinds and unit.is_complete or unit.profile.can_build
        for unit in decision.enemies
    )
    cleanup = not can_develop or not enemy_can_develop
    if not active:
        minimum = 1 if cleanup else ATTACK_WAVE_SIZE
        if decision.simulation.tick - leader.phase_tick >= 900:
            minimum = min(minimum, 12)
        members = _members(decision, leader)
        assembled = sum(unit.position.distance_to(center) <= ARMY_RADIUS for unit in members)
        target = (
            _choose_target(decision, force)
            if len(force) >= minimum and assembled >= ceil(len(members) * 0.8)
            else None
        )
        if target is None:
            for squad in ordered:
                _order_groups(
                    decision,
                    [squad],
                    SquadPhase.GATHERING,
                    _assembly_point(decision, squad),
                    None,
                    ceiling,
                )
            return
        active = [
            squad
            for squad in ordered
            if _center(_members(decision, squad)).distance_to(center) <= ARMY_RADIUS
        ]
        for squad in active:
            _set_phase(decision, squad, SquadPhase.ADVANCING)
            squad.target_id = target.entity_id
    target = decision.simulation.entities.get(leader.target_id or "")
    if target is None:
        target = _choose_target(decision, force)
    if target is None or len(force) < 12 and not cleanup:
        _order_groups(
            decision,
            active,
            SquadPhase.GATHERING,
            _assembly_point(decision, leader),
            None,
            ceiling,
        )
        return
    members = _members(decision, leader)
    assembled = sum(unit.position.distance_to(center) <= ARMY_RADIUS for unit in members)
    point = target.selection_position
    holding = assembled < ceil(len(members) * 0.8) or any(
        _center(_members(decision, squad)).distance_to(center) > 2 * ARMY_RADIUS for squad in active
    )
    if holding:
        point = center
    attack = not holding and center.distance_to(target.selection_position) <= LOCAL_RADIUS
    followers = [squad for squad in active if squad is not leader]
    offset = (decision.simulation.tick // DECISION_TICKS) % max(1, len(followers))
    for squad in [leader, *followers[offset:], *followers[:offset]]:
        lagging = _center(_members(decision, squad)).distance_to(center) > ARMY_RADIUS
        _order_groups(
            decision,
            [squad],
            SquadPhase.ATTACKING if attack and not lagging else SquadPhase.ADVANCING,
            center if lagging else point,
            target,
            ceiling,
            attack=attack and not lagging,
        )
    # Fresh units assemble locally; only a complete reinforcement batch follows the army.
    for squad in ordered:
        if squad in active:
            continue
        point = (
            center
            if len(squad.entity_ids) >= REINFORCEMENT_SIZE
            else _assembly_point(decision, squad)
        )
        if (
            len(squad.entity_ids) >= REINFORCEMENT_SIZE
            and _center(_members(decision, squad)).distance_to(center) <= ARMY_RADIUS
        ):
            _order_groups(
                decision,
                [squad],
                SquadPhase.ADVANCING,
                target.selection_position,
                target,
                ceiling,
            )
        else:
            _order_groups(decision, [squad], SquadPhase.GATHERING, point, None, ceiling)


def _pending_construction(decision: _Decision) -> list[ConstructionParameters]:
    return [
        automation.parameters
        for automation in decision.simulation.live_automations
        if automation.owner_id == decision.owner_id
        and isinstance(automation.parameters, ConstructionParameters)
    ]


def _production_rate(decision: _Decision, kind: EntityKind) -> float:
    return (
        kind.profile.production_cost
        * decision.simulation.TICKS_PER_SECOND
        / kind.profile.production_ticks
    )


def _economy_forecast(decision: _Decision) -> tuple[float, float]:
    income = building_income_per_second(decision.simulation, decision.owner_id)
    capacity = 0.0
    for unit in decision.owned:
        if unit.kind is not EntityKind.FACTORY or not unit.is_complete:
            continue
        loop = decision.simulation.continuous_production(unit.entity_id)
        if loop is None:
            kind = EntityKind.LIGHT_TANK
        else:
            assert isinstance(loop.parameters, ProductionParameters)
            kind = loop.parameters.unit_kind
        capacity += _production_rate(decision, kind)
    return float(income), capacity


def _refresh_growth_credit(decision: _Decision) -> None:
    simulation, memory = decision.simulation, decision.memory
    _, committed = _production_demand(decision)
    committed += sum(
        job.building_kind.profile.construction_cost
        for job in _pending_construction(decision)
        if not job.cost_paid
    )
    cash = max(0, simulation.resources.get(decision.owner_id, 0) - committed)
    elapsed = max(0, simulation.tick - memory.budget_tick) / simulation.TICKS_PER_SECOND
    income, _ = _economy_forecast(decision)
    memory.growth_credit = min(
        cash, max(cash * GROWTH_SHARE, memory.growth_credit + income * elapsed * GROWTH_SHARE)
    )
    memory.budget_tick = simulation.tick


def _income_upgrade_goal(decision: _Decision, *, threatened: bool) -> tuple[Entity, float] | None:
    if (
        threatened
        or not any(unit.kind.production_kinds and unit.is_complete for unit in decision.owned)
        or not any(unit.profile.can_build for unit in decision.owned)
        or any(
            unit.upgrade.remaining_ticks and unit.profile.income_per_second
            for unit in decision.owned
        )
    ):
        return None
    reclaiming = {unit.reclaim_target_id for unit in decision.owned if unit.reclaim_target_id}
    candidates: list[tuple[float, str, Entity]] = []
    for unit in decision.owned:
        upgrade = unit.next_upgrade
        if (
            not unit.is_complete
            or unit.upgrade.remaining_ticks
            or unit.entity_id in reclaiming
            or unit.health < unit.profile.max_health * 0.75
            or upgrade is None
        ):
            continue
        gain = (
            upgrade.profile.income_per_second - unit.profile.income_per_second
        ) * decision.simulation.ai_income_multiplier
        if gain <= 0 or decision.threats(unit.selection_position):
            continue
        payback = (
            upgrade.duration_ticks / decision.simulation.TICKS_PER_SECOND + upgrade.cost / gain
        )
        candidates.append((payback, unit.entity_id, unit))
    if not candidates:
        return None
    payback, _, unit = min(candidates, key=lambda item: (item[0], item[1]))
    return unit, payback


def _start_income_upgrade(decision: _Decision, goal: tuple[Entity, float] | None) -> bool:
    if goal is None:
        return False
    unit, _ = goal
    upgrade = unit.next_upgrade
    assert upgrade is not None
    if upgrade.cost > decision.memory.growth_credit:
        return False
    result = decision.submit(UpgradeEntityCommand(unit.entity_id, owner_id=decision.owner_id))
    if result is not None and result.accepted:
        decision.memory.growth_credit = max(0.0, decision.memory.growth_credit - upgrade.cost)
        return True
    return False


def _outpost_point(decision: _Decision, frontier: Point) -> Point:
    producers = [unit.selection_position for unit in decision.owned if unit.kind.production_kinds]
    unsupported = [
        unit.selection_position
        for unit in decision.owned
        if unit.is_complete
        and unit.profile.income_per_second
        and all(unit.selection_position.distance_to(point) > 20 for point in producers)
    ]
    if unsupported:
        return min(unsupported, key=lambda point: (point.distance_to(frontier), point.x, point.y))
    occupied = {
        unit.position for unit in decision.simulation.entities.values() if not unit.is_movable
    }
    occupied.update(job.position for job in _pending_construction(decision))
    sites = [
        site for site in decision.simulation.game_map.resource_sites or () if site not in occupied
    ]
    if sites:
        goal = min(
            sites,
            key=lambda point: (
                point.distance_to(frontier) + point.distance_to(decision.home) * 0.35,
                point.x,
                point.y,
            ),
        )
    elif decision.enemies:
        goal = min(
            decision.enemies, key=lambda unit: frontier.distance_to(unit.selection_position)
        ).selection_position
    else:
        return frontier
    dx, dy = goal.x - frontier.x, goal.y - frontier.y
    length = max(1.0, sqrt(dx * dx + dy * dy))
    step = min(28.0, length)
    return (
        _safe_point(
            decision, Point(frontier.x + dx / length * step, frontier.y + dy / length * step), 8
        )
        or frontier
    )


def _defense_point(decision: _Decision, anchor: Point) -> Point:
    enemy = min(
        decision.enemies, key=lambda unit: anchor.distance_to(unit.selection_position), default=None
    )
    if enemy is None:
        return anchor
    dx, dy = enemy.selection_position.x - anchor.x, enemy.selection_position.y - anchor.y
    length = max(1.0, sqrt(dx * dx + dy * dy))
    return Point(anchor.x + 6 * dx / length, anchor.y + 6 * dy / length)


def _plan_growth(decision: _Decision, *, threatened: bool) -> None:
    simulation, memory = decision.simulation, decision.memory
    pending = _pending_construction(decision)
    upgrade_goal = _income_upgrade_goal(decision, threatened=threatened)
    busy = {key for job in pending for key in job.builder_ids}
    builders = [
        unit
        for unit in decision.owned
        if unit.profile.can_build
        and unit.entity_id not in busy
        and unit.entity_id not in simulation.assignments
    ]
    all_builders = sum(unit.profile.can_build for unit in decision.owned)
    if not builders or len(pending) >= min(6, max(2, all_builders)):
        _start_income_upgrade(decision, upgrade_goal)
        return
    counts = {
        kind: sum(unit.kind is kind for unit in decision.owned)
        + sum(job.building_kind is kind and job.site_entity_id is None for job in pending)
        for kind in EntityKind
    }
    _, committed = _production_demand(decision)
    cash = (
        simulation.resources.get(decision.owner_id, 0)
        - committed
        - sum(job.building_kind.profile.construction_cost for job in pending if not job.cost_paid)
    )
    budget = min(cash, memory.growth_credit)
    income, capacity = _economy_forecast(decision)
    frontier = _frontier(decision)
    outpost = _outpost_point(decision, frontier)
    options: list[tuple[EntityKind, Point]] = []
    if not counts[EntityKind.FACTORY] and (
        income or cash >= EntityKind.FACTORY.profile.construction_cost
    ):
        options.append((EntityKind.FACTORY, decision.home))
    enemy_army = [
        unit for unit in decision.enemies if unit.is_movable and unit.profile.attack_damage > 0
    ]
    own_army = [
        unit for unit in decision.owned if unit.is_movable and unit.profile.attack_damage > 0
    ]
    core_defended = any(
        unit.kind is EntityKind.TURRET and unit.selection_position.distance_to(decision.home) <= 18
        for unit in decision.owned
    ) or any(
        job.building_kind is EntityKind.TURRET and job.position.distance_to(decision.home) <= 18
        for job in pending
    )
    if (
        income
        and not core_defended
        and sum(unit.profile.production_cost for unit in enemy_army)
        >= 6 * EntityKind.LIGHT_TANK.profile.production_cost
        and force_strength(enemy_army) > force_strength(own_army) * 1.1
    ):
        options.append((EntityKind.TURRET, _defense_point(decision, decision.home)))
    cash_backlog = (
        ATTACK_WAVE_SIZE * EntityKind.LIGHT_TANK.profile.production_cost
        + EntityKind.FACTORY.profile.construction_cost
    )
    if counts[EntityKind.FACTORY] == 1 and cash >= cash_backlog:
        options.append((EntityKind.FACTORY, outpost))
    if counts[EntityKind.RESOURCE_GENERATOR] < 2 or income < capacity * 0.8:
        options.append((EntityKind.RESOURCE_GENERATOR, frontier))
    defenses = [
        unit.selection_position for unit in decision.owned if unit.kind is EntityKind.TURRET
    ]
    defenses.extend(job.position for job in pending if job.building_kind is EntityKind.TURRET)
    exposed = [
        unit.selection_position
        for unit in decision.owned
        if unit.is_complete
        and (unit.profile.income_per_second or unit.kind.production_kinds)
        and all(unit.selection_position.distance_to(gun) > 15 for gun in defenses)
    ]
    army_size = sum(unit.is_movable and unit.profile.attack_damage > 0 for unit in decision.owned)
    desired_factories = min(
        10,
        max(
            2,
            ceil(income * 1.25 / _production_rate(decision, EntityKind.LIGHT_TANK)),
            1 + counts[EntityKind.RESOURCE_GENERATOR] // 2,
            1 + army_size // ATTACK_WAVE_SIZE,
        ),
    )
    if counts[EntityKind.FACTORY] < 2:
        options.append((EntityKind.FACTORY, outpost))
    if exposed:
        point = min(exposed, key=lambda point: (point.distance_to(frontier), point.x, point.y))
        options.append((EntityKind.TURRET, _defense_point(decision, point)))
    if 2 <= counts[EntityKind.FACTORY] < desired_factories:
        options.append((EntityKind.FACTORY, outpost))
    if (
        not counts[EntityKind.REPAIR_HUB]
        and sum(
            unit.is_movable and unit.health < unit.profile.max_health for unit in decision.owned
        )
        >= 3
    ):
        options.append((EntityKind.REPAIR_HUB, frontier))
    options.append((EntityKind.RESOURCE_GENERATOR, frontier))
    if counts[EntityKind.MATTER_SYNTHESIZER] < min(10, max(1, counts[EntityKind.FACTORY])):
        options.append((EntityKind.MATTER_SYNTHESIZER, frontier))
    for kind, center in options:
        recovering = (
            kind is EntityKind.FACTORY
            and not counts[EntityKind.FACTORY]
            or bool(kind.profile.income_per_second)
            and not income
        )
        site = _construction_site(decision, kind, center, builders, pending)
        if site is None:
            continue
        builder, point = site
        if upgrade_goal is not None and not recovering:
            build_seconds = (
                simulation.CONSTRUCTION_REQUIRED_VALUE
                / builder.profile.build_speed
                / simulation.TICKS_PER_SECOND
            )
            payback = (
                kind.profile.construction_cost
                / (kind.profile.income_per_second * simulation.ai_income_multiplier)
                + build_seconds
                + builder.position.distance_to(point) / builder.speed
                if kind.profile.income_per_second
                else float("inf")
            )
            if not (
                kind is EntityKind.FACTORY
                and counts[EntityKind.FACTORY] < 2
                and (army_size >= 12 or cash >= cash_backlog)
            ) and (
                kind.profile.income_per_second
                and upgrade_goal[1] <= payback
                or kind is EntityKind.FACTORY
                and income < capacity * 0.8
            ):
                _start_income_upgrade(decision, upgrade_goal)
                return
        if kind.profile.construction_cost > (cash if recovering else budget):
            return
        result = decision.submit(
            CreateConstructionCommand(
                builder.entity_id,
                kind,
                point,
                owner_id=decision.owner_id,
                title="AI expansion",
            )
        )
        if result is not None and result.accepted:
            memory.growth_credit = max(0.0, memory.growth_credit - kind.profile.construction_cost)
        return
    _start_income_upgrade(decision, upgrade_goal)


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
    resource_cells = set().union(
        *(
            footprint_cells(EntityKind.RESOURCE_GENERATOR, site)
            for site in simulation.game_map.resource_sites or ()
        )
    )
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
    anchors = [
        unit.selection_position
        for unit in decision.owned
        if not unit.is_movable and unit.is_complete
    ]
    candidates.sort(
        key=lambda point: (
            point.distance_to(center) * 0.5
            + min(
                (point.distance_to(anchor) for anchor in anchors),
                default=point.distance_to(decision.home),
            )
            + point.distance_to(decision.home) * 0.3
            if kind is EntityKind.RESOURCE_GENERATOR
            else point.distance_to(center),
            point.x,
            point.y,
        )
    )
    for point in candidates[:MAX_PLACEMENT_CANDIDATES]:
        cells = footprint_cells(kind, point)
        if (
            point in claimed
            or reserved & cells
            or decision.production_lanes & cells
            or any(
                simulation.entities[key].is_movable
                for cell in cells
                for key in simulation.occupancy.occupants(cell)
            )
            or kind is not EntityKind.RESOURCE_GENERATOR
            and resource_cells & cells
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


def _production_demand(decision: _Decision) -> tuple[dict[EntityKind, int], int]:
    simulation = decision.simulation
    counts = {kind: sum(entity.kind is kind for entity in decision.owned) for kind in EntityKind}
    committed = 0
    for factory in decision.owned:
        if not factory.kind.production_kinds or not factory.is_complete:
            continue
        for job in simulation.production_queue(factory.entity_id):
            parameters = job.parameters
            assert isinstance(parameters, ProductionParameters)
            if parameters.continuous:
                if parameters.cost_paid:
                    counts[parameters.current_unit_kind] += 1
            else:
                for unit_kind in factory.kind.production_kinds:
                    count = parameters.remaining_count(unit_kind)
                    counts[unit_kind] += count
                    paid = int(parameters.cost_paid and parameters.current_unit_kind is unit_kind)
                    committed += max(0, count - paid) * unit_kind.profile.production_cost
    return counts, committed


def _plan_production(decision: _Decision, *, threatened: bool) -> None:
    simulation, memory = decision.simulation, decision.memory
    counts, committed = _production_demand(decision)
    income = building_income_per_second(simulation, decision.owner_id)
    construction = sum(
        job.building_kind.profile.construction_cost
        for job in _pending_construction(decision)
        if not job.cost_paid
    )
    free = max(
        0,
        int(
            simulation.resources.get(decision.owner_id, 0)
            - committed
            - construction
            - (
                0
                if threatened or not income or not counts[EntityKind.BUILDER]
                else memory.growth_credit
            )
        ),
    )
    memory.budget_paused_ids = [
        key
        for key in memory.budget_paused_ids
        if key in simulation.automations and not simulation.automations[key].status.terminal
    ]
    sources = [unit for unit in decision.owned if unit.kind.production_kinds and unit.is_complete]
    centers = [unit for unit in sources if unit.kind is EntityKind.COMMAND_CENTER]
    factories = [unit for unit in sources if unit.kind is not EntityKind.COMMAND_CENTER]
    offset = (simulation.tick // ECONOMY_TICKS) % max(1, len(factories))
    sources = centers + factories[offset:] + factories[:offset]
    desired_builders = min(8, 3 + counts[EntityKind.RESOURCE_GENERATOR] // 2 + len(factories) // 3)
    military_funds = free
    for source in sources:
        loop = simulation.continuous_production(source.entity_id)
        if loop is not None:
            key = loop.automation_id
            if loop.status is AutomationStatus.PAUSED and key not in memory.budget_paused_ids:
                continue
            parameters = loop.parameters
            assert isinstance(parameters, ProductionParameters)
            cost = parameters.unit_kind.profile.production_cost
            loop_funds = free - (
                EntityKind.BUILDER.profile.production_cost if not counts[EntityKind.BUILDER] else 0
            )
            goal = _composition_goal(decision, counts, military_funds=military_funds)
            if not threatened and income > 0 and goal is not parameters.unit_kind:
                loop_funds -= goal.profile.production_cost
            if loop_funds < cost and loop.status is not AutomationStatus.PAUSED:
                result = decision.submit(PauseAutomationCommand(key, owner_id=decision.owner_id))
                if result is not None and result.accepted and key not in memory.budget_paused_ids:
                    memory.budget_paused_ids.append(key)
            elif loop_funds >= cost and key in memory.budget_paused_ids:
                result = decision.submit(ResumeAutomationCommand(key, owner_id=decision.owner_id))
                if result is not None and result.accepted:
                    memory.budget_paused_ids.remove(key)
            if loop.status is not AutomationStatus.PAUSED:
                free = max(0, free - cost)
        jobs = simulation.production_queue(source.entity_id)
        if any(
            isinstance(job.parameters, ProductionParameters) and not job.parameters.continuous
            for job in jobs
        ):
            continue
        need_builder = counts[EntityKind.BUILDER] < desired_builders and (
            not threatened or not counts[EntityKind.BUILDER]
        )
        if need_builder and (
            not counts[EntityKind.BUILDER]
            or not centers
            or source.kind is EntityKind.COMMAND_CENTER
        ):
            kind = EntityKind.BUILDER
        elif source.kind is EntityKind.COMMAND_CENTER:
            continue
        else:
            kind = _composition_goal(decision, counts, military_funds=military_funds)
        if kind not in source.kind.production_kinds:
            continue
        if kind.profile.production_cost > free:
            # Save for one missing role, not an indefinitely funded production queue.
            # Emergency/no-income production must instead use the cash available now.
            if not threatened and income > 0:
                continue
            kind = EntityKind.LIGHT_TANK
        if kind not in source.kind.production_kinds or kind.profile.production_cost > free:
            continue
        if loop is not None and kind is EntityKind.LIGHT_TANK:
            continue
        count = min(3 if kind is EntityKind.LIGHT_TANK else 1, free // kind.profile.production_cost)
        if kind is EntityKind.BUILDER:
            count = min(2, desired_builders - counts[kind], free // kind.profile.production_cost)
        if count <= 0:
            continue
        result = decision.submit(
            CreateProductionCommand(
                source.entity_id,
                kind,
                count,
                owner_id=decision.owner_id,
                title="AI production",
                rally_point=_staging_point(decision, source.selection_position),
            )
        )
        if result is not None and result.accepted:
            counts[kind] += count
            free -= count * kind.profile.production_cost


def _composition_goal(
    decision: _Decision, counts: dict[EntityKind, int], *, military_funds: int
) -> EntityKind:
    front = counts[EntityKind.LIGHT_TANK] + counts[EntityKind.HEAVY_TANK]
    if front < 8:
        return EntityKind.LIGHT_TANK
    if counts[EntityKind.SCOUT] < max(1, ceil(front / 24)):
        return EntityKind.SCOUT
    armored = sum(
        unit.kind in {EntityKind.HEAVY_TANK, EntityKind.TANK_DESTROYER} for unit in decision.enemies
    )
    defenses = sum(unit.kind is EntityKind.TURRET and unit.is_complete for unit in decision.enemies)
    light_cost = EntityKind.LIGHT_TANK.profile.production_cost
    heavy_cost = EntityKind.HEAVY_TANK.profile.production_cost
    investment = sum(
        count * kind.profile.production_cost
        for kind, count in counts.items()
        if kind.profile.combat_support and kind.profile.movement_speed is not None
    )
    income = building_income_per_second(decision.simulation, decision.owner_id) * (1 - GROWTH_SHARE)
    maturity = min(
        1.0,
        max(
            0.0,
            (investment - 8 * light_cost) / (32 * light_cost),
            military_funds / (8 * heavy_cost),
            income / _production_rate(decision, EntityKind.HEAVY_TANK),
        ),
    )
    # Balance spending instead of tying specialist quotas to the light-tank count.
    # The army's investment keeps its mixed doctrine after a surplus has been spent.
    weights = {
        EntityKind.LIGHT_TANK: 0.48 - 0.33 * maturity,
        EntityKind.HEAVY_TANK: 0.22 + 0.14 * maturity,
        EntityKind.LIGHT_ARTILLERY: 0.13 + 0.01 * maturity,
        EntityKind.TANK_DESTROYER: 0.07 + 0.06 * maturity + (0.12 if armored else 0),
        EntityKind.ARTILLERY: 0.10 + 0.12 * maturity + (0.08 if defenses else 0),
    }
    total_weight = sum(weights.values())
    projected = investment + heavy_cost
    return max(
        weights,
        key=lambda kind: (
            weights[kind] / total_weight * projected - counts[kind] * kind.profile.production_cost,
            -kind.profile.production_ticks,
            kind.value,
        ),
    )
