"""Deterministic structured event recording."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class EventType(StrEnum):
    COMMAND_ACCEPTED = "command_accepted"
    COMMAND_REJECTED = "command_rejected"
    VALIDATION_FAILED = "validation_failed"
    MOVEMENT_STARTED = "movement_started"
    MOVEMENT_COMPLETED = "movement_completed"
    MOVEMENT_FAILED = "movement_failed"
    MOVEMENT_BLOCKED = "movement_blocked"
    PATH_COMPUTED = "path_computed"
    PATHFINDING_FAILED = "pathfinding_failed"
    AUTOMATION_CREATED = "automation_created"
    AUTOMATION_STATE_CHANGED = "automation_state_changed"
    ASSIGNMENT_CHANGED = "assignment_changed"
    ENTITY_REMOVED = "entity_removed"
    MANUAL_OVERRIDE = "manual_override"
    PRODUCTION_STARTED = "production_started"
    PRODUCTION_COMPLETED = "production_completed"
    REPAIR_STARTED = "repair_started"
    REPAIR_COMPLETED = "repair_completed"
    VISIBILITY_CHANGED = "visibility_changed"


@dataclass(frozen=True, slots=True)
class Event:
    sequence: int
    tick: int
    event_type: EventType
    subject_id: str | None
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "tick": self.tick,
            "type": self.event_type.value,
            "subject_id": self.subject_id,
            "details": self.details,
        }


class EventLog:
    def __init__(self) -> None:
        self._events: list[Event] = []

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def record(
        self,
        tick: int,
        event_type: EventType,
        subject_id: str | None = None,
        **details: object,
    ) -> Event:
        event = Event(len(self._events) + 1, tick, event_type, subject_id, details)
        self._events.append(event)
        return event

    def write_jsonl(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as stream:
            for event in self._events:
                stream.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

    def restore(self, events: list[Event]) -> None:
        expected = list(range(1, len(events) + 1))
        if [event.sequence for event in events] != expected:
            raise ValueError("event sequences must be contiguous and start at one")
        self._events = list(events)
