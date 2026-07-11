"""Tagged, serializable command schemas shared by every control source."""

from __future__ import annotations

from dataclasses import dataclass

from airts.geometry import Point, SpatialTarget, target_from_dict, target_to_dict
from airts.map_model import EntityKind


@dataclass(frozen=True, slots=True)
class MoveCommand:
    entity_ids: tuple[str, ...]
    target: Point
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class StopCommand:
    entity_ids: tuple[str, ...]
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class HoldPositionCommand:
    entity_ids: tuple[str, ...]
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class RemoveEntityCommand:
    """Authoritative system input for deterministic entity removal and replay."""

    entity_id: str
    reason: str = "ENTITY_REMOVED"


@dataclass(frozen=True, slots=True)
class CreatePatrolCommand:
    entity_ids: tuple[str, ...]
    target: SpatialTarget
    title: str = "Patrol Selected Area"
    priority: int = 0
    owner_id: str = "player"
    original_instruction: str = ""


@dataclass(frozen=True, slots=True)
class CreateDefendCommand:
    entity_ids: tuple[str, ...]
    target: SpatialTarget
    title: str = "Defend Selected Area"
    priority: int = 0
    owner_id: str = "player"
    original_instruction: str = ""


@dataclass(frozen=True, slots=True)
class CreateProductionCommand:
    factory_id: str
    unit_kind: EntityKind
    target_count: int
    rally_point: Point | None = None
    title: str = "Produce Reinforcements"
    priority: int = 0
    owner_id: str = "player"
    original_instruction: str = ""


@dataclass(frozen=True, slots=True)
class CreateReinforcementCommand:
    candidate_entity_ids: tuple[str, ...]
    target_automation_id: str
    minimum_units: int
    title: str = "Reinforce Assignment"
    priority: int = 0
    owner_id: str = "player"
    original_instruction: str = ""


@dataclass(frozen=True, slots=True)
class CreateRepairAndReturnCommand:
    entity_ids: tuple[str, ...]
    health_threshold: float = 1.0
    repair_rate: int = 5
    title: str = "Repair And Return"
    priority: int = 100
    owner_id: str = "player"
    original_instruction: str = ""


@dataclass(frozen=True, slots=True)
class PauseAutomationCommand:
    automation_id: str
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class ResumeAutomationCommand:
    automation_id: str
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class CancelAutomationCommand:
    automation_id: str
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class CreateSpatialReferenceCommand:
    target: SpatialTarget
    name: str | None = None
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class EditSpatialReferenceCommand:
    reference_id: str
    target: SpatialTarget
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class RenameRegionCommand:
    reference_id: str
    name: str
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class SetSelectionCommand:
    entity_ids: tuple[str, ...] = ()
    point_ids: tuple[str, ...] = ()
    route_ids: tuple[str, ...] = ()
    region_ids: tuple[str, ...] = ()
    owner_id: str = "player"


@dataclass(frozen=True, slots=True)
class ModifyAutomationCommand:
    automation_id: str
    title: str | None = None
    priority: int | None = None
    target: SpatialTarget | None = None
    minimum_units: int | None = None
    target_count: int | None = None
    owner_id: str = "player"


Command = (
    MoveCommand
    | StopCommand
    | HoldPositionCommand
    | RemoveEntityCommand
    | CreatePatrolCommand
    | CreateDefendCommand
    | CreateProductionCommand
    | CreateReinforcementCommand
    | CreateRepairAndReturnCommand
    | PauseAutomationCommand
    | ResumeAutomationCommand
    | CancelAutomationCommand
    | CreateSpatialReferenceCommand
    | EditSpatialReferenceCommand
    | RenameRegionCommand
    | SetSelectionCommand
    | ModifyAutomationCommand
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    accepted: bool
    reason: str
    automation_id: str | None = None
    reference_id: str | None = None


def command_to_dict(command: Command) -> dict[str, object]:
    if isinstance(command, CreateSpatialReferenceCommand):
        return {
            "type": "create_spatial_reference",
            "target": target_to_dict(command.target),
            "name": command.name,
            "owner_id": command.owner_id,
        }
    if isinstance(command, EditSpatialReferenceCommand):
        return {
            "type": "edit_spatial_reference",
            "reference_id": command.reference_id,
            "target": target_to_dict(command.target),
            "owner_id": command.owner_id,
        }
    if isinstance(command, RenameRegionCommand):
        return {
            "type": "rename_region",
            "reference_id": command.reference_id,
            "name": command.name,
            "owner_id": command.owner_id,
        }
    if isinstance(command, SetSelectionCommand):
        return {
            "type": "set_selection",
            "entity_ids": list(command.entity_ids),
            "point_ids": list(command.point_ids),
            "route_ids": list(command.route_ids),
            "region_ids": list(command.region_ids),
            "owner_id": command.owner_id,
        }
    if isinstance(command, ModifyAutomationCommand):
        return {
            "type": "modify_automation",
            "automation_id": command.automation_id,
            "title": command.title,
            "priority": command.priority,
            "target": None if command.target is None else target_to_dict(command.target),
            "minimum_units": command.minimum_units,
            "target_count": command.target_count,
            "owner_id": command.owner_id,
        }
    if isinstance(command, MoveCommand):
        return {
            "type": "move",
            "entity_ids": list(command.entity_ids),
            "target": [command.target.x, command.target.y],
            "owner_id": command.owner_id,
        }
    if isinstance(command, StopCommand | HoldPositionCommand):
        return {
            "type": "stop" if isinstance(command, StopCommand) else "hold_position",
            "entity_ids": list(command.entity_ids),
            "owner_id": command.owner_id,
        }
    if isinstance(command, RemoveEntityCommand):
        return {
            "type": "remove_entity",
            "entity_id": command.entity_id,
            "reason": command.reason,
        }
    if isinstance(command, CreatePatrolCommand | CreateDefendCommand):
        return {
            "type": (
                "create_patrol" if isinstance(command, CreatePatrolCommand) else "create_defend"
            ),
            "entity_ids": list(command.entity_ids),
            "target": target_to_dict(command.target),
            "title": command.title,
            "priority": command.priority,
            "owner_id": command.owner_id,
            "original_instruction": command.original_instruction,
        }
    if isinstance(command, CreateProductionCommand):
        return {
            "type": "create_production",
            "factory_id": command.factory_id,
            "unit_kind": command.unit_kind.value,
            "target_count": command.target_count,
            "rally_point": (
                None
                if command.rally_point is None
                else [command.rally_point.x, command.rally_point.y]
            ),
            "title": command.title,
            "priority": command.priority,
            "owner_id": command.owner_id,
            "original_instruction": command.original_instruction,
        }
    if isinstance(command, CreateReinforcementCommand):
        return {
            "type": "create_reinforcement",
            "candidate_entity_ids": list(command.candidate_entity_ids),
            "target_automation_id": command.target_automation_id,
            "minimum_units": command.minimum_units,
            "title": command.title,
            "priority": command.priority,
            "owner_id": command.owner_id,
            "original_instruction": command.original_instruction,
        }
    if isinstance(command, CreateRepairAndReturnCommand):
        return {
            "type": "create_repair_and_return",
            "entity_ids": list(command.entity_ids),
            "health_threshold": command.health_threshold,
            "repair_rate": command.repair_rate,
            "title": command.title,
            "priority": command.priority,
            "owner_id": command.owner_id,
            "original_instruction": command.original_instruction,
        }
    if isinstance(command, PauseAutomationCommand):
        command_type = "pause_automation"
    elif isinstance(command, ResumeAutomationCommand):
        command_type = "resume_automation"
    else:
        command_type = "cancel_automation"
    return {
        "type": command_type,
        "automation_id": command.automation_id,
        "owner_id": command.owner_id,
    }


def command_from_dict(raw_data: object) -> Command:
    data = _mapping(raw_data, "command")
    command_type = data.get("type")
    owner_id = _string(data.get("owner_id", "player"), "owner_id")
    if command_type == "create_spatial_reference":
        name = data.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError("name must be a string or null")
        return CreateSpatialReferenceCommand(target_from_dict(data.get("target")), name, owner_id)
    if command_type == "edit_spatial_reference":
        return EditSpatialReferenceCommand(
            _string(data.get("reference_id"), "reference_id"),
            target_from_dict(data.get("target")),
            owner_id,
        )
    if command_type == "rename_region":
        return RenameRegionCommand(
            _string(data.get("reference_id"), "reference_id"),
            _string(data.get("name"), "name"),
            owner_id,
        )
    if command_type == "set_selection":
        return SetSelectionCommand(
            _string_ids(data.get("entity_ids"), "entity_ids"),
            _string_ids(data.get("point_ids"), "point_ids"),
            _string_ids(data.get("route_ids"), "route_ids"),
            _string_ids(data.get("region_ids"), "region_ids"),
            owner_id,
        )
    if command_type == "modify_automation":
        target = data.get("target")
        return ModifyAutomationCommand(
            _string(data.get("automation_id"), "automation_id"),
            _nullable_string(data.get("title"), "title"),
            _nullable_integer(data.get("priority"), "priority"),
            None if target is None else target_from_dict(target),
            _nullable_integer(data.get("minimum_units"), "minimum_units"),
            _nullable_integer(data.get("target_count"), "target_count"),
            owner_id,
        )
    if command_type == "move":
        return MoveCommand(
            _entity_ids(data.get("entity_ids")), _point(data.get("target"), "target"), owner_id
        )
    if command_type in {"stop", "hold_position"}:
        entity_ids = _entity_ids(data.get("entity_ids"))
        return (
            StopCommand(entity_ids, owner_id)
            if command_type == "stop"
            else HoldPositionCommand(entity_ids, owner_id)
        )
    if command_type == "remove_entity":
        return RemoveEntityCommand(
            _string(data.get("entity_id"), "entity_id"),
            _string(data.get("reason", "ENTITY_REMOVED"), "reason"),
        )
    if command_type in {"create_patrol", "create_defend"}:
        entity_ids = _entity_ids(data.get("entity_ids"))
        target = target_from_dict(data.get("target"))
        common = _automation_common(data, owner_id)
        if command_type == "create_patrol":
            return CreatePatrolCommand(entity_ids, target, *common)
        return CreateDefendCommand(entity_ids, target, *common)
    if command_type == "create_production":
        try:
            unit_kind = EntityKind(_string(data.get("unit_kind"), "unit_kind"))
        except ValueError as error:
            raise ValueError(f"unsupported unit_kind: {error}") from error
        rally_data = data.get("rally_point")
        rally_point = None if rally_data is None else _point(rally_data, "rally_point")
        return CreateProductionCommand(
            factory_id=_string(data.get("factory_id"), "factory_id"),
            unit_kind=unit_kind,
            target_count=_integer(data.get("target_count"), "target_count"),
            rally_point=rally_point,
            title=_string(data.get("title", "Produce Reinforcements"), "title"),
            priority=_integer(data.get("priority", 0), "priority"),
            owner_id=owner_id,
            original_instruction=_optional_string(data.get("original_instruction", "")),
        )
    if command_type == "create_reinforcement":
        return CreateReinforcementCommand(
            candidate_entity_ids=_entity_ids(data.get("candidate_entity_ids")),
            target_automation_id=_string(data.get("target_automation_id"), "target_automation_id"),
            minimum_units=_integer(data.get("minimum_units"), "minimum_units"),
            title=_string(data.get("title", "Reinforce Assignment"), "title"),
            priority=_integer(data.get("priority", 0), "priority"),
            owner_id=owner_id,
            original_instruction=_optional_string(data.get("original_instruction", "")),
        )
    if command_type == "create_repair_and_return":
        return CreateRepairAndReturnCommand(
            entity_ids=_entity_ids(data.get("entity_ids")),
            health_threshold=_number(data.get("health_threshold", 1.0), "health_threshold"),
            repair_rate=_integer(data.get("repair_rate", 5), "repair_rate"),
            title=_string(data.get("title", "Repair And Return"), "title"),
            priority=_integer(data.get("priority", 100), "priority"),
            owner_id=owner_id,
            original_instruction=_optional_string(data.get("original_instruction", "")),
        )
    automation_id = _string(data.get("automation_id"), "automation_id")
    if command_type == "pause_automation":
        return PauseAutomationCommand(automation_id, owner_id)
    if command_type == "resume_automation":
        return ResumeAutomationCommand(automation_id, owner_id)
    if command_type == "cancel_automation":
        return CancelAutomationCommand(automation_id, owner_id)
    raise ValueError(f"unsupported command type: {command_type}")


def _automation_common(data: dict[str, object], owner_id: str) -> tuple[str, int, str, str]:
    return (
        _string(data.get("title", "Automation"), "title"),
        _integer(data.get("priority", 0), "priority"),
        owner_id,
        _optional_string(data.get("original_instruction", "")),
    )


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return value


def _entity_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("entity IDs must be a list of strings")
    return tuple(value)


def _string_ids(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return tuple(value)


def _nullable_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    return value


def _nullable_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field)


def _point(value: object, field: str) -> Point:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field} must contain two numbers")
    return Point(_number(value[0], f"{field}.x"), _number(value[1], f"{field}.y"))


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("original_instruction must be a string")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be a number")
    return float(value)
