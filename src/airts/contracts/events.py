"""Deterministic structured event recording."""

from __future__ import annotations

import json
from array import array
from dataclasses import dataclass
from enum import StrEnum
from itertools import chain
from pathlib import Path


class EventType(StrEnum):
    UPGRADE_STARTED = "upgrade_started"
    UPGRADE_COMPLETED = "upgrade_completed"
    UPGRADE_CANCELED = "upgrade_canceled"
    COMBAT_SUPPORT_JOINED = "combat_support_joined"
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
    PATROL_ENGAGED = "patrol_engaged"
    PATROL_RETURNED = "patrol_returned"
    PROJECTILE_LAUNCHED = "projectile_launched"
    PROJECTILE_IMPACT = "projectile_impact"
    ENTITY_DESTROYED = "entity_destroyed"
    RETREAT_STARTED = "retreat_started"


@dataclass(frozen=True, slots=True, init=False)
class Event:
    sequence: int
    tick: int
    event_type: EventType
    subject_id: str | None
    _details: dict[str, object]
    _path_cells: bytes | None

    def __init__(
        self,
        sequence: int,
        tick: int,
        event_type: EventType,
        subject_id: str | None,
        details: dict[str, object],
    ) -> None:
        packed = None
        cells = details.get("cells")
        if (
            event_type is EventType.PATH_COMPUTED
            and isinstance(cells, array)
            and cells.typecode == "q"
        ):
            packed = cells.tobytes()
        elif event_type is EventType.PATH_COMPUTED and isinstance(cells, list | tuple):
            # Event payloads are otherwise generic. Compact ordinary integer cell
            # records, retaining other payloads unchanged for the event codec.
            coordinates = array("q")
            for cell in cells:
                if not isinstance(cell, list | tuple) or len(cell) != 2:
                    break
                if not all(
                    type(value) is int and -(1 << 63) <= value < (1 << 63) for value in cell
                ):
                    break
                coordinates.extend(cell)
            else:
                packed = coordinates.tobytes()
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "tick", tick)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "subject_id", subject_id)
        object.__setattr__(self, "_path_cells", packed)
        object.__setattr__(
            self,
            "_details",
            {key: value for key, value in details.items() if packed is None or key != "cells"},
        )

    @property
    def details(self) -> dict[str, object]:
        """Expose the original payload; unpack complete paths only when inspected/exported."""
        if self._path_cells is None:
            return self._details
        coordinates = array("q")
        coordinates.frombytes(self._path_cells)
        pairs = iter(coordinates)
        return self._details | {"cells": [[x, next(pairs)] for x in pairs]}

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

    def record_path(
        self,
        tick: int,
        subject_id: str,
        *,
        source: str,
        cost: float,
        cells: tuple[tuple[int, int], ...],
    ) -> Event:
        return self.record(
            tick,
            EventType.PATH_COMPUTED,
            subject_id,
            source=source,
            cost=cost,
            cells=array("q", chain.from_iterable(cells)),
        )

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
