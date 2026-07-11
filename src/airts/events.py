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
    MOVEMENT_STOPPED = "movement_stopped"
    MOVEMENT_YIELDED = "movement_yielded"
    UNIT_PUSHED = "unit_pushed"
    PATH_COMPUTED = "path_computed"
    PATHFINDING_FAILED = "pathfinding_failed"
    AUTOMATION_CREATED = "automation_created"
    AUTOMATION_STATE_CHANGED = "automation_state_changed"
    ASSIGNMENT_CHANGED = "assignment_changed"
    ENTITY_REMOVED = "entity_removed"
    MANUAL_OVERRIDE = "manual_override"
    PRODUCTION_STARTED = "production_started"
    PRODUCTION_COMPLETED = "production_completed"
    ENEMY_REINFORCEMENT_SPAWNED = "enemy_reinforcement_spawned"
    REPAIR_STARTED = "repair_started"
    REPAIR_COMPLETED = "repair_completed"
    VISIBILITY_CHANGED = "visibility_changed"
    SPATIAL_REFERENCE_CREATED = "spatial_reference_created"
    SPATIAL_REFERENCE_EDITED = "spatial_reference_edited"
    SPATIAL_REFERENCE_NAMED = "spatial_reference_named"
    SPATIAL_REFERENCE_DELETED = "spatial_reference_deleted"
    SELECTION_CHANGED = "selection_changed"
    AUTOMATION_MODIFIED = "automation_modified"
    RESOURCE_CHANGED = "resource_changed"
    COMBAT_ATTACK = "combat_attack"
    DEFEND_ENGAGED = "defend_engaged"
    DEFEND_RETURNED = "defend_returned"
    PROJECTILE_LAUNCHED = "projectile_launched"
    PROJECTILE_IMPACT = "projectile_impact"
    ENTITY_DESTROYED = "entity_destroyed"
    RETREAT_STARTED = "retreat_started"


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

    def query(
        self,
        *,
        event_types: frozenset[EventType] | None = None,
        subject_id: str | None = None,
        since_tick: int | None = None,
        limit: int | None = None,
    ) -> tuple[Event, ...]:
        """Return matching events newest-first, suitable for an inspector."""

        matches = (
            event
            for event in reversed(self._events)
            if (event_types is None or event.event_type in event_types)
            and (subject_id is None or event.subject_id == subject_id)
            and (since_tick is None or event.tick >= since_tick)
        )
        if limit is None:
            return tuple(matches)
        if limit < 0:
            raise ValueError("event query limit cannot be negative")
        result: list[Event] = []
        for event in matches:
            if len(result) == limit:
                break
            result.append(event)
        return tuple(result)
