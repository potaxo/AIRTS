"""Focused tests for command schemas and serialization."""

from __future__ import annotations

import pytest

from airts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    Command,
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    HoldPositionCommand,
    MoveCommand,
    PauseAutomationCommand,
    RemoveEntityCommand,
    ResumeAutomationCommand,
    StopCommand,
    command_from_dict,
    command_to_dict,
)
from airts.geometry import Point, PointTarget, PolylineTarget
from airts.map_model import EntityKind


@pytest.mark.parametrize(
    "command",
    [
        MoveCommand(("unit",), Point(3, 4)),
        StopCommand(("unit",)),
        HoldPositionCommand(("unit",)),
        RemoveEntityCommand("unit", "DESTROYED"),
        CreatePatrolCommand(("unit",), PointTarget(Point(4, 4)), priority=4),
        CreateDefendCommand(
            ("unit",),
            PolylineTarget((Point(1, 1), Point(5, 1))),
            priority=3,
            gathering_point=True,
        ),
        CreateProductionCommand("factory", EntityKind.LIGHT_TANK, 3, Point(8, 8)),
        CreateReinforcementCommand(("reserve",), "automation_001", 2),
        CreateRepairAndReturnCommand(("unit",), 0.4, 7),
        PauseAutomationCommand("automation_001"),
        ResumeAutomationCommand("automation_001"),
        CancelAutomationCommand("automation_001"),
        AttackCommand(("unit",), "enemy"),
    ],
)
def test_command_schemas_round_trip(command: Command) -> None:
    assert command_from_dict(command_to_dict(command)) == command


def test_command_schema_rejects_unknown_and_malformed_data() -> None:
    with pytest.raises(ValueError, match="unsupported command type"):
        command_from_dict({"type": "unsupported", "automation_id": "x"})
    with pytest.raises(ValueError, match="target_count must be an integer"):
        command_from_dict(
            {
                "type": "create_production",
                "factory_id": "factory",
                "unit_kind": "scout",
                "target_count": "three",
            }
        )
