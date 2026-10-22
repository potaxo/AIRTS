"""CPU-only economy planning and persistent squads through validated commands.

The force estimate is an aggregate short-horizon forecast, not a second simulation:
it considers current health, damage, range and approach time, without changing units.
Spatial queries are shared within a decision; expensive orders have fixed budgets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from math import ceil, hypot, inf, sqrt
from typing import TYPE_CHECKING

import numpy as np

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
from airts.world._numeric import FloatArray, compile_numeric
from airts.world.entities import Entity
from airts.world.map import Cell, Terrain, footprint_cells
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
MAX_COMMAND_COHORTS = 4
MASS_ASSAULT_SIZE = 500
PRODUCTION_CLEARANCE = 3.0
ATTACK_WAVE_SIZE = 20
REINFORCEMENT_SIZE = 6
ARMY_RADIUS = 14.0
GROWTH_SHARE = 0.4
MAX_EXPANSION_REGIONS = 4
CASH_DRAIN_SECONDS = 60
BASE_BLOCK_SIZE = 12
BASE_STREET_WIDTH = 2
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
    placement_checks: dict[EntityKind, int] = field(default_factory=dict)
    production_demand: tuple[dict[EntityKind, int], int] | None = None
    enemy_positions: tuple[tuple[Entity, ...], FloatArray] | None = None
    staging_points: dict[Point, Point] = field(default_factory=dict)
    staging_obstacles: frozenset[Cell] | None = None
    producer_boxes: FloatArray | None = None

    def nearest_enemy(self, point: Point) -> Entity | None:
        """Reuse positions within this decision; commands cannot move hostile bodies here."""
        if self.enemy_positions is None:
            enemies = tuple(sorted(self.enemies, key=lambda entity: entity.entity_id))
            centers = [entity.selection_position for entity in enemies]
            coordinates = np.asarray([(p.x, p.y) for p in centers], dtype=np.float64).reshape(
                (-1, 2)
            )
            self.enemy_positions = enemies, coordinates
        enemies, coordinates = self.enemy_positions
        index = _nearest_enemy_index(coordinates, float(point.x), float(point.y))
        return enemies[index] if index >= 0 else None

    def submit(self, command: Command) -> CommandResult | None:
        if self.orders >= MAX_ORDERS:
            return None
        self.orders += 1
        self.simulation._opponent_command_count += 1
        self.production_demand = None
        if isinstance(command, CreateConstructionCommand):
            self.staging_points.clear()
            self.staging_obstacles = None
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
    rich = [slot for slot in slots if _has_surplus(simulation.resources.get(slot.owner_id, 0))]
    if not due and not rich:
        return
    entities = sorted(simulation.entities.values(), key=lambda entity: entity.entity_id)
    owners = {entity.owner_id for entity in entities}
    rich = [slot for slot in rich if slot.owner_id in owners]
    macro_slot = rich[simulation.tick % len(rich)] if rich else None
    scheduled = due + ([macro_slot] if macro_slot is not None and macro_slot not in due else [])
    for slot in scheduled:
        if simulation._opponent_command_count >= MAX_ORDERS:
            break
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
        decision.orders = simulation._opponent_command_count
        decision.production_lanes = _production_lanes(decision)
        threat = _base_threat(decision)
        if slot not in due:
            if _has_surplus(_available_cash(decision)):
                _refresh_growth_credit(decision)
                _plan_production(
                    decision, threatened=threat is not None, order_limit=decision.orders + 1
                )
                before = decision.orders
                _plan_growth(
                    decision, threatened=threat is not None, order_limit=decision.orders + 1
                )
                if (
                    decision.orders > before
                    and decision.orders < MAX_ORDERS
                    and sum(decision.placement_checks.values()) <= MAX_PLACEMENT_CANDIDATES // 4
                ):
                    _plan_growth(
                        decision, threatened=threat is not None, order_limit=decision.orders + 1
                    )
            continue
        frontier = _frontier(decision)
        decision.rally = _staging_point(decision, frontier)
        economy_due = simulation.tick % ECONOMY_TICKS < DECISION_TICKS
        if economy_due:
            # Keep one immediate tactical response and two macro opportunities.
            # Persistent combat/repair/clearance must not starve funded expansion.
            _assign_squads(decision)
            _drive_reserve(decision, threat)
            _drive_army(decision, threat, order_limit=1)
            _refresh_growth_credit(decision)
            if _has_surplus(_available_cash(decision)):
                _plan_production(
                    decision, threatened=threat is not None, order_limit=decision.orders + 1
                )
            _plan_growth(decision, threatened=threat is not None, order_limit=decision.orders + 1)
            _clear_production_exits(decision)
            if threat is None:
                _repair_wounded(decision)
            _plan_production(decision, threatened=threat is not None)
            if decision.orders < MAX_ORDERS:
                _plan_growth(decision, threatened=threat is not None)
            continue
        if threat is None:
            _repair_wounded(decision)
        _assign_squads(decision)
        _clear_production_exits(decision)
        _drive_reserve(decision, threat)
        _drive_army(decision, threat)


def _center(units: Sequence[Entity]) -> Point:
    return Point(
        sum(unit.selection_position.x for unit in units) / len(units),
        sum(unit.selection_position.y for unit in units) / len(units),
    )


@compile_numeric
def _nearest_enemy_index(positions: FloatArray, x: float, y: float) -> int:
    """Complete nearest search; sorted input retains the existing entity-ID tie break."""
    best = -1
    distance = inf
    for index in range(len(positions)):
        candidate = hypot(positions[index, 0] - x, positions[index, 1] - y)
        if candidate < distance:
            best, distance = index, candidate
    return best


def warm_opponent_queries() -> None:
    _nearest_enemy_index(np.empty((0, 2), dtype=np.float64), 0.0, 0.0)
    _minimum_distances(np.empty((0, 2), dtype=np.float64), np.empty((0, 2), dtype=np.float64), 0.0)
    _producer_nearby(np.empty((0, 4), dtype=np.float64), 0.0, 0.0)


@compile_numeric
def _producer_nearby(boxes: FloatArray, x: float, y: float) -> bool:
    for index in range(len(boxes)):
        dx = max(boxes[index, 0] - x, 0.0, x - boxes[index, 0] - boxes[index, 2])
        dy = max(boxes[index, 1] - y, 0.0, y - boxes[index, 1] - boxes[index, 3])
        if dx * dx + dy * dy < PRODUCTION_CLEARANCE * PRODUCTION_CLEARANCE:
            return True
    return False


def _coordinates(points: Sequence[Point]) -> FloatArray:
    return np.asarray([(point.x, point.y) for point in points], dtype=np.float64).reshape((-1, 2))


@compile_numeric
def _minimum_distances(queries: FloatArray, references: FloatArray, default: float) -> FloatArray:
    result = np.full(len(queries), default)
    if len(references):
        for index in range(len(queries)):
            best = inf
            for other in range(len(references)):
                best = min(
                    best,
                    hypot(
                        queries[index, 0] - references[other, 0],
                        queries[index, 1] - references[other, 1],
                    ),
                )
            result[index] = best
    return result


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
    nearest = decision.nearest_enemy(decision.home)
    assert nearest is not None
    enemy = nearest.selection_position
    return min(anchors, key=lambda point: (point.distance_to(enemy), point.x, point.y))


def _safe_point(decision: _Decision, desired: Point, radius: int) -> Point | None:
    simulation = decision.simulation
    if decision.staging_obstacles is None:
        cells = set(simulation._building_cells())
        for job in _pending_construction(decision):
            width, height = job.building_kind.profile.footprint
            cells.update(
                (x, y)
                for x in range(int(job.position.x) - 3, int(job.position.x) + width + 3)
                for y in range(int(job.position.y) - 3, int(job.position.y) + height + 3)
            )
        decision.staging_obstacles = frozenset(cells)
    blocked = decision.staging_obstacles
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
    if decision.producer_boxes is None:
        decision.producer_boxes = np.asarray(
            [
                (source.position.x, source.position.y, *source.kind.profile.footprint)
                for source in decision.owned
                if source.kind.production_kinds
            ],
            dtype=np.float64,
        ).reshape((-1, 4))
    return _producer_nearby(decision.producer_boxes, float(point.x), float(point.y))


def _staging_point(decision: _Decision, anchor: Point) -> Point:
    if anchor in decision.staging_points:
        return decision.staging_points[anchor]
    enemy = decision.nearest_enemy(anchor)
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
    point = point or _safe_point(decision, anchor, 12) or anchor
    decision.staging_points[anchor] = point
    return point


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
        decision.submit(MoveCommand(units, decision.rally, owner_id=decision.owner_id))


def _members(decision: _Decision, squad: Squad) -> list[Entity]:
    return [decision.simulation.entities[key] for key in squad.entity_ids]


def _repair_wounded(decision: _Decision) -> None:
    simulation = decision.simulation
    wounded = tuple(
        entity.entity_id
        for entity in decision.owned
        if entity.is_movable
        and entity.health / entity.kind.profile.max_health < 0.30
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
    mobilized = sum(len(squad.entity_ids) for squad in memory.squads) >= MASS_ASSAULT_SIZE // 2
    waiting = [
        squad
        for squad in memory.squads
        if squad.phase is SquadPhase.GATHERING or mobilized and squad.phase is SquadPhase.ADVANCING
    ]
    for index, squad in enumerate(waiting):
        if not squad.entity_ids:
            continue
        for other in waiting[index + 1 :]:
            if not other.entity_ids or len(squad.entity_ids) >= MAX_SQUAD_SIZE:
                continue
            if (squad.phase, squad.target_id) != (other.phase, other.target_id):
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
                if (
                    squad.phase in {SquadPhase.GATHERING, SquadPhase.RETREATING}
                    or mobilized
                    and squad.phase is SquadPhase.ADVANCING
                )
                and len(squad.entity_ids) < MAX_SQUAD_SIZE
                and _center(_members(decision, squad)).distance_to(unit.position) <= 2 * ARMY_RADIUS
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
    enemies = [
        unit for unit in decision.enemies if unit.profile.attack_damage > 0 and unit.is_complete
    ]
    assets_index = SpatialIndex(
        {unit.entity_id: unit.selection_position for unit in assets}, bucket_size=LOCAL_RADIUS
    )
    nearby = assets_index.nearest_many(
        tuple((unit.selection_position, LOCAL_RADIUS) for unit in enemies)
    )
    threats = [unit for unit, key in zip(enemies, nearby, strict=True) if key is not None]
    under_fire: dict[str, int] = {}
    for asset in assets:
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
        threats,
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
        point, attack = _defense_order(decision, threat)
        _order_groups(
            decision, [squad], SquadPhase.DEFENDING, point, threat, MAX_ORDERS, attack=attack
        )
    else:
        _order_groups(
            decision,
            [squad],
            SquadPhase.GATHERING,
            _staging_point(decision, decision.home),
            None,
            MAX_ORDERS,
        )


def _choose_target(
    decision: _Decision, units: list[Entity], *, infrastructure: bool = False
) -> Entity | None:
    center = _center(units)
    enemies = decision.enemies
    if infrastructure:
        buildings = [enemy for enemy in enemies if not enemy.is_movable]
        enemies = buildings or enemies
    ranked = sorted(
        enemies,
        key=lambda enemy: (
            -TARGET_VALUES.get(enemy.kind, 100.0)
            / (20 + center.distance_to(enemy.selection_position)),
            enemy.entity_id,
        ),
    )
    nearest = sorted(
        enemies,
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
    mass_attack: bool = False,
) -> None:
    tick = decision.simulation.tick
    attacking = attack and target is not None
    assert not mass_attack or attacking
    if not attacking and _near_producer(decision, point):
        point = _safe_point(decision, point, 12) or decision.rally
    work_sites = set().union(
        *(
            footprint_cells(job.building_kind, job.position)
            for job in _pending_construction(decision)
        )
    )
    batch: list[Squad] = []
    dispatch: list[str] = []

    def send_batch() -> None:
        command: Command
        if attacking:
            assert target is not None
            command = AttackCommand(tuple(dispatch), target.entity_id, owner_id=decision.owner_id)
        else:
            command = MoveCommand(tuple(dispatch), point, owner_id=decision.owner_id)
        result = decision.submit(command)
        if result is not None:
            for cohort in batch:
                cohort.last_order_tick = tick
                cohort.last_failure = None if result.accepted else result.reason
                cohort.retry_tick = 0 if attacking and result.accepted else tick + ORDER_RETRY_TICKS
                if result.accepted:
                    cohort.destination = None if attacking else point
        batch.clear()
        dispatch.clear()

    for squad in groups:
        if decision.orders >= ceiling:
            break
        units = _members(decision, squad)
        if (
            batch
            and not mass_attack
            and _center(units).distance_to(_center(_members(decision, batch[0]))) > 2 * ARMY_RADIUS
        ):
            send_batch()
            if decision.orders >= ceiling:
                break
        if (
            phase is SquadPhase.DEFENDING
            and target is not None
            and squad.target_id != target.entity_id
        ):
            squad.last_failure = None
            squad.retry_tick = 0
        _set_phase(decision, squad, phase)
        # Movement keeps the shared strategic objective, not a new per-squad raid.
        squad.target_id = None if target is None else target.entity_id
        if squad.last_failure is not None and tick < squad.retry_tick:
            continue
        if attacking:
            assert target is not None
            members = [
                unit.entity_id
                for unit in units
                if not unit.pursue_target or unit.attack_target_id != target.entity_id
            ]
        else:
            if squad.destination == point and all(
                unit.move_target is not None or unit.position.distance_to(point) <= ASSEMBLY_RADIUS
                for unit in units
            ):
                continue
            if all(
                unit.position.distance_to(point) <= ASSEMBLY_RADIUS
                and not unit.pursue_target
                and (
                    unit.move_target is None
                    or unit.move_target.distance_to(point) <= ASSEMBLY_RADIUS
                )
                and decision.simulation.game_map.cell_for(unit.position)
                not in decision.production_lanes
                and decision.simulation.game_map.cell_for(unit.position) not in work_sites
                for unit in units
            ):
                continue
            members = list(squad.entity_ids)
        if not members:
            continue
        batch.append(squad)
        dispatch.extend(members)
        if not mass_attack and len(batch) >= MAX_COMMAND_COHORTS:
            send_batch()
    if batch and decision.orders < ceiling:
        send_batch()


def _assembly_point(decision: _Decision, squad: Squad) -> Point:
    center = _center(_members(decision, squad))
    sources = [unit for unit in decision.owned if unit.kind.production_kinds and unit.is_complete]
    if not sources:
        return decision.rally
    source = min(
        sources, key=lambda unit: (center.distance_to(unit.selection_position), unit.entity_id)
    )
    return _staging_point(decision, source.selection_position)


def _drive_army(
    decision: _Decision, threat: Entity | None, *, order_limit: int = MAX_ORDERS
) -> None:
    groups = decision.memory.squads
    if not groups:
        return
    guarded: set[str] = set()
    if threat is not None:
        guards = _members(decision, decision.memory.reserve)
        raiders = decision.threats(threat.position)
        if (
            guards
            and _center(guards).distance_to(threat.position) <= 2 * LOCAL_RADIUS
            and force_strength(guards) >= force_strength(raiders) * 1.3
        ):
            guarded = {unit.entity_id for unit in raiders}
            threat = None
    ceiling = min(
        MAX_ORDERS,
        order_limit,
        decision.orders
        + (
            MAX_SQUAD_UPDATES
            if threat is not None or decision.simulation.tick % ECONOMY_TICKS >= DECISION_TICKS
            else 1
        ),
    )
    if threat is not None:
        # Rescue uses the nearest sufficient force, not every offensive cohort.
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
        field = [squad for squad in groups if squad not in defenders]
        point, attack = _defense_order(decision, threat)
        _order_groups(
            decision,
            sorted(defenders, key=lambda squad: squad.last_order_tick),
            SquadPhase.DEFENDING,
            point,
            threat,
            min(ceiling, decision.orders + 1) if field else ceiling,
            attack=attack,
        )
        groups = field
    if groups:
        _drive_field_army(decision, groups, guarded, ceiling)


def _drive_field_army(
    decision: _Decision, groups: list[Squad], guarded: set[str], ceiling: int
) -> None:
    if _drive_mass_assault(decision, groups, ceiling):
        return
    active = [
        squad for squad in groups if squad.phase in {SquadPhase.ADVANCING, SquadPhase.ATTACKING}
    ]
    leader = (
        active[0]
        if active
        else max(groups, key=lambda squad: (len(squad.entity_ids), -squad.phase_tick))
    )
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
    )
    if not force:
        force = _members(decision, leader)
    contacts = {unit.entity_id: unit for unit in decision.threats(center)}
    for squad in ordered[:3]:
        for enemy in decision.threats(_center(_members(decision, squad))):
            contacts[enemy.entity_id] = enemy
    local = [unit for key, unit in contacts.items() if key not in guarded]
    margin = decision.margin(force, local)
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
        near.sort(key=lambda squad: squad.last_order_tick)
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
                sorted(
                    (
                        squad
                        for squad in ordered
                        if squad in near or len(squad.entity_ids) >= REINFORCEMENT_SIZE
                    ),
                    key=lambda squad: squad.last_order_tick,
                ),
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
            for squad in sorted(ordered, key=lambda squad: squad.last_order_tick):
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
    missions: dict[tuple[SquadPhase, Point, bool], list[Squad]] = {}
    for squad in active:
        lagging = _center(_members(decision, squad)).distance_to(center) > ARMY_RADIUS
        mission = (
            SquadPhase.ATTACKING if attack and not lagging else SquadPhase.ADVANCING,
            center if lagging else point,
            attack and not lagging,
        )
        missions.setdefault(mission, []).append(squad)
    # Fresh units assemble locally; only a complete reinforcement batch follows the army.
    for squad in ordered:
        if squad in active:
            continue
        rally = (
            center
            if len(squad.entity_ids) >= REINFORCEMENT_SIZE
            else _assembly_point(decision, squad)
        )
        if (
            len(squad.entity_ids) >= REINFORCEMENT_SIZE
            and _center(_members(decision, squad)).distance_to(center) <= ARMY_RADIUS
        ):
            mission = (SquadPhase.ADVANCING, target.selection_position, False)
        else:
            mission = (SquadPhase.GATHERING, rally, False)
        missions.setdefault(mission, []).append(squad)
    # Give each common mission one dispatch opportunity before revisiting it.
    # Rear reinforcements must not sit behind a constantly changing front order.
    for (phase, destination, engage), cohorts in sorted(
        missions.items(), key=lambda item: min(squad.last_order_tick for squad in item[1])
    ):
        cohorts.sort(key=lambda squad: (holding and squad is not leader, squad.last_order_tick))
        _order_groups(
            decision,
            cohorts,
            phase,
            destination,
            None if phase is SquadPhase.GATHERING else target,
            min(ceiling, decision.orders + 1),
            attack=engage,
        )


def _drive_mass_assault(decision: _Decision, groups: list[Squad], ceiling: int) -> bool:
    """Commit a large field army without a small leader's moving assembly barrier.

    Cohorts retain serializable membership, not separate march destinations. Attack
    intent is cheap to enroll; the shared combat router admits paths over later ticks.
    """
    count = sum(len(squad.entity_ids) for squad in groups)
    if count < MASS_ASSAULT_SIZE // 2:
        return False
    objectives: dict[str, int] = {}
    for squad in groups:
        target = decision.simulation.entities.get(squad.target_id or "")
        if (
            squad.phase is SquadPhase.ADVANCING
            and target is not None
            and decision.simulation.are_hostile(decision.owner_id, target.owner_id)
        ):
            objectives[target.entity_id] = objectives.get(target.entity_id, 0) + len(
                squad.entity_ids
            )
    # Hysteresis keeps losses around 500 from flipping the army back into assembly.
    continuing = max(objectives.values(), default=0) >= count * 0.5
    if count < MASS_ASSAULT_SIZE and not continuing:
        return False
    units = [unit for squad in groups for unit in _members(decision, squad)]
    target = (
        decision.simulation.entities[max(objectives, key=lambda key: (objectives[key], key))]
        if continuing
        else _choose_target(decision, units, infrastructure=True)
    )
    if target is None:
        return False
    for squad in groups:
        if squad.target_id != target.entity_id:
            squad.last_failure = None
            squad.retry_tick = 0
    _order_groups(
        decision,
        groups,
        SquadPhase.ADVANCING,
        target.selection_position,
        target,
        ceiling,
        attack=True,
        mass_attack=True,
    )
    return True


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
        costs = ticks = 0
        for job in decision.simulation.production_queue(unit.entity_id):
            parameters = job.parameters
            assert isinstance(parameters, ProductionParameters)
            for kind in unit.kind.production_kinds:
                count = (
                    int(kind is parameters.unit_kind)
                    if parameters.continuous
                    else parameters.remaining_count(kind)
                )
                costs += count * kind.profile.production_cost
                ticks += count * kind.profile.production_ticks
        capacity += (
            costs * decision.simulation.TICKS_PER_SECOND / ticks
            if ticks
            else _production_rate(decision, EntityKind.LIGHT_TANK)
        )
    return float(income), capacity


def _available_cash(decision: _Decision, committed: int | None = None) -> int:
    if committed is None:
        _, committed = _production_demand(decision)
    committed += sum(
        job.building_kind.profile.construction_cost
        for job in _pending_construction(decision)
        if not job.cost_paid
    )
    return max(0, decision.simulation.resources.get(decision.owner_id, 0) - committed)


def _has_surplus(cash: int) -> bool:
    return cash >= 8 * EntityKind.FACTORY.profile.construction_cost


def _desired_builders(counts: dict[EntityKind, int], factories: int, cash: int) -> int:
    sources = counts[EntityKind.RESOURCE_GENERATOR] + counts[EntityKind.MATTER_SYNTHESIZER]
    normal = 3 + sources // 2 + factories // 3
    if not _has_surplus(cash):
        return normal
    return max(
        normal,
        min(
            3 + factories + sources,
            3 + cash // (16 * EntityKind.BUILDER.profile.production_cost),
        ),
    )


def _refresh_growth_credit(decision: _Decision) -> None:
    simulation, memory = decision.simulation, decision.memory
    cash = _available_cash(decision)
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
    if upgrade.cost > min(_available_cash(decision), decision.memory.growth_credit):
        return False
    result = decision.submit(UpgradeEntityCommand(unit.entity_id, owner_id=decision.owner_id))
    if result is not None and result.accepted:
        decision.memory.growth_credit = max(0.0, decision.memory.growth_credit - upgrade.cost)
        return True
    return False


def _income_build_payback(
    decision: _Decision, kind: EntityKind, builder: Entity, point: Point
) -> float:
    if not kind.profile.income_per_second:
        return float("inf")
    simulation = decision.simulation
    return (
        kind.profile.construction_cost
        / (kind.profile.income_per_second * simulation.ai_income_multiplier)
        + simulation.CONSTRUCTION_REQUIRED_VALUE
        / builder.profile.build_speed
        / simulation.TICKS_PER_SECOND
        + builder.position.distance_to(point) / builder.speed
    )


def _outpost_point(decision: _Decision, frontier: Point) -> Point:
    unsupported = _production_gaps(decision, _pending_construction(decision))
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


def _production_gaps(decision: _Decision, pending: list[ConstructionParameters]) -> list[Point]:
    producers = [unit.selection_position for unit in decision.owned if unit.kind.production_kinds]
    producers.extend(job.position for job in pending if job.building_kind.production_kinds)
    points = [
        _support_anchor(decision, unit.selection_position)
        for unit in decision.owned
        if unit.is_complete and unit.profile.income_per_second
    ]
    index = SpatialIndex({str(i): point for i, point in enumerate(producers)}, bucket_size=20)
    coverage = index.nearest_many(tuple((point, 20.0) for point in points))
    return [point for point, key in zip(points, coverage, strict=True) if key is None]


def _support_anchor(decision: _Decision, point: Point) -> Point:
    game_map = decision.simulation.game_map
    if game_map.terrain_at(point) is not Terrain.BRIDGE:
        return point
    # Support wide-bridge resources from their land approaches.
    approaches = [
        Point(point.x + dx * step, point.y + dy * step)
        for step in (12, 24, 36)
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
    ]
    land = [
        candidate
        for candidate in approaches
        if game_map.is_passable(candidate)
        and game_map.terrain_at(candidate) not in {Terrain.ROAD, Terrain.BRIDGE}
    ]
    return min(
        land,
        key=lambda candidate: (
            candidate.distance_to(point) + candidate.distance_to(decision.home) * 0.5
        ),
        default=point,
    )


def _defense_gaps(decision: _Decision, pending: list[ConstructionParameters]) -> list[Point]:
    forts = [unit.selection_position for unit in decision.owned if unit.kind is EntityKind.TURRET]
    forts.extend(job.position for job in pending if job.building_kind is EntityKind.TURRET)
    points = [
        _support_anchor(decision, unit.selection_position)
        for unit in decision.owned
        if unit.is_complete and (unit.profile.income_per_second or unit.kind.production_kinds)
    ]
    index = SpatialIndex({str(i): point for i, point in enumerate(forts)}, bucket_size=15)
    coverage = index.nearest_many(tuple((point, 15.0) for point in points))
    return [point for point, key in zip(points, coverage, strict=True) if key is None]


def _defense_point(decision: _Decision, anchor: Point) -> Point:
    enemy = min(
        decision.enemies, key=lambda unit: anchor.distance_to(unit.selection_position), default=None
    )
    if enemy is None:
        return anchor
    dx, dy = enemy.selection_position.x - anchor.x, enemy.selection_position.y - anchor.y
    length = max(1.0, sqrt(dx * dx + dy * dy))
    return Point(anchor.x + 6 * dx / length, anchor.y + 6 * dy / length)


def _growth_priorities(
    decision: _Decision, pending: list[ConstructionParameters]
) -> list[EntityKind]:
    counts = {
        kind: sum(unit.kind is kind for unit in decision.owned)
        + sum(job.building_kind is kind and job.site_entity_id is None for job in pending)
        for kind in EntityKind
    }
    income, capacity = _economy_forecast(decision)
    completed_factories = sum(
        unit.kind is EntityKind.FACTORY and unit.is_complete for unit in decision.owned
    )
    rate = (
        capacity / completed_factories
        if completed_factories
        else _production_rate(decision, EntityKind.LIGHT_TANK)
    )
    capacity += sum(rate for job in pending if job.building_kind is EntityKind.FACTORY)
    income += sum(
        job.building_kind.profile.income_per_second * decision.simulation.ai_income_multiplier
        for job in pending
    )
    # Spend down excess stock over a horizon, not by multiplying factories forever.
    # Count planned capacity while builders are still travelling.
    stock = max(0, _available_cash(decision) - 8 * EntityKind.FACTORY.profile.construction_cost)
    factory_goal = max(2, ceil((income + stock / CASH_DRAIN_SECONDS) * (1 - GROWTH_SHARE) / rate))
    factories = counts[EntityKind.FACTORY]
    sources = counts[EntityKind.RESOURCE_GENERATOR] + counts[EntityKind.MATTER_SYNTHESIZER]
    income_goal = max(2, ceil(factory_goal * 0.65), ceil(factories * 0.8))
    turret_goal = max(2, ceil((factories + sources) / 3))
    repair_goal = max(1, ceil(factories / 8))
    income_need = max(
        GROWTH_SHARE,
        (income_goal - sources) / income_goal,
        0.5 * max(0.0, capacity * 0.8 - income) / max(1.0, capacity * 0.8),
    )
    scores = {
        EntityKind.FACTORY: max(
            (factory_goal - factories) / factory_goal,
            0.6 if _production_gaps(decision, pending) else 0.0,
        ),
        EntityKind.RESOURCE_GENERATOR: (
            income_need
            + 0.3
            * max(
                0.0,
                1
                - sqrt(
                    EntityKind.RESOURCE_GENERATOR.profile.construction_cost
                    / EntityKind.MATTER_SYNTHESIZER.profile.construction_cost
                ),
            )
            if income_need > 0
            else 0.0
        ),
        EntityKind.MATTER_SYNTHESIZER: income_need,
        EntityKind.TURRET: max(
            (turret_goal - counts[EntityKind.TURRET]) / turret_goal,
            0.7 if _defense_gaps(decision, pending) else 0.0,
        ),
        EntityKind.REPAIR_HUB: 0.6 * (repair_goal - counts[EntityKind.REPAIR_HUB]) / repair_goal,
    }
    return sorted(
        (kind for kind, score in scores.items() if score > 0),
        key=lambda kind: (-scores[kind], kind is not EntityKind.RESOURCE_GENERATOR, kind.value),
    )


def _region_window(
    points: list[Point], tick: int, limit: int = MAX_EXPANSION_REGIONS
) -> list[Point]:
    near, other = points[: limit - 1], points[limit - 1 :]
    if other:
        near.append(other[(tick // ECONOMY_TICKS) % len(other)])
    return near


def _expansion_regions(
    decision: _Decision,
    kind: EntityKind,
    builders: list[Entity],
    pending: list[ConstructionParameters],
) -> list[Point]:
    game_map = decision.simulation.game_map
    span = max(32, ceil(max(game_map.width, game_map.height) / 12))
    sectors: dict[tuple[int, int], int] = {}
    anchors = [unit.selection_position for unit in decision.owned if not unit.is_movable]
    coverage = [unit.selection_position for unit in decision.owned if unit.kind is kind]
    occupied = set()
    for unit in decision.owned:
        if not unit.is_movable:
            key = (int(unit.position.x) // span, int(unit.position.y) // span)
            width, height = unit.kind.profile.footprint
            sectors[key] = sectors.get(key, 0) + width * height
    for unit in decision.simulation.entities.values():
        if not unit.is_movable:
            occupied.add(unit.position)
    for job in pending:
        if job.site_entity_id is None:
            key = (int(job.position.x) // span, int(job.position.y) // span)
            width, height = job.building_kind.profile.footprint
            sectors[key] = sectors.get(key, 0) + width * height
        anchors.append(job.position)
        if job.building_kind is kind:
            coverage.append(job.position)
        occupied.add(job.position)
    strategic = (
        _production_gaps(decision, pending)
        if kind is EntityKind.FACTORY
        else _defense_gaps(decision, pending)
        if kind is EntityKind.TURRET
        else []
    )
    strategic = _region_window(
        sorted(strategic, key=lambda point: (point.distance_to(decision.home), point.x, point.y)),
        decision.simulation.tick,
        limit=MAX_EXPANSION_REGIONS - 1,
    )
    if kind is EntityKind.RESOURCE_GENERATOR and game_map.resource_sites is not None:
        options = [point for point in game_map.resource_sites if point not in occupied]
    else:
        options = [
            Point((x + min(x + span, game_map.width)) / 2, (y + min(y + span, game_map.height)) / 2)
            for y in range(0, game_map.height, span)
            for x in range(0, game_map.width, span)
        ]
        options.extend(strategic)
    expanding = (
        _has_surplus(_available_cash(decision))
        and (decision.simulation.tick // ECONOMY_TICKS) % 3 == 1
        and (
            kind in {EntityKind.FACTORY, EntityKind.MATTER_SYNTHESIZER}
            or kind is EntityKind.RESOURCE_GENERATOR
            and game_map.resource_sites is None
        )
    )
    frontier = {
        (x, y)
        for x in range(ceil(game_map.width / span))
        for y in range(ceil(game_map.height / span))
        if (x, y) not in sectors
        and any((x + dx, y + dy) in sectors for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)))
    }
    queries = _coordinates(options)
    anchor_distances = _minimum_distances(queries, _coordinates(anchors), 0.0)
    builder_distances = _minimum_distances(
        queries, _coordinates([unit.position for unit in builders]), 0.0
    )
    coverage_distances = _minimum_distances(queries, _coordinates(coverage), 18.0)
    ranked = sorted(
        enumerate(options),
        key=lambda item: (
            (point := item[1]) not in strategic,
            expanding
            and bool(frontier)
            and (int(point.x) // span, int(point.y) // span) not in frontier,
            anchor_distances[item[0]] * 0.8
            + builder_distances[item[0]] * 0.5
            + decision.home.distance_to(point) * 0.3
            + sectors.get((int(point.x) // span, int(point.y) // span), 0) * 1.5 / span
            + (
                max(0.0, 18 - coverage_distances[item[0]])
                if kind in {EntityKind.TURRET, EntityKind.REPAIR_HUB}
                else 0.0
            ),
            point.x,
            point.y,
        ),
    )
    options = [point for _, point in ranked]
    # Keep nearby infill stable; rotate only the fallback beyond the nearest three.
    # A blocked shore cannot hide all reachable rear/flank space indefinitely.
    return _region_window(options, decision.simulation.tick)


def _plan_surplus_growth(
    decision: _Decision, pending: list[ConstructionParameters], *, order_limit: int
) -> None:
    simulation = decision.simulation
    busy = {key for job in pending for key in job.builder_ids}
    builders = [
        unit
        for unit in decision.owned
        if unit.profile.can_build
        and unit.entity_id not in busy
        and unit.entity_id not in simulation.assignments
    ]
    ceiling = min(order_limit, MAX_ORDERS - 1 if decision.orders == 0 else MAX_ORDERS)
    if builders:
        for kind in _growth_priorities(decision, pending):
            if not builders or decision.orders >= ceiling:
                break
            if decision.placement_checks.get(kind, 0) >= MAX_PLACEMENT_CANDIDATES:
                continue
            budget = min(_available_cash(decision), decision.memory.growth_credit)
            if kind.profile.construction_cost > budget:
                continue
            if kind is EntityKind.MATTER_SYNTHESIZER and simulation.game_map.resource_sites is None:
                continue
            site = None
            for center in _expansion_regions(decision, kind, builders, pending):
                site = _construction_site(
                    decision,
                    kind,
                    _defense_point(decision, center) if kind is EntityKind.TURRET else center,
                    builders,
                    pending,
                    candidate_limit=MAX_PLACEMENT_CANDIDATES // MAX_EXPANSION_REGIONS,
                    exact_resource_site=simulation.game_map.resource_sites is not None,
                )
                if site is not None:
                    break
            if site is None:
                continue
            builder, point = site
            if kind.profile.income_per_second:
                upgrade = _income_upgrade_goal(decision, threatened=False)
                if (
                    upgrade is not None
                    and (simulation.tick // ECONOMY_TICKS) % 2 == 0
                    and upgrade[1] <= _income_build_payback(decision, kind, builder, point)
                    and _start_income_upgrade(decision, upgrade)
                ):
                    return
            result = decision.submit(
                CreateConstructionCommand(
                    builder.entity_id, kind, point, owner_id=decision.owner_id, title="AI expansion"
                )
            )
            if result is not None and result.accepted:
                decision.memory.growth_credit -= kind.profile.construction_cost
                builders.remove(builder)
                pending = _pending_construction(decision)
                decision.production_lanes = _production_lanes(decision)
    if decision.orders < ceiling:
        # Front-line combat must not veto upgrades at safe rear income buildings.
        _start_income_upgrade(decision, _income_upgrade_goal(decision, threatened=False))


def _plan_growth(decision: _Decision, *, threatened: bool, order_limit: int = MAX_ORDERS) -> None:
    if decision.orders >= min(MAX_ORDERS, order_limit):
        return
    simulation, memory = decision.simulation, decision.memory
    pending = _pending_construction(decision)
    cash = _available_cash(decision)
    if _has_surplus(cash):
        _plan_surplus_growth(decision, pending, order_limit=order_limit)
        return
    upgrade_goal = _income_upgrade_goal(decision, threatened=threatened)
    busy = {key for job in pending for key in job.builder_ids}
    builders = [
        unit
        for unit in decision.owned
        if unit.profile.can_build
        and unit.entity_id not in busy
        and unit.entity_id not in simulation.assignments
    ]
    if not builders:
        _start_income_upgrade(decision, upgrade_goal)
        return
    counts = {
        kind: sum(unit.kind is kind for unit in decision.owned)
        + sum(job.building_kind is kind and job.site_entity_id is None for job in pending)
        for kind in EntityKind
    }
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
    exposed = _defense_gaps(decision, pending)
    army_size = sum(unit.is_movable and unit.profile.attack_damage > 0 for unit in decision.owned)
    desired_factories = max(
        2,
        ceil(income * 1.25 / _production_rate(decision, EntityKind.LIGHT_TANK)),
        1 + counts[EntityKind.RESOURCE_GENERATOR] // 2,
        1 + army_size // ATTACK_WAVE_SIZE,
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
            payback = _income_build_payback(decision, kind, builder, point)
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
    *,
    candidate_limit: int = MAX_PLACEMENT_CANDIDATES,
    exact_resource_site: bool = False,
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
            [center]
            if exact_resource_site
            else list(simulation.game_map.resource_sites or ())
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
    blocked = simulation._building_cells() | reserved | decision.production_lanes
    fixed_extractor = (
        kind is EntityKind.RESOURCE_GENERATOR and simulation.game_map.resource_sites is not None
    )
    candidates = [
        point
        for point in candidates
        if not (footprint_cells(kind, point) & blocked)
        and all(
            simulation.game_map.contains_cell(cell)
            and simulation.game_map.is_cell_passable(cell)
            and (
                fixed_extractor
                or simulation.game_map.terrain_at_cell(cell) not in {Terrain.ROAD, Terrain.BRIDGE}
                and cell[0] % BASE_BLOCK_SIZE >= BASE_STREET_WIDTH
                and cell[1] % BASE_BLOCK_SIZE >= BASE_STREET_WIDTH
            )
            for cell in footprint_cells(kind, point)
        )
    ]
    if kind is EntityKind.RESOURCE_GENERATOR:
        anchors = [
            unit.selection_position
            for unit in decision.owned
            if not unit.is_movable and unit.is_complete
        ]
        distances = _minimum_distances(_coordinates(candidates), _coordinates(anchors), 0.0)
        candidates = [
            point
            for _, point in sorted(
                enumerate(candidates),
                key=lambda item: (
                    item[1].distance_to(center) * 0.5
                    + (distances[item[0]] if anchors else item[1].distance_to(decision.home))
                    + item[1].distance_to(decision.home) * 0.3,
                    item[1].x,
                    item[1].y,
                ),
            )
        ]
    else:
        candidates.sort(key=lambda point: (point.distance_to(center), point.x, point.y))
    remaining = MAX_PLACEMENT_CANDIDATES - decision.placement_checks.get(kind, 0)
    for point in candidates[: min(candidate_limit, remaining)]:
        decision.placement_checks[kind] = decision.placement_checks.get(kind, 0) + 1
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
        blocked = simulation._building_cells() | reserved | footprint_cells(kind, point)
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
                if (
                    validate_building_placement(simulation, kind, point, owner_id=decision.owner_id)
                    is None
                ):
                    return builder, point
                break
    return None


def _production_demand(decision: _Decision) -> tuple[dict[EntityKind, int], int]:
    if decision.production_demand is not None:
        counts, committed = decision.production_demand
        return counts.copy(), committed
    simulation = decision.simulation
    counts = dict.fromkeys(EntityKind, 0)
    for entity in decision.owned:
        counts[entity.kind] += 1
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
    decision.production_demand = counts.copy(), committed
    return counts, committed


def _plan_production(
    decision: _Decision, *, threatened: bool, order_limit: int = MAX_ORDERS
) -> None:
    simulation, memory = decision.simulation, decision.memory
    counts, committed = _production_demand(decision)
    income = building_income_per_second(simulation, decision.owner_id)
    cash = _available_cash(decision, committed)
    surplus = _has_surplus(cash)
    free = max(
        0,
        int(
            cash
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
    sources.sort(key=lambda source: bool(simulation.production_queue(source.entity_id)))
    desired_builders = _desired_builders(counts, len(factories), cash)
    idle_builders = sum(
        unit.is_movable and unit.profile.can_build and unit.entity_id not in simulation.assignments
        for unit in decision.owned
    )
    backbone = counts[EntityKind.LIGHT_TANK] + counts[EntityKind.HEAVY_TANK]
    if surplus:
        # Standing military orders represent near-future output, not an unlimited
        # cash commitment. Include their mix before assigning more factories.
        for source in factories:
            loop = simulation.continuous_production(source.entity_id)
            if loop is None or loop.status is AutomationStatus.PAUSED:
                continue
            parameters = loop.parameters
            assert isinstance(parameters, ProductionParameters)
            kind = parameters.unit_kind
            if kind is not EntityKind.BUILDER:
                counts[kind] += max(
                    0,
                    ceil(
                        ECONOMY_HORIZON_SECONDS
                        * simulation.TICKS_PER_SECOND
                        / parameters.build_ticks
                    )
                    - int(parameters.cost_paid),
                )
    military_funds = free
    military_goal = (
        EntityKind.LIGHT_TANK
        if backbone < 8
        else _composition_goal(decision, counts, military_funds=military_funds)
    )
    for source in sources:
        if decision.orders >= min(MAX_ORDERS, order_limit):
            break
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
            goal = military_goal
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
        builder_target = desired_builders
        if surplus and not centers:
            combat_count = sum(
                count
                for kind, count in counts.items()
                if kind.profile.combat_support and kind.profile.movable
            )
            builder_target = min(builder_target, max(3, combat_count // 4))
        need_builder = (
            counts[EntityKind.BUILDER] < builder_target
            and (
                not threatened
                or not counts[EntityKind.BUILDER]
                or surplus
                and not decision.threats(source.selection_position)
            )
            and (not surplus or idle_builders < max(2, len(_pending_construction(decision)) // 4))
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
            kind = military_goal
        if kind not in source.kind.production_kinds:
            continue
        managed_loop = loop is not None and loop.title == "AI production"
        if managed_loop and kind is not EntityKind.BUILDER:
            assert loop is not None and isinstance(loop.parameters, ProductionParameters)
            if (
                kind is loop.parameters.unit_kind
                or surplus
                and (loop.parameters.cost_paid or loop.parameters.progress_ticks)
            ):
                continue
        if kind.profile.production_cost > free:
            # Save for one missing role, not an indefinitely funded production queue.
            # Emergency/no-income production must instead use the cash available now.
            if not threatened and income > 0:
                continue
            kind = EntityKind.LIGHT_TANK
        if kind not in source.kind.production_kinds or kind.profile.production_cost > free:
            continue
        if loop is not None and not managed_loop and kind is EntityKind.LIGHT_TANK:
            continue
        continuous = surplus and kind is not EntityKind.BUILDER
        count = (
            1
            if continuous
            else min(
                3 if kind is EntityKind.LIGHT_TANK else 1, free // kind.profile.production_cost
            )
        )
        if kind is EntityKind.BUILDER:
            count = min(
                6 if surplus else 2,
                builder_target - counts[kind],
                free // kind.profile.production_cost,
            )
        if count <= 0:
            continue
        result = decision.submit(
            CreateProductionCommand(
                source.entity_id,
                kind,
                count,
                owner_id=decision.owner_id,
                title="AI production",
                continuous=continuous,
                rally_point=_staging_point(decision, source.selection_position),
            )
        )
        if result is not None and result.accepted:
            counts[kind] += (
                ceil(
                    ECONOMY_HORIZON_SECONDS
                    * simulation.TICKS_PER_SECOND
                    / kind.profile.production_ticks
                )
                if continuous
                else count
            )
            free -= (1 if continuous else count) * kind.profile.production_cost
            if kind in {EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK}:
                backbone += 1 if continuous else count
            military_goal = (
                EntityKind.LIGHT_TANK
                if backbone < 8
                else _composition_goal(decision, counts, military_funds=military_funds)
            )


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
