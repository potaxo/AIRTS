"""Deterministic Phase 1 simulation and command executor."""

from __future__ import annotations

from math import isclose

from airts.automations import AutomationStatus, PatrolAutomation, build_patrol_waypoints
from airts.commands import (
    Command,
    CommandResult,
    CreatePatrolCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
)
from airts.entities import Unit, UnitState
from airts.events import EventLog, EventType
from airts.geometry import Point
from airts.map_model import GameMap


class Simulation:
    TICKS_PER_SECOND = 10
    TICK_SECONDS = 1.0 / TICKS_PER_SECOND

    def __init__(self, game_map: GameMap) -> None:
        self.game_map = game_map
        self.tick = 0
        self.entities = {
            spec.entity_id: Unit(
                entity_id=spec.entity_id,
                kind=spec.kind,
                position=spec.position,
                speed=spec.kind.movement_speed,
            )
            for spec in game_map.entities
        }
        self.automations: dict[str, PatrolAutomation] = {}
        self.assignments: dict[str, str] = {}
        self.events = EventLog()
        self._next_automation_number = 1

    def execute(self, command: Command) -> CommandResult:
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

    def snapshot(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "entities": {
                entity_id: {
                    "position": [round(unit.position.x, 8), round(unit.position.y, 8)],
                    "state": unit.state.value,
                    "assignment": self.assignments.get(entity_id),
                }
                for entity_id, unit in sorted(self.entities.items())
            },
            "automations": {
                automation_id: automation.to_dict()
                for automation_id, automation in sorted(self.automations.items())
            },
        }

    def _move(self, command: MoveCommand) -> CommandResult:
        error = self._validate_entities(command.entity_ids)
        if error is not None:
            return self._reject("move", error)
        if not self.game_map.is_passable(command.target):
            return self._reject("move", "TARGET_NOT_PASSABLE")
        for entity_id in command.entity_ids:
            self._detach_for_manual_override(entity_id)
            unit = self.entities[entity_id]
            unit.move_target = command.target
            unit.state = UnitState.MOVING
            self.events.record(
                self.tick,
                EventType.MOVEMENT_STARTED,
                entity_id,
                target=[command.target.x, command.target.y],
                source="manual",
            )
        return self._accept("move")

    def _create_patrol(self, command: CreatePatrolCommand) -> CommandResult:
        error = self._validate_entities(command.entity_ids)
        if error is not None:
            return self._reject("create_patrol", error)
        if not command.title.strip():
            return self._reject("create_patrol", "EMPTY_TITLE")
        try:
            waypoints = build_patrol_waypoints(command.target, self.game_map)
        except ValueError as error_value:
            return self._reject("create_patrol", str(error_value).upper().replace(" ", "_"))
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
            self.entities[entity_id].move_target = None
            self.entities[entity_id].state = UnitState.PATROLLING
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
            unit = self.entities[entity_id]
            unit.move_target = None
            unit.state = UnitState.IDLE
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
            unit = self.entities[entity_id]
            unit.move_target = None
            unit.state = UnitState.IDLE
        self._record_automation_state(automation)
        return self._accept("cancel_automation", automation_id)

    def _drive_automations(self) -> None:
        for automation in self.automations.values():
            if automation.status is not AutomationStatus.ACTIVE:
                continue
            for entity_id in tuple(automation.entity_ids):
                unit = self.entities[entity_id]
                if unit.move_target is None:
                    target = automation.take_next_waypoint(entity_id)
                    unit.move_target = target
                    unit.state = UnitState.PATROLLING
                    self.events.record(
                        self.tick,
                        EventType.MOVEMENT_STARTED,
                        entity_id,
                        target=[target.x, target.y],
                        source=automation.automation_id,
                    )

    def _move_entities(self) -> None:
        for unit in self.entities.values():
            target = unit.move_target
            if target is None:
                continue
            distance = unit.position.distance_to(target)
            maximum_step = unit.speed * self.TICK_SECONDS
            if distance <= maximum_step or isclose(distance, maximum_step):
                next_position = target
                arrived = True
            else:
                fraction = maximum_step / distance
                next_position = Point(
                    unit.position.x + (target.x - unit.position.x) * fraction,
                    unit.position.y + (target.y - unit.position.y) * fraction,
                )
                arrived = False
            if not self.game_map.is_passable(next_position):
                unit.move_target = None
                unit.state = UnitState.IDLE
                self.events.record(
                    self.tick,
                    EventType.MOVEMENT_FAILED,
                    unit.entity_id,
                    reason="IMPASSABLE_TERRAIN",
                    position=[next_position.x, next_position.y],
                )
                continue
            unit.position = next_position
            if arrived:
                unit.move_target = None
                assignment = self.assignments.get(unit.entity_id)
                unit.state = UnitState.PATROLLING if assignment is not None else UnitState.IDLE
                self.events.record(
                    self.tick,
                    EventType.MOVEMENT_COMPLETED,
                    unit.entity_id,
                    position=[unit.position.x, unit.position.y],
                    assignment=assignment,
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

    def _validate_entities(self, entity_ids: tuple[str, ...]) -> str | None:
        if not entity_ids:
            return "NO_ENTITIES"
        if len(set(entity_ids)) != len(entity_ids):
            return "DUPLICATE_ENTITY"
        if unknown := next((item for item in entity_ids if item not in self.entities), None):
            return f"UNKNOWN_ENTITY:{unknown}"
        return None

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
