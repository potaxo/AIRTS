"""Deterministic Phase 2 simulation and shared command executor."""

from __future__ import annotations

from collections import deque
from math import isclose

from airts.automations import AutomationStatus, PatrolAutomation, build_patrol_waypoints
from airts.commands import (
    Command,
    CommandResult,
    CreatePatrolCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
    command_to_dict,
)
from airts.entities import Entity, UnitState
from airts.events import EventLog, EventType
from airts.geometry import Point
from airts.map_model import Cell, EntityCategory, GameMap
from airts.occupancy import OccupancyError, OccupancyGrid
from airts.pathfinding import PathfindingError, PathResult, find_path
from airts.visibility import VisibilitySystem


class Simulation:
    TICKS_PER_SECOND = 10
    TICK_SECONDS = 1.0 / TICKS_PER_SECOND

    def __init__(self, game_map: GameMap, random_seed: int = 0) -> None:
        self.game_map = game_map
        self.random_seed = random_seed
        self.tick = 0
        self.entities = {
            spec.entity_id: Entity(
                entity_id=spec.entity_id,
                kind=spec.kind,
                owner_id=spec.owner_id,
                position=spec.position,
                health=spec.kind.profile.max_health,
            )
            for spec in game_map.entities
        }
        self.occupancy = OccupancyGrid(game_map.width, game_map.height)
        for entity in self.entities.values():
            self.occupancy.place(entity.entity_id, entity.occupied_cells)
        self.automations: dict[str, PatrolAutomation] = {}
        self.assignments: dict[str, str] = {}
        self.events = EventLog()
        self.visibility = VisibilitySystem(game_map)
        self._next_automation_number = 1
        self._command_history: list[dict[str, object]] = []
        self._movement_blocked: set[str] = set()
        self._update_visibility()

    @property
    def command_history(self) -> tuple[dict[str, object], ...]:
        return tuple(self._command_history)

    def execute(self, command: Command) -> CommandResult:
        self._command_history.append({"tick": self.tick, "command": command_to_dict(command)})
        if isinstance(command, MoveCommand):
            return self._move(command)
        if isinstance(command, CreatePatrolCommand):
            return self._create_patrol(command)
        if isinstance(command, PauseAutomationCommand):
            return self._pause(command.automation_id)
        if isinstance(command, ResumeAutomationCommand):
            return self._resume(command.automation_id)
        return self._cancel(command.automation_id)

    def advance(self, ticks: int = 1) -> None:
        if ticks < 0:
            raise ValueError("tick count cannot be negative")
        for _ in range(ticks):
            self.tick += 1
            self._drive_automations()
            self._move_entities()
            self._update_visibility()

    def snapshot(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "random_seed": self.random_seed,
            "map": {"id": self.game_map.map_id, "version": self.game_map.map_version},
            "entities": {
                entity_id: entity.to_dict() for entity_id, entity in sorted(self.entities.items())
            },
            "occupancy": self.occupancy.snapshot(),
            "assignments": dict(sorted(self.assignments.items())),
            "automations": {
                automation_id: automation.to_dict()
                for automation_id, automation in sorted(self.automations.items())
            },
            "visibility": self.visibility.to_dict(),
        }

    def export_state(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "random_seed": self.random_seed,
            "entities": {
                entity_id: entity.to_dict() for entity_id, entity in sorted(self.entities.items())
            },
            "assignments": dict(sorted(self.assignments.items())),
            "automations": {
                automation_id: automation.to_dict()
                for automation_id, automation in sorted(self.automations.items())
            },
            "visibility": self.visibility.to_dict(),
            "events": [event.to_dict() for event in self.events.events],
            "command_history": list(self._command_history),
            "next_automation_number": self._next_automation_number,
            "movement_blocked": sorted(self._movement_blocked),
        }

    def _move(self, command: MoveCommand) -> CommandResult:
        error = self._validate_entities(command.entity_ids, require_movable=True)
        if error is not None:
            return self._reject("move", error)
        if not self.game_map.is_passable(command.target):
            return self._reject("move", "TARGET_NOT_PASSABLE")
        try:
            destinations = self._allocate_destinations(command.entity_ids, command.target)
            blocked = self.occupancy.blocked_cells(frozenset(command.entity_ids))
            paths = {
                entity_id: find_path(
                    self.game_map,
                    self.entities[entity_id].position,
                    destinations[entity_id],
                    blocked,
                )
                for entity_id in command.entity_ids
            }
        except PathfindingError as error_value:
            self.events.record(
                self.tick,
                EventType.PATHFINDING_FAILED,
                None,
                reason=str(error_value),
                target=[command.target.x, command.target.y],
            )
            return self._reject("move", str(error_value))
        for entity_id in command.entity_ids:
            self._detach_for_manual_override(entity_id)
            self._start_path(
                self.entities[entity_id],
                destinations[entity_id],
                paths[entity_id],
                "manual",
                UnitState.MOVING,
            )
        return self._accept("move")

    def _create_patrol(self, command: CreatePatrolCommand) -> CommandResult:
        error = self._validate_entities(command.entity_ids, require_movable=True)
        if error is not None:
            return self._reject("create_patrol", error)
        if not command.title.strip():
            return self._reject("create_patrol", "EMPTY_TITLE")
        try:
            waypoints = build_patrol_waypoints(command.target, self.game_map)
            self._validate_patrol_paths(command.entity_ids, waypoints)
        except (ValueError, PathfindingError) as error_value:
            reason = str(error_value).upper().replace(" ", "_")
            self.events.record(
                self.tick,
                EventType.PATHFINDING_FAILED,
                None,
                reason=reason,
                command="create_patrol",
            )
            return self._reject("create_patrol", reason)
        automation_id = f"patrol_{self._next_automation_number:03d}"
        self._next_automation_number += 1
        for entity_id in command.entity_ids:
            self._detach_from_current_automation(entity_id, "REASSIGNED")
        automation = PatrolAutomation(
            automation_id=automation_id,
            title=command.title.strip(),
            target=command.target,
            entity_ids=list(command.entity_ids),
            waypoints=waypoints,
            created_tick=self.tick,
        )
        self.automations[automation_id] = automation
        for entity_id in command.entity_ids:
            self.assignments[entity_id] = automation_id
            entity = self.entities[entity_id]
            entity.move_target = None
            entity.path.clear()
            entity.state = UnitState.PATROLLING
        self.events.record(
            self.tick,
            EventType.AUTOMATION_CREATED,
            automation_id,
            template="patrol",
            entity_ids=list(command.entity_ids),
            waypoint_count=len(waypoints),
        )
        return self._accept("create_patrol", automation_id)

    def _pause(self, automation_id: str) -> CommandResult:
        automation = self.automations.get(automation_id)
        if automation is None:
            return self._reject("pause_automation", "UNKNOWN_AUTOMATION")
        if automation.status is not AutomationStatus.ACTIVE:
            return self._reject("pause_automation", "AUTOMATION_NOT_ACTIVE")
        automation.status = AutomationStatus.PAUSED
        automation.reason_code = "PLAYER_PAUSED"
        for entity_id in automation.entity_ids:
            entity = self.entities[entity_id]
            entity.move_target = None
            entity.path.clear()
            entity.state = UnitState.IDLE
            self._movement_blocked.discard(entity_id)
        self._record_automation_state(automation)
        return self._accept("pause_automation", automation_id)

    def _resume(self, automation_id: str) -> CommandResult:
        automation = self.automations.get(automation_id)
        if automation is None:
            return self._reject("resume_automation", "UNKNOWN_AUTOMATION")
        if automation.status is not AutomationStatus.PAUSED:
            return self._reject("resume_automation", "AUTOMATION_NOT_PAUSED")
        automation.status = AutomationStatus.ACTIVE
        automation.reason_code = "PLAYER_RESUMED"
        for entity_id in automation.entity_ids:
            self.entities[entity_id].state = UnitState.PATROLLING
        self._record_automation_state(automation)
        return self._accept("resume_automation", automation_id)

    def _cancel(self, automation_id: str) -> CommandResult:
        automation = self.automations.get(automation_id)
        if automation is None:
            return self._reject("cancel_automation", "UNKNOWN_AUTOMATION")
        if automation.status is AutomationStatus.CANCELED:
            return self._reject("cancel_automation", "AUTOMATION_ALREADY_CANCELED")
        automation.status = AutomationStatus.CANCELED
        automation.reason_code = "PLAYER_CANCELED"
        for entity_id in tuple(automation.entity_ids):
            self.assignments.pop(entity_id, None)
            entity = self.entities[entity_id]
            entity.move_target = None
            entity.path.clear()
            entity.state = UnitState.IDLE
            self._movement_blocked.discard(entity_id)
        self._record_automation_state(automation)
        return self._accept("cancel_automation", automation_id)

    def _drive_automations(self) -> None:
        building_cells = self._building_cells()
        for automation in self.automations.values():
            if automation.status is not AutomationStatus.ACTIVE:
                continue
            for entity_id in tuple(automation.entity_ids):
                entity = self.entities[entity_id]
                if entity.move_target is not None or entity.path:
                    continue
                target = automation.take_next_waypoint(entity_id)
                try:
                    path = find_path(self.game_map, entity.position, target, building_cells)
                except PathfindingError as error_value:
                    automation.reason_code = str(error_value)
                    self.events.record(
                        self.tick,
                        EventType.PATHFINDING_FAILED,
                        entity_id,
                        reason=str(error_value),
                        automation_id=automation.automation_id,
                    )
                    continue
                self._start_path(
                    entity,
                    target,
                    path,
                    automation.automation_id,
                    UnitState.PATROLLING,
                )

    def _move_entities(self) -> None:
        for entity_id in sorted(self.entities):
            entity = self.entities[entity_id]
            if not entity.path:
                continue
            target = entity.path[0]
            distance = entity.position.distance_to(target)
            maximum_step = entity.speed * self.TICK_SECONDS
            if distance <= maximum_step or isclose(distance, maximum_step):
                next_position = target
                arrived_at_waypoint = True
            else:
                fraction = maximum_step / distance
                next_position = Point(
                    entity.position.x + (target.x - entity.position.x) * fraction,
                    entity.position.y + (target.y - entity.position.y) * fraction,
                )
                arrived_at_waypoint = False
            if not self.game_map.is_passable(next_position):
                self._fail_movement(entity, "IMPASSABLE_TERRAIN", next_position)
                continue
            next_cells = self._cells_at(entity, next_position)
            try:
                self.occupancy.move(entity_id, next_cells)
            except OccupancyError as error_value:
                if entity_id not in self._movement_blocked:
                    self.events.record(
                        self.tick,
                        EventType.MOVEMENT_BLOCKED,
                        entity_id,
                        reason="OCCUPIED",
                        evidence=str(error_value),
                    )
                    self._movement_blocked.add(entity_id)
                continue
            self._movement_blocked.discard(entity_id)
            entity.position = next_position
            if arrived_at_waypoint:
                entity.path.pop(0)
                if not entity.path:
                    entity.move_target = None
                    assignment = self.assignments.get(entity_id)
                    entity.state = (
                        UnitState.PATROLLING if assignment is not None else UnitState.IDLE
                    )
                    self.events.record(
                        self.tick,
                        EventType.MOVEMENT_COMPLETED,
                        entity_id,
                        position=[entity.position.x, entity.position.y],
                        assignment=assignment,
                    )

    def _start_path(
        self,
        entity: Entity,
        destination: Point,
        path: PathResult,
        source: str,
        state: UnitState,
    ) -> None:
        self._movement_blocked.discard(entity.entity_id)
        entity.path = list(path.waypoints)
        entity.path_cost = path.cost
        entity.move_target = destination if entity.path else None
        entity.state = state if entity.path else UnitState.IDLE
        self.events.record(
            self.tick,
            EventType.PATH_COMPUTED,
            entity.entity_id,
            destination=[destination.x, destination.y],
            cell_count=len(path.cells),
            cost=path.cost,
            source=source,
        )
        if entity.path:
            self.events.record(
                self.tick,
                EventType.MOVEMENT_STARTED,
                entity.entity_id,
                target=[destination.x, destination.y],
                source=source,
            )
        else:
            self.events.record(
                self.tick,
                EventType.MOVEMENT_COMPLETED,
                entity.entity_id,
                position=[entity.position.x, entity.position.y],
                assignment=self.assignments.get(entity.entity_id),
            )

    def _fail_movement(self, entity: Entity, reason: str, position: Point) -> None:
        entity.move_target = None
        entity.path.clear()
        entity.state = UnitState.IDLE
        self.events.record(
            self.tick,
            EventType.MOVEMENT_FAILED,
            entity.entity_id,
            reason=reason,
            position=[position.x, position.y],
        )

    def _allocate_destinations(
        self, entity_ids: tuple[str, ...], target: Point
    ) -> dict[str, Point]:
        blocked = self.occupancy.blocked_cells(frozenset(entity_ids))
        target_cell = self.game_map.cell_for(target)
        frontier = deque([target_cell])
        visited = {target_cell}
        candidates: list[Cell] = []
        while frontier and len(candidates) < len(entity_ids):
            cell = frontier.popleft()
            if self.game_map.is_cell_passable(cell) and cell not in blocked:
                candidates.append(cell)
            for neighbor in self._neighbor_cells(cell):
                if neighbor not in visited and self.game_map.contains_cell(neighbor):
                    visited.add(neighbor)
                    frontier.append(neighbor)
        if len(candidates) < len(entity_ids):
            raise PathfindingError("INSUFFICIENT_DESTINATIONS")
        result: dict[str, Point] = {}
        for index, entity_id in enumerate(entity_ids):
            cell = candidates[index]
            result[entity_id] = (
                target
                if index == 0 and cell == target_cell
                else Point(cell[0] + 0.5, cell[1] + 0.5)
            )
        return result

    def _validate_patrol_paths(
        self, entity_ids: tuple[str, ...], waypoints: tuple[Point, ...]
    ) -> None:
        building_cells = self._building_cells()
        for entity_id in entity_ids:
            find_path(
                self.game_map, self.entities[entity_id].position, waypoints[0], building_cells
            )
        if len(waypoints) > 1:
            for start, end in zip(waypoints, waypoints[1:] + waypoints[:1], strict=True):
                find_path(self.game_map, start, end, building_cells)

    def _building_cells(self) -> frozenset[Cell]:
        return frozenset(
            cell
            for entity in self.entities.values()
            if entity.category is EntityCategory.BUILDING
            for cell in entity.occupied_cells
        )

    def _cells_at(self, entity: Entity, position: Point) -> frozenset[Cell]:
        width, height = entity.kind.profile.footprint
        origin_x = int(position.x)
        origin_y = int(position.y)
        return frozenset(
            (x, y)
            for y in range(origin_y, origin_y + height)
            for x in range(origin_x, origin_x + width)
        )

    def _detach_for_manual_override(self, entity_id: str) -> None:
        automation_id = self.assignments.get(entity_id)
        if automation_id is None:
            return
        self._detach_from_current_automation(entity_id, "MANUAL_OVERRIDE")
        self.events.record(
            self.tick,
            EventType.MANUAL_OVERRIDE,
            entity_id,
            automation_id=automation_id,
        )

    def _detach_from_current_automation(self, entity_id: str, reason: str) -> None:
        automation_id = self.assignments.pop(entity_id, None)
        if automation_id is None:
            return
        automation = self.automations[automation_id]
        automation.remove_entity(entity_id)
        if not automation.entity_ids and automation.status is not AutomationStatus.CANCELED:
            automation.status = AutomationStatus.CANCELED
            automation.reason_code = "NO_ASSIGNED_ENTITIES"
            self._record_automation_state(automation)
        elif reason != "REASSIGNED":
            automation.reason_code = reason

    def _validate_entities(
        self, entity_ids: tuple[str, ...], *, require_movable: bool = False
    ) -> str | None:
        if not entity_ids:
            return "NO_ENTITIES"
        if len(set(entity_ids)) != len(entity_ids):
            return "DUPLICATE_ENTITY"
        if unknown := next((item for item in entity_ids if item not in self.entities), None):
            return f"UNKNOWN_ENTITY:{unknown}"
        if require_movable:
            immovable = next(
                (item for item in entity_ids if not self.entities[item].is_movable), None
            )
            if immovable is not None:
                return f"ENTITY_NOT_MOVABLE:{immovable}"
        return None

    def _update_visibility(self) -> None:
        for player_id, (newly_visible, newly_explored, no_longer_visible) in self.visibility.update(
            self.entities, self.tick
        ).items():
            if newly_visible or newly_explored or no_longer_visible:
                self.events.record(
                    self.tick,
                    EventType.VISIBILITY_CHANGED,
                    player_id,
                    newly_visible=newly_visible,
                    newly_explored=newly_explored,
                    no_longer_visible=no_longer_visible,
                )

    def _record_automation_state(self, automation: PatrolAutomation) -> None:
        self.events.record(
            self.tick,
            EventType.AUTOMATION_STATE_CHANGED,
            automation.automation_id,
            status=automation.status.value,
            reason=automation.reason_code,
        )

    def _accept(self, command: str, automation_id: str | None = None) -> CommandResult:
        self.events.record(
            self.tick,
            EventType.COMMAND_ACCEPTED,
            automation_id,
            command=command,
        )
        return CommandResult(True, "ACCEPTED", automation_id)

    def _reject(self, command: str, reason: str) -> CommandResult:
        self.events.record(
            self.tick,
            EventType.COMMAND_REJECTED,
            None,
            command=command,
            reason=reason,
        )
        return CommandResult(False, reason)

    @staticmethod
    def _neighbor_cells(cell: Cell) -> tuple[Cell, ...]:
        x, y = cell
        return ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1))
