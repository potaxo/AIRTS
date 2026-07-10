"""Versioned JSON save/load support for complete simulation state."""

from __future__ import annotations

import json
from pathlib import Path

from airts.automations import AutomationStatus, PatrolAutomation
from airts.commands import command_from_dict, command_to_dict
from airts.entities import Entity, UnitState
from airts.events import Event, EventLog, EventType
from airts.geometry import Point, target_from_dict
from airts.map_model import (
    EntityCategory,
    EntityKind,
    GameMap,
    MapValidationError,
    load_map_data,
)
from airts.occupancy import OccupancyError, OccupancyGrid
from airts.simulation import Simulation
from airts.visibility import PlayerVisibility, VisibilitySystem

SAVE_SCHEMA = "airts-save-v1"


class PersistenceError(ValueError):
    """Raised when saved state is malformed or incompatible."""


def save_simulation(simulation: Simulation, path: str | Path) -> None:
    document = {
        "schema": SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": simulation.export_state(),
    }
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")


def load_simulation(path: str | Path) -> Simulation:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return load_simulation_data(json.load(stream))
    except json.JSONDecodeError as error:
        raise PersistenceError(f"invalid save JSON: {error.msg}") from error


def load_simulation_data(raw_data: object) -> Simulation:
    document = _mapping(raw_data, "save document")
    if document.get("schema") != SAVE_SCHEMA:
        raise PersistenceError(f"unsupported save schema: {document.get('schema')}")
    try:
        game_map = load_map_data(document.get("map"))
    except MapValidationError as error:
        raise PersistenceError(f"invalid saved map: {error}") from error
    state = _mapping(document.get("state"), "state")
    tick = _integer(state.get("tick"), "state.tick", minimum=0)
    random_seed = _integer(state.get("random_seed"), "state.random_seed")
    simulation = Simulation(game_map, random_seed)
    simulation.tick = tick
    simulation.entities = _load_entities(state.get("entities"), game_map)
    simulation.occupancy = _build_occupancy(simulation)
    simulation.automations = _load_automations(state.get("automations"), simulation)
    simulation.assignments = _load_assignments(state.get("assignments"), simulation)
    _validate_assignment_completeness(simulation)
    simulation.visibility = _load_visibility(state.get("visibility"), simulation)
    event_log = EventLog()
    try:
        event_log.restore(_load_events(state.get("events"), tick))
    except ValueError as error:
        raise PersistenceError(f"invalid saved event sequence: {error}") from error
    simulation.events = event_log
    simulation._command_history = _load_command_history(state.get("command_history"), tick)
    simulation._next_automation_number = _integer(
        state.get("next_automation_number"), "state.next_automation_number", minimum=1
    )
    generated_numbers = [
        int(automation_id.removeprefix("patrol_"))
        for automation_id in simulation.automations
        if automation_id.startswith("patrol_") and automation_id.removeprefix("patrol_").isdigit()
    ]
    if generated_numbers and simulation._next_automation_number <= max(generated_numbers):
        raise PersistenceError("next_automation_number would overwrite an existing automation")
    simulation._movement_blocked = _string_set(
        state.get("movement_blocked", []), "state.movement_blocked"
    )
    if not simulation._movement_blocked.issubset(simulation.entities):
        raise PersistenceError("movement_blocked references an unknown entity")
    return simulation


def _load_entities(raw_data: object, game_map: GameMap) -> dict[str, Entity]:
    entities_data = _mapping(raw_data, "state.entities")
    entities: dict[str, Entity] = {}
    for entity_id, raw_entity in entities_data.items():
        entity = _mapping(raw_entity, f"state.entities.{entity_id}")
        if entity.get("id") != entity_id:
            raise PersistenceError(f"entity key and ID differ: {entity_id}")
        try:
            kind = EntityKind(_string(entity.get("kind"), "entity.kind"))
            state = UnitState(_string(entity.get("state"), "entity.state"))
        except ValueError as error:
            raise PersistenceError(f"invalid entity {entity_id}: {error}") from error
        position = _point(entity.get("position"), "entity.position")
        health = _integer(entity.get("health"), "entity.health", minimum=0)
        if health > kind.profile.max_health:
            raise PersistenceError(f"entity {entity_id} health exceeds its maximum")
        move_target_raw = entity.get("move_target")
        move_target = (
            None if move_target_raw is None else _point(move_target_raw, "entity.move_target")
        )
        path = [_point(item, "entity.path item") for item in _list(entity.get("path"), "path")]
        path_cost = _number(entity.get("path_cost"), "entity.path_cost", minimum=0.0)
        if kind.profile.category is EntityCategory.BUILDING and (
            path or move_target is not None or state is not UnitState.IDLE
        ):
            raise PersistenceError(f"building {entity_id} cannot have movement state")
        if kind.profile.category is EntityCategory.BUILDING and (
            not position.x.is_integer() or not position.y.is_integer()
        ):
            raise PersistenceError(f"building {entity_id} position must be grid-aligned")
        if not game_map.contains(position):
            raise PersistenceError(f"entity {entity_id} lies outside the saved map")
        if move_target is not None and not game_map.is_passable(move_target):
            raise PersistenceError(f"entity {entity_id} has an invalid movement target")
        if any(not game_map.is_passable(point) for point in path):
            raise PersistenceError(f"entity {entity_id} path crosses invalid terrain")
        entities[entity_id] = Entity(
            entity_id=entity_id,
            kind=kind,
            owner_id=_string(entity.get("owner"), "entity.owner"),
            position=position,
            health=health,
            state=state,
            move_target=move_target,
            path=path,
            path_cost=path_cost,
        )
    if not entities:
        raise PersistenceError("saved state must contain at least one entity")
    return entities


def _build_occupancy(simulation: Simulation) -> OccupancyGrid:
    occupancy = OccupancyGrid(simulation.game_map.width, simulation.game_map.height)
    for entity_id in sorted(simulation.entities):
        entity = simulation.entities[entity_id]
        if any(not simulation.game_map.is_cell_passable(cell) for cell in entity.occupied_cells):
            raise PersistenceError(f"entity {entity_id} occupies impassable terrain")
        try:
            occupancy.place(entity_id, entity.occupied_cells)
        except OccupancyError as error:
            raise PersistenceError(f"invalid saved occupancy: {error}") from error
    return occupancy


def _load_automations(raw_data: object, simulation: Simulation) -> dict[str, PatrolAutomation]:
    automations_data = _mapping(raw_data, "state.automations")
    automations: dict[str, PatrolAutomation] = {}
    for automation_id, raw_automation in automations_data.items():
        data = _mapping(raw_automation, f"automation {automation_id}")
        if data.get("id") != automation_id or data.get("template") != "patrol":
            raise PersistenceError(f"invalid patrol automation identity: {automation_id}")
        try:
            status = AutomationStatus(_string(data.get("status"), "automation.status"))
            target = target_from_dict(data.get("target"))
        except ValueError as error:
            raise PersistenceError(f"invalid automation {automation_id}: {error}") from error
        entity_ids = _string_list(data.get("entity_ids"), "automation.entity_ids")
        if any(
            entity_id not in simulation.entities or not simulation.entities[entity_id].is_movable
            for entity_id in entity_ids
        ):
            raise PersistenceError(f"automation {automation_id} references an invalid entity")
        waypoints = tuple(
            _point(item, "automation.waypoint")
            for item in _list(data.get("waypoints"), "automation.waypoints")
        )
        if not waypoints:
            raise PersistenceError(f"automation {automation_id} has no waypoints")
        raw_indices = _mapping(data.get("waypoint_indices"), "automation.waypoint_indices")
        if set(raw_indices) != set(entity_ids):
            raise PersistenceError(
                f"automation {automation_id} waypoint indices do not match its entities"
            )
        indices = {
            entity_id: _integer(raw_indices.get(entity_id), "waypoint index", minimum=0)
            for entity_id in entity_ids
        }
        if any(index >= len(waypoints) for index in indices.values()):
            raise PersistenceError(f"automation {automation_id} has an invalid waypoint index")
        automations[automation_id] = PatrolAutomation(
            automation_id=automation_id,
            title=_string(data.get("title"), "automation.title"),
            target=target,
            entity_ids=entity_ids,
            waypoints=waypoints,
            created_tick=_past_tick(
                data.get("created_tick"), "automation.created_tick", simulation.tick
            ),
            status=status,
            reason_code=_string(data.get("reason_code"), "automation.reason_code"),
            waypoint_indices=indices,
        )
    return automations


def _load_assignments(raw_data: object, simulation: Simulation) -> dict[str, str]:
    data = _mapping(raw_data, "state.assignments")
    assignments: dict[str, str] = {}
    for entity_id, automation_id in data.items():
        if not isinstance(automation_id, str):
            raise PersistenceError("assignment IDs must be strings")
        automation = simulation.automations.get(automation_id)
        if (
            entity_id not in simulation.entities
            or automation is None
            or entity_id not in automation.entity_ids
        ):
            raise PersistenceError(f"invalid assignment: {entity_id} -> {automation_id}")
        assignments[entity_id] = automation_id
    return assignments


def _validate_assignment_completeness(simulation: Simulation) -> None:
    for automation in simulation.automations.values():
        assigned = {
            entity_id
            for entity_id, automation_id in simulation.assignments.items()
            if automation_id == automation.automation_id
        }
        expected = set(automation.entity_ids)
        if automation.status is AutomationStatus.CANCELED:
            if assigned:
                raise PersistenceError("canceled automations cannot retain assignments")
        elif assigned != expected:
            raise PersistenceError(
                f"automation {automation.automation_id} assignments are incomplete"
            )


def _load_visibility(raw_data: object, simulation: Simulation) -> VisibilitySystem:
    data = _mapping(raw_data, "state.visibility")
    system = VisibilitySystem(simulation.game_map)
    for player_id, raw_player in data.items():
        player = _mapping(raw_player, f"visibility.{player_id}")
        visible = _cell_set(player.get("visible"), simulation, "visible")
        explored = _cell_set(player.get("explored"), simulation, "explored")
        if not visible.issubset(explored):
            raise PersistenceError("visible cells must also be explored")
        last_observed: dict[tuple[int, int], int] = {}
        for raw_entry in _list(player.get("last_observed_tick"), "last_observed_tick"):
            entry = _list(raw_entry, "last_observed_tick entry")
            if len(entry) != 3:
                raise PersistenceError("last_observed_tick entries must contain x, y, and tick")
            cell = (
                _integer(entry[0], "last observed x", minimum=0),
                _integer(entry[1], "last observed y", minimum=0),
            )
            if cell not in explored:
                raise PersistenceError("last observation references an unexplored cell")
            last_observed[cell] = _past_tick(entry[2], "last observed tick", simulation.tick)
        if set(last_observed) != explored:
            raise PersistenceError("every explored cell requires a last observation tick")
        system.players[player_id] = PlayerVisibility(
            simulation.game_map.width,
            simulation.game_map.height,
            visible,
            explored,
            last_observed,
        )
    owners = {entity.owner_id for entity in simulation.entities.values()}
    if not owners.issubset(system.players):
        raise PersistenceError("visibility state is missing an entity owner")
    return system


def _load_events(raw_data: object, current_tick: int) -> list[Event]:
    events: list[Event] = []
    for raw_event in _list(raw_data, "state.events"):
        data = _mapping(raw_event, "event")
        try:
            event_type = EventType(_string(data.get("type"), "event.type"))
        except ValueError as error:
            raise PersistenceError(f"invalid event type: {error}") from error
        subject_id = data.get("subject_id")
        if subject_id is not None and not isinstance(subject_id, str):
            raise PersistenceError("event subject_id must be a string or null")
        event_tick = _integer(data.get("tick"), "event.tick", minimum=0)
        if event_tick > current_tick:
            raise PersistenceError("event tick cannot be in the future")
        details = _mapping(data.get("details"), "event.details")
        events.append(
            Event(
                sequence=_integer(data.get("sequence"), "event.sequence", minimum=1),
                tick=event_tick,
                event_type=event_type,
                subject_id=subject_id,
                details=details,
            )
        )
    return events


def _load_command_history(raw_data: object, current_tick: int) -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    previous_tick = 0
    for raw_entry in _list(raw_data, "state.command_history"):
        entry = _mapping(raw_entry, "command history entry")
        tick = _integer(entry.get("tick"), "command tick", minimum=0)
        if tick < previous_tick or tick > current_tick:
            raise PersistenceError("command history ticks must be ordered and not in the future")
        previous_tick = tick
        try:
            command = command_from_dict(entry.get("command"))
        except ValueError as error:
            raise PersistenceError(f"invalid recorded command: {error}") from error
        history.append({"tick": tick, "command": command_to_dict(command)})
    return history


def _cell_set(raw_data: object, simulation: Simulation, field: str) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for raw_cell in _list(raw_data, field):
        cell_data = _list(raw_cell, field)
        if len(cell_data) != 2:
            raise PersistenceError(f"{field} cells must contain x and y")
        cell = (
            _integer(cell_data[0], f"{field}.x", minimum=0),
            _integer(cell_data[1], f"{field}.y", minimum=0),
        )
        if not simulation.game_map.contains_cell(cell):
            raise PersistenceError(f"{field} contains an out-of-bounds cell")
        result.add(cell)
    return result


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PersistenceError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise PersistenceError(f"{field} must be a list")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PersistenceError(f"{field} must be a non-empty string")
    return value


def _string_set(value: object, field: str) -> set[str]:
    return set(_string_list(value, field))


def _string_list(value: object, field: str) -> list[str]:
    raw_items = _list(value, field)
    result: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            raise PersistenceError(f"{field} must contain only strings")
        if item in result:
            raise PersistenceError(f"{field} cannot contain duplicates")
        result.append(item)
    return result


def _integer(value: object, field: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise PersistenceError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise PersistenceError(f"{field} must be at least {minimum}")
    return value


def _past_tick(value: object, field: str, current_tick: int) -> int:
    tick = _integer(value, field, minimum=0)
    if tick > current_tick:
        raise PersistenceError(f"{field} cannot be in the future")
    return tick


def _number(value: object, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PersistenceError(f"{field} must be a number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise PersistenceError(f"{field} must be at least {minimum}")
    return result


def _point(value: object, field: str) -> Point:
    items = _list(value, field)
    if len(items) != 2:
        raise PersistenceError(f"{field} must contain x and y")
    return Point(_number(items[0], f"{field}.x"), _number(items[1], f"{field}.y"))
