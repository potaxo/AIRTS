"""Focused tests for event logging and serialization."""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest

from airts.contracts.events import Event, EventLog, EventType


def test_event_log_has_stable_sequence_and_writes_json_lines(tmp_path: Path) -> None:
    event_log = EventLog()
    event_log.record(3, EventType.COMMAND_ACCEPTED, "unit_01", command="move")
    event_log.record(4, EventType.MOVEMENT_COMPLETED, "unit_01", position=[2, 3])
    destination = tmp_path / "events.jsonl"

    event_log.write_jsonl(destination)
    records = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]

    assert [record["sequence"] for record in records] == [1, 2]
    assert records[0] == {
        "details": {"command": "move"},
        "sequence": 1,
        "subject_id": "unit_01",
        "tick": 3,
        "type": "command_accepted",
    }


def test_event_query_filters_newest_first_and_validates_limit() -> None:
    events = EventLog()
    events.record(1, EventType.COMMAND_ACCEPTED, "one")
    events.record(2, EventType.AUTOMATION_MODIFIED, "two", reason="EDITED")
    events.record(3, EventType.AUTOMATION_MODIFIED, "two", reason="EDITED_AGAIN")

    result = events.query(
        event_types=frozenset({EventType.AUTOMATION_MODIFIED}), subject_id="two", limit=1
    )
    assert [event.tick for event in result] == [3]
    with pytest.raises(ValueError, match="negative"):
        events.query(limit=-1)


def test_path_events_retain_full_json_payload_with_bounded_live_allocation(tmp_path: Path) -> None:
    cells = tuple((index, index + 1) for index in range(10_000))
    events = EventLog()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        event = events.record_path(1, "unit", source="human", cost=9999.0, cells=cells)
        retained = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert retained < len(cells) * 32
    expected = {"source": "human", "cost": 9999.0, "cells": [list(cell) for cell in cells]}
    assert event.details == expected
    # Reading/editing an inspector payload cannot mutate retained path history.
    event.details["cells"].clear()
    assert event.details == expected
    destination = tmp_path / "path-events.jsonl"
    events.write_jsonl(destination)
    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert saved["details"] == expected
    restored = Event(1, 1, EventType.PATH_COMPUTED, "unit", saved["details"])
    assert restored.to_dict() == event.to_dict()
