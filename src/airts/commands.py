"""Shared command interface used by all Phase 1 control sources."""

from __future__ import annotations

from dataclasses import dataclass

from airts.geometry import Point, SpatialTarget


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
