"""Shared command interface used by all Phase 1 control sources."""

from __future__ import annotations

from dataclasses import dataclass

from airts.geometry import Point, SpatialTarget, target_from_dict, target_to_dict


@dataclass(frozen=True, slots=True)
class MoveCommand:
    entity_ids: tuple[str, ...]
    target: Point


@dataclass(frozen=True, slots=True)
class CreatePatrolCommand:
    entity_ids: tuple[str, ...]
    target: SpatialTarget
    title: str = "Patrol Selected Area"


@dataclass(frozen=True, slots=True)
class PauseAutomationCommand:
    automation_id: str


@dataclass(frozen=True, slots=True)
class ResumeAutomationCommand:
    automation_id: str


@dataclass(frozen=True, slots=True)
class CancelAutomationCommand:
    automation_id: str


Command = (
    MoveCommand
    | CreatePatrolCommand
    | PauseAutomationCommand
    | ResumeAutomationCommand
    | CancelAutomationCommand
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    accepted: bool
    reason: str
    automation_id: str | None = None


def command_to_dict(command: Command) -> dict[str, object]:
    if isinstance(command, MoveCommand):
        return {
            "type": "move",
            "entity_ids": list(command.entity_ids),
            "target": [command.target.x, command.target.y],
        }
    if isinstance(command, CreatePatrolCommand):
        return {
            "type": "create_patrol",
            "entity_ids": list(command.entity_ids),
            "target": target_to_dict(command.target),
            "title": command.title,
        }
    if isinstance(command, PauseAutomationCommand):
        command_type = "pause_automation"
    elif isinstance(command, ResumeAutomationCommand):
        command_type = "resume_automation"
    else:
        command_type = "cancel_automation"
    return {"type": command_type, "automation_id": command.automation_id}


def command_from_dict(raw_data: object) -> Command:
    if not isinstance(raw_data, dict) or not all(isinstance(key, str) for key in raw_data):
        raise ValueError("command must be an object")
    command_type = raw_data.get("type")
    if command_type in {"move", "create_patrol"}:
        raw_entity_ids = raw_data.get("entity_ids")
        if not isinstance(raw_entity_ids, list) or not all(
            isinstance(entity_id, str) for entity_id in raw_entity_ids
        ):
            raise ValueError("command entity_ids must be a list of strings")
        entity_ids = tuple(raw_entity_ids)
        if command_type == "move":
            target = _point_from_data(raw_data.get("target"))
            return MoveCommand(entity_ids, target)
        title = raw_data.get("title", "Patrol Selected Area")
        if not isinstance(title, str):
            raise ValueError("patrol title must be a string")
        return CreatePatrolCommand(entity_ids, target_from_dict(raw_data.get("target")), title)
    automation_id = raw_data.get("automation_id")
    if not isinstance(automation_id, str) or not automation_id:
        raise ValueError("automation_id must be a non-empty string")
    if command_type == "pause_automation":
        return PauseAutomationCommand(automation_id)
    if command_type == "resume_automation":
        return ResumeAutomationCommand(automation_id)
    if command_type == "cancel_automation":
        return CancelAutomationCommand(automation_id)
    raise ValueError(f"unsupported command type: {command_type}")


def _point_from_data(raw_data: object) -> Point:
    if not isinstance(raw_data, list) or len(raw_data) != 2:
        raise ValueError("move target must contain two numbers")
    if any(isinstance(value, bool) or not isinstance(value, int | float) for value in raw_data):
        raise ValueError("move target must contain two numbers")
    return Point(float(raw_data[0]), float(raw_data[1]))
