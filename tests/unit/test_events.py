"""Focused tests for event logging and serialization."""

from __future__ import annotations

import json
from pathlib import Path

from airts.events import EventLog, EventType


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
