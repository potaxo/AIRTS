"""Deterministic command replay recording, loading, and verification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from airts.commands import Command, command_from_dict, command_to_dict
from airts.map_model import GameMap, MapValidationError, load_map_data
from airts.simulation import Simulation

REPLAY_SCHEMA = "airts-replay-v3"


class ReplayError(ValueError):
    """Raised when replay data is invalid or does not reproduce its recorded result."""


@dataclass(frozen=True, slots=True)
class RecordedCommand:
    tick: int
    command: Command

    def to_dict(self) -> dict[str, object]:
        return {"tick": self.tick, "command": command_to_dict(self.command)}


@dataclass(frozen=True, slots=True)
class ReplayData:
    game_map: GameMap
    random_seed: int
    commands: tuple[RecordedCommand, ...]
    final_tick: int
    expected_snapshot: dict[str, object]
    expected_events: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": REPLAY_SCHEMA,
            "airts_version": "0.1.0",
            "map": self.game_map.to_dict(),
            "random_seed": self.random_seed,
            "commands": [record.to_dict() for record in self.commands],
            "final_tick": self.final_tick,
            "expected_snapshot": self.expected_snapshot,
            "expected_events": list(self.expected_events),
        }


def capture_replay(simulation: Simulation) -> ReplayData:
    commands = tuple(
        RecordedCommand(
            tick=_integer(entry.get("tick"), "recorded command tick", minimum=0),
            command=_command(entry.get("command")),
        )
        for entry in simulation.command_history
    )
    return ReplayData(
        game_map=simulation.game_map,
        random_seed=simulation.random_seed,
        commands=commands,
        final_tick=simulation.tick,
        expected_snapshot=simulation.snapshot(),
        expected_events=tuple(event.to_dict() for event in simulation.events.events),
    )


def save_replay(simulation: Simulation, path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(capture_replay(simulation).to_dict(), stream, indent=2, sort_keys=True)
        stream.write("\n")


def load_replay(path: str | Path) -> ReplayData:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return load_replay_data(json.load(stream))
    except json.JSONDecodeError as error:
        raise ReplayError(f"invalid replay JSON: {error.msg}") from error


def load_replay_data(raw_data: object) -> ReplayData:
    document = _mapping(raw_data, "replay document")
    if document.get("schema") != REPLAY_SCHEMA:
        raise ReplayError(f"unsupported replay schema: {document.get('schema')}")
    try:
        game_map = load_map_data(document.get("map"))
    except MapValidationError as error:
        raise ReplayError(f"invalid replay map: {error}") from error
    random_seed = _integer(document.get("random_seed"), "random_seed")
    final_tick = _integer(document.get("final_tick"), "final_tick", minimum=0)
    commands: list[RecordedCommand] = []
    previous_tick = 0
    for raw_record in _list(document.get("commands"), "commands"):
        record = _mapping(raw_record, "recorded command")
        tick = _integer(record.get("tick"), "recorded command tick", minimum=0)
        if tick < previous_tick or tick > final_tick:
            raise ReplayError("recorded command ticks must be ordered and not after final_tick")
        previous_tick = tick
        commands.append(RecordedCommand(tick, _command(record.get("command"))))
    expected_snapshot = _mapping(document.get("expected_snapshot"), "expected_snapshot")
    expected_events = tuple(
        _mapping(event, "expected event")
        for event in _list(document.get("expected_events"), "expected_events")
    )
    return ReplayData(
        game_map,
        random_seed,
        tuple(commands),
        final_tick,
        expected_snapshot,
        expected_events,
    )


def run_replay(data: ReplayData, *, verify: bool = True) -> Simulation:
    simulation = Simulation(data.game_map, data.random_seed)
    for record in data.commands:
        simulation.advance(record.tick - simulation.tick)
        simulation.execute(record.command)
    simulation.advance(data.final_tick - simulation.tick)
    if verify and simulation.snapshot() != data.expected_snapshot:
        raise ReplayError("replayed final state does not match the recorded snapshot")
    actual_events = tuple(event.to_dict() for event in simulation.events.events)
    if verify and actual_events != data.expected_events:
        raise ReplayError("replayed events do not match the recorded event stream")
    return simulation


def _command(raw_data: object) -> Command:
    try:
        return command_from_dict(raw_data)
    except ValueError as error:
        raise ReplayError(f"invalid recorded command: {error}") from error


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReplayError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ReplayError(f"{field} must be a list")
    return value


def _integer(value: object, field: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ReplayError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ReplayError(f"{field} must be at least {minimum}")
    return value
