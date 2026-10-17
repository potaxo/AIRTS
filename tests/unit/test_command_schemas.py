"""Focused tests for command schemas and serialization."""

from __future__ import annotations

import pytest

from airts.contracts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    Command,
    CreateConstructionCommand,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    CreateSpatialReferenceCommand,
    DeleteRegionCommand,
    DeleteSpatialReferenceCommand,
    EditSpatialReferenceCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    ReduceProductionCommand,
    RemoveEntityCommand,
    RenameRegionCommand,
    RepairBuildingCommand,
    ResumeAutomationCommand,
    SetAIIncomeMultiplierCommand,
    SetDeveloperModeCommand,
    SetSelectionCommand,
    StopCommand,
    command_from_dict,
    command_to_dict,
)
from airts.contracts.spatial import Point, PointTarget, PolylineTarget
from airts.world.units.catalog import EntityKind


@pytest.mark.parametrize(
    "command",
    [
        SetAIIncomeMultiplierCommand(1),
        SetAIIncomeMultiplierCommand(5),
        SetDeveloperModeCommand(True),
        SetDeveloperModeCommand(False),
        MoveCommand(("unit",), Point(3, 4)),
        MoveCommand(("unit",), Point(3, 4), gathering=True),
        StopCommand(("unit",)),
        HoldPositionCommand(("unit",)),
        RemoveEntityCommand("unit", "DESTROYED"),
        CreatePatrolCommand(("unit",), PointTarget(Point(4, 4)), priority=4),
        CreatePatrolCommand(("unit",), PointTarget(Point(4, 4)), auto_repair=True),
        CreateDefendCommand(("unit",), PointTarget(Point(4, 4)), auto_repair=True),
        CreateProductionCommand("factory", EntityKind.SCOUT, 1, auto_repair=True),
        ModifyAutomationCommand("automation_001", auto_repair=False),
        CreateDefendCommand(
            ("unit",),
            PolylineTarget((Point(1, 1), Point(5, 1))),
            priority=3,
            gathering_point=True,
        ),
        CreateProductionCommand("factory", EntityKind.LIGHT_TANK, 3, Point(8, 8)),
        ReduceProductionCommand("factory", EntityKind.SCOUT),
        CreateReinforcementCommand(("reserve",), "automation_001", 2),
        CreateRepairAndReturnCommand(("unit",), 0.4, 7),
        PauseAutomationCommand("automation_001"),
        ResumeAutomationCommand("automation_001"),
        CancelAutomationCommand("automation_001"),
        AttackCommand(("unit",), "enemy"),
        RepairBuildingCommand(("builder",), "factory"),
        CreateConstructionCommand(
            "builder",
            EntityKind.FACTORY,
            Point(6, 6),
            builder_ids=("builder", "builder_2"),
            queued=True,
        ),
        CreateProductionBatchCommand("factory", ((EntityKind.SCOUT, 2), (EntityKind.BUILDER, 1))),
        CreateEconomyCommand(("generator",), 1000),
        CreateSpatialReferenceCommand(PointTarget(Point(3, 4)), "North"),
        EditSpatialReferenceCommand("point_001", PointTarget(Point(5, 6))),
        RenameRegionCommand("region_001", "Bridge"),
        DeleteRegionCommand("region_001"),
        DeleteSpatialReferenceCommand("route_001"),
        SetSelectionCommand(("unit",), ("point_001",), ("route_001",), ("region_001",)),
        ModifyAutomationCommand(
            "automation_001", title="Watch bridge", priority=3, minimum_units=4
        ),
    ],
    ids=lambda command: type(command).__name__,
)
def test_command_schemas_round_trip(command: Command) -> None:
    assert command_from_dict(command_to_dict(command)) == command


@pytest.mark.parametrize(
    ("data", "message"),
    [
        pytest.param(
            {"type": "set_ai_income_multiplier", "multiplier": True},
            "multiplier must be an integer",
            id="ai-income-boolean",
        ),
        pytest.param(
            {"type": "set_developer_mode", "enabled": 1},
            "enabled must be a boolean",
            id="developer-mode-non-boolean",
        ),
        pytest.param(
            {"type": "set_developer_mode"},
            "enabled must be a boolean",
            id="developer-mode-missing-enabled",
        ),
        pytest.param(
            {"type": "move", "entity_ids": ["unit"], "target": [3, 4], "gathering": 1},
            "gathering must be a boolean",
            id="malformed-move-gathering",
        ),
        pytest.param(
            {"type": "unsupported", "automation_id": "x"},
            "unsupported command type",
            id="unknown-tag",
        ),
        pytest.param(
            {
                "type": "create_production",
                "factory_id": "factory",
                "unit_kind": "scout",
                "target_count": "three",
            },
            "target_count must be an integer",
            id="malformed-count",
        ),
    ],
)
def test_command_schema_rejects_unknown_and_malformed_data(data: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        command_from_dict(data)


@pytest.mark.parametrize(
    "command",
    [
        CreatePatrolCommand(("unit",), PointTarget(Point(4, 4))),
        CreateDefendCommand(("unit",), PointTarget(Point(4, 4))),
        CreateProductionCommand("factory", EntityKind.SCOUT, 1),
        ModifyAutomationCommand("automation_001", auto_repair=True),
    ],
)
def test_auto_repair_commands_reject_non_boolean_choices(command: Command) -> None:
    data = command_to_dict(command)
    data["auto_repair"] = "false"
    with pytest.raises(ValueError, match="auto_repair must be a boolean"):
        command_from_dict(data)
