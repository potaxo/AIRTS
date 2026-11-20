"""Tick-derived visual headings; no simulation state or save fields are modified."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, pi, tau
from typing import TYPE_CHECKING

from airts.contracts.spatial import Point
from airts.presentation.visuals.catalog import APPEARANCES
from airts.presentation.visuals.work import work_sources
from airts.world.entities import Entity

if TYPE_CHECKING:
    from airts.simulation import Simulation


@dataclass(slots=True)
class Facing:
    previous_body: float = 0
    body: float = 0
    previous_weapon: float = 0
    weapon: float = 0
    position: Point = Point(0, 0)
    traveling: bool = False
    body_target: float = 0
    working: bool = False


def heading(start: Point, end: Point) -> float:
    """Clockwise radians from north in map coordinates (positive Y points down)."""
    return atan2(end.x - start.x, start.y - end.y)


def turn_towards(current: float, target: float, allowance: float) -> float:
    if current == target:
        return current
    delta = (target - current + pi) % tau - pi
    if delta > allowance:
        delta = allowance
    if delta < -allowance:
        delta = -allowance
    return current + delta


class FacingHistory:
    def __init__(self) -> None:
        self._key: tuple[int, int, int, int] | None = None
        self._poses: dict[str, Facing] = {}
        self.work_links: tuple[tuple[Entity, Entity], ...] = ()

    def reset(self, simulation: Simulation) -> None:
        self._key = None
        self._poses.clear()
        self.sync(simulation)

    def sync(self, simulation: Simulation) -> None:
        key = (id(simulation), simulation.tick, simulation.command_count, len(simulation.entities))
        if key == self._key:
            return
        same_world = self._key is not None and self._key[0] == id(simulation)
        elapsed = (
            max(0, simulation.tick - self._key[1]) * simulation.TICK_SECONDS
            if same_world and self._key is not None
            else 0
        )
        if not same_world:
            self._poses.clear()
        self.work_links = tuple(work_sources(simulation))
        work_targets = {
            source.entity_id: target.selection_position
            for source, target in self.work_links
            if APPEARANCES[source.kind].work_port is not None
        }
        body_allowance = tau * elapsed
        weapon_allowance = tau * 1.5 * elapsed
        for key_id, entity in simulation.entities.items():
            old = self._poses.get(key_id)
            position = entity.position
            body_target = old.body_target if old is not None else 0.0
            previous = old.position if old is not None else position
            destination = entity.path[0] if entity.path else entity.move_target
            work_target = work_targets.get(key_id)
            if old is not None and old.working and work_target is None and destination is None:
                body_target = old.body
            if entity.is_movable:
                dx, dy = position.x - previous.x, position.y - previous.y
                if (destination is not None or (old is not None and old.traveling)) and (
                    dx * dx + dy * dy > 1e-10
                ):
                    body_target = atan2(dx, -dy)
                elif destination is not None and position != destination:
                    body_target = heading(position, destination)
                if work_target is not None:
                    center = entity.selection_position
                    if center != work_target:
                        body_target = heading(center, work_target)
            else:
                body_target = 0.0
            if old is None:
                weapon_target = _weapon_target(simulation, entity, body_target)
                pose = Facing(body_target, body_target, weapon_target, weapon_target)
                self._poses[key_id] = pose
            elif elapsed > 0:
                # Keep adjacent endpoints unwrapped so the GPU takes the short arc.
                body = (
                    turn_towards(old.body, body_target, body_allowance)
                    if old.body != body_target
                    else old.body
                )
                old.previous_body, old.body = old.body, body
                if entity.attack_target_id is None and old.weapon == old.previous_body:
                    old.previous_weapon, old.weapon = old.previous_body, body
                else:
                    target = _weapon_target(simulation, entity, body)
                    old.previous_weapon, old.weapon = (
                        old.weapon,
                        turn_towards(old.weapon, target, weapon_allowance),
                    )
                # Bound magnitudes without adding a 2pi discontinuity to either pair.
                if abs(old.body) > pi:
                    shift = round(old.body / tau) * tau
                    old.previous_body -= shift
                    old.body -= shift
                if abs(old.weapon) > pi:
                    shift = round(old.weapon / tau) * tau
                    old.previous_weapon -= shift
                    old.weapon -= shift
                pose = old
            else:
                pose = old
            pose.position = position
            pose.traveling = destination is not None
            pose.body_target = body_target
            pose.working = work_target is not None
        if len(self._poses) != len(simulation.entities):
            for removed in self._poses.keys() - simulation.entities.keys():
                del self._poses[removed]
        self._key = key

    def pose(self, entity_id: str) -> Facing:
        return self._poses[entity_id]

    def seed_births(self, simulation: Simulation, entity_ids: tuple[str, ...]) -> None:
        """Seed non-worker births without advancing existing visual endpoints."""
        if (
            self._key is None
            or self._key[0] != id(simulation)
            or any(
                APPEARANCES[simulation.entities[key].kind].work_port is not None
                for key in entity_ids
            )
        ):
            self.sync(simulation)
            return
        for key in entity_ids:
            if key in self._poses:
                continue
            entity = simulation.entities[key]
            destination = entity.path[0] if entity.path else entity.move_target
            body = (
                heading(entity.position, destination)
                if entity.is_movable and destination is not None and entity.position != destination
                else 0.0
            )
            weapon = _weapon_target(simulation, entity, body)
            self._poses[key] = Facing(
                body, body, weapon, weapon, entity.position, destination is not None, body
            )


def _weapon_target(simulation: Simulation, entity: Entity, default: float) -> float:
    target_id = entity.attack_target_id
    target = simulation.entities.get(target_id) if target_id is not None else None
    if target is not None:
        start, end = entity.selection_position, target.selection_position
        dx, dy = end.x - start.x, start.y - end.y
        if dx * dx + dy * dy > 1e-10:
            return atan2(dx, dy)
    return default
