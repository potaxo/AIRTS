"""Versioned JSON save/load support for complete simulation state."""

from __future__ import annotations

import json
from pathlib import Path

from airts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    AutomationTransition,
    DefendParameters,
    EconomyParameters,
    PatrolParameters,
    ProductionParameters,
    ReinforcementParameters,
    RepairParameters,
    RepairPhase,
    transition_is_allowed,
)
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
from airts.spatial import (
    GroundingSelection,
    SpatialKind,
    SpatialReference,
    SpatialStore,
    spatial_kind,
)
from airts.visibility import PlayerVisibility, VisibilitySystem

SAVE_SCHEMA = "airts-save-v4"


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
    simulation.suspended_assignments = _load_suspended_assignments(
        state.get("suspended_assignments", {}), simulation
    )
    _validate_assignment_completeness(simulation)
    simulation.visibility = _load_visibility(state.get("visibility"), simulation)
    simulation.resources = _load_resources(state.get("resources"), simulation)
    simulation.spatial = _load_spatial(state.get("spatial"), simulation, tick)
    simulation.selection = _load_selection(state.get("selection"), simulation)
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
        int(automation_id.removeprefix("automation_"))
        for automation_id in simulation.automations
        if automation_id.startswith("automation_")
        and automation_id.removeprefix("automation_").isdigit()
    ]
    if generated_numbers and simulation._next_automation_number <= max(generated_numbers):
        raise PersistenceError("next_automation_number would overwrite an existing automation")
    simulation._next_entity_number = _integer(
        state.get("next_entity_number"), "state.next_entity_number", minimum=1
    )
    simulation._movement_blocked = _string_set(
        state.get("movement_blocked", []), "state.movement_blocked"
    )
    if not simulation._movement_blocked.issubset(simulation.entities):
        raise PersistenceError("movement_blocked references an unknown entity")
    return simulation


def _load_spatial(raw_data: object, simulation: Simulation, tick: int) -> SpatialStore:
    data = _mapping(raw_data, "state.spatial")
    references_data = _mapping(data.get("references"), "state.spatial.references")
    store = SpatialStore()
    store.references.clear()
    names: set[str] = set()
    for reference_id, raw_reference in references_data.items():
        reference_data = _mapping(raw_reference, f"spatial reference {reference_id}")
        if reference_data.get("id") != reference_id:
            raise PersistenceError("spatial reference key and ID differ")
        try:
            kind = SpatialKind(_string(reference_data.get("kind"), "spatial kind"))
            geometry = target_from_dict(reference_data.get("geometry"))
        except ValueError as error:
            raise PersistenceError(f"invalid spatial reference: {error}") from error
        if spatial_kind(geometry) is not kind:
            raise PersistenceError("spatial reference kind does not match geometry")
        points = (geometry.point,) if hasattr(geometry, "point") else geometry.points
        if any(not simulation.game_map.contains(point) for point in points):
            raise PersistenceError("spatial reference lies outside the map")
        name = _nullable_string(reference_data.get("name"), "spatial name")
        if name is not None:
            if kind is not SpatialKind.REGION or name.casefold() in names:
                raise PersistenceError("spatial region names must be unique")
            names.add(name.casefold())
        created_tick = _past_tick(reference_data.get("created_tick"), "created_tick", tick)
        modified_tick = _past_tick(reference_data.get("modified_tick"), "modified_tick", tick)
        if modified_tick < created_tick:
            raise PersistenceError("spatial modified tick precedes creation")
        store.references[reference_id] = SpatialReference(
            reference_id, kind, geometry, created_tick, modified_tick, name
        )
    counters = _mapping(data.get("next_numbers"), "state.spatial.next_numbers")
    for kind in SpatialKind:
        store.next_numbers[kind] = _integer(
            counters.get(kind.value), f"next {kind.value} number", minimum=1
        )
        used = [
            int(item.removeprefix(f"{kind.value}_"))
            for item in store.references
            if item.startswith(f"{kind.value}_") and item.removeprefix(f"{kind.value}_").isdigit()
        ]
        if used and store.next_numbers[kind] <= max(used):
            raise PersistenceError("spatial counter would overwrite an existing reference")
    return store


def _load_selection(raw_data: object, simulation: Simulation) -> GroundingSelection:
    data = _mapping(raw_data, "state.selection")
    selection = GroundingSelection(
        tuple(_string_list(data.get("entity_ids"), "selection.entity_ids")),
        tuple(_string_list(data.get("point_ids"), "selection.point_ids")),
        tuple(_string_list(data.get("route_ids"), "selection.route_ids")),
        tuple(_string_list(data.get("region_ids"), "selection.region_ids")),
    )
    if not set(selection.entity_ids).issubset(simulation.entities):
        raise PersistenceError("selection references an unknown entity")
    for ids, kind in (
        (selection.point_ids, SpatialKind.POINT),
        (selection.route_ids, SpatialKind.ROUTE),
        (selection.region_ids, SpatialKind.REGION),
    ):
        if any(
            reference_id not in simulation.spatial.references
            or simulation.spatial.references[reference_id].kind is not kind
            for reference_id in ids
        ):
            raise PersistenceError("selection references an invalid spatial object")
    return selection


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
            path or move_target is not None or state not in {UnitState.IDLE, UnitState.PRODUCING}
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
            attack_target_id=_nullable_string(
                entity.get("attack_target_id"), "entity.attack_target_id"
            ),
            attack_cooldown=_integer(
                entity.get("attack_cooldown"), "entity.attack_cooldown", minimum=0
            ),
        )
    for loaded_entity in entities.values():
        if (
            loaded_entity.attack_target_id is not None
            and loaded_entity.attack_target_id not in entities
        ):
            raise PersistenceError(f"entity {loaded_entity.entity_id} has an unknown attack target")
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


def _load_automations(raw_data: object, simulation: Simulation) -> dict[str, Automation]:
    automations_data = _mapping(raw_data, "state.automations")
    automations: dict[str, Automation] = {}
    for automation_id, raw_automation in automations_data.items():
        data = _mapping(raw_automation, f"automation {automation_id}")
        if data.get("id") != automation_id:
            raise PersistenceError(f"invalid automation identity: {automation_id}")
        try:
            kind = AutomationKind(_string(data.get("template"), "automation.template"))
            status = AutomationStatus(_string(data.get("status"), "automation.status"))
        except ValueError as error:
            raise PersistenceError(f"invalid automation {automation_id}: {error}") from error
        entity_ids = _string_list(data.get("entity_ids"), "automation.entity_ids")
        if any(entity_id not in simulation.entities for entity_id in entity_ids):
            raise PersistenceError(f"automation {automation_id} references an invalid entity")
        created_tick = _past_tick(
            data.get("created_tick"), "automation.created_tick", simulation.tick
        )
        modified_tick = _past_tick(
            data.get("modified_tick"), "automation.modified_tick", simulation.tick
        )
        if modified_tick < created_tick:
            raise PersistenceError("automation modified_tick cannot precede created_tick")
        parameters = _load_automation_parameters(
            kind, data.get("parameters"), entity_ids, simulation
        )
        history = _load_transition_history(data.get("transition_history"), status, simulation.tick)
        automations[automation_id] = Automation(
            automation_id=automation_id,
            title=_string(data.get("title"), "automation.title"),
            kind=kind,
            owner_id=_string(data.get("owner_id"), "automation.owner_id"),
            priority=_integer(data.get("priority"), "automation.priority", minimum=-100),
            created_tick=created_tick,
            modified_tick=modified_tick,
            original_instruction=_optional_string(
                data.get("original_instruction"), "automation.original_instruction"
            ),
            entity_ids=entity_ids,
            parameters=parameters,
            creation_source=_string(data.get("creation_source"), "automation.creation_source"),
            model_provider=_nullable_string(
                data.get("model_provider"), "automation.model_provider"
            ),
            status=status,
            reason_code=_string(data.get("reason_code"), "automation.reason_code"),
            transition_history=history,
        )
        if automations[automation_id].priority > 100:
            raise PersistenceError("automation priority cannot exceed 100")
    _validate_automation_links(automations, simulation)
    return automations


def _validate_automation_links(automations: dict[str, Automation], simulation: Simulation) -> None:
    for automation in automations.values():
        if automation.transition_history[-1].reason_code != automation.reason_code:
            raise PersistenceError("automation reason does not match transition history")
        if automation.transition_history[-1].tick != automation.modified_tick:
            raise PersistenceError("automation modified tick does not match transition history")
        if automation.transition_history[0].tick != automation.created_tick:
            raise PersistenceError("automation created tick does not match transition history")
        if any(
            simulation.entities[entity_id].owner_id != automation.owner_id
            for entity_id in automation.entity_ids
        ):
            raise PersistenceError("automation references an entity owned by another player")
        if isinstance(automation.parameters, ReinforcementParameters):
            target = automations.get(automation.parameters.target_automation_id)
            if target is None or target.owner_id != automation.owner_id:
                raise PersistenceError("reinforcement references an invalid target automation")
        elif isinstance(automation.parameters, RepairParameters):
            for building_id in automation.parameters.destinations.values():
                building = simulation.entities.get(building_id)
                if building is None or building.kind not in {
                    EntityKind.REPAIR_HUB,
                    EntityKind.FACTORY,
                    EntityKind.COMMAND_CENTER,
                }:
                    raise PersistenceError("repair references an invalid destination")
            for resume_id in automation.parameters.resume_automation_ids.values():
                if resume_id is not None and resume_id not in automations:
                    raise PersistenceError("repair references an invalid resume automation")


def _load_automation_parameters(
    kind: AutomationKind,
    raw_data: object,
    entity_ids: list[str],
    simulation: Simulation,
) -> (
    PatrolParameters
    | DefendParameters
    | ProductionParameters
    | ReinforcementParameters
    | RepairParameters
    | EconomyParameters
):
    data = _mapping(raw_data, "automation.parameters")
    if kind is AutomationKind.PATROL:
        try:
            target = target_from_dict(data.get("target"))
        except ValueError as error:
            raise PersistenceError(f"invalid patrol target: {error}") from error
        waypoints = _points(data.get("waypoints"), "patrol.waypoints")
        if not waypoints:
            raise PersistenceError("patrol requires waypoints")
        indices_data = _mapping(data.get("waypoint_indices"), "patrol.waypoint_indices")
        if set(indices_data) != set(entity_ids):
            raise PersistenceError("patrol waypoint indices must match its entities")
        indices = {
            entity_id: _integer(indices_data[entity_id], "waypoint index", minimum=0)
            for entity_id in entity_ids
        }
        if any(index >= len(waypoints) for index in indices.values()):
            raise PersistenceError("patrol has an invalid waypoint index")
        return PatrolParameters(target, waypoints, indices)
    if kind is AutomationKind.DEFEND:
        try:
            target = target_from_dict(data.get("target"))
        except ValueError as error:
            raise PersistenceError(f"invalid defend target: {error}") from error
        stations_data = _mapping(data.get("stations"), "defend.stations")
        if set(stations_data) != set(entity_ids):
            raise PersistenceError("defend stations must match its entities")
        return DefendParameters(
            target,
            {
                entity_id: _point(stations_data[entity_id], "defend station")
                for entity_id in entity_ids
            },
        )
    if kind is AutomationKind.PRODUCTION:
        try:
            unit_kind = EntityKind(_string(data.get("unit_kind"), "production.unit_kind"))
        except ValueError as error:
            raise PersistenceError(f"invalid production kind: {error}") from error
        if unit_kind.profile.category is not EntityCategory.UNIT:
            raise PersistenceError("production unit_kind must be a unit")
        factory_id = _string(data.get("factory_id"), "production.factory_id")
        if (
            factory_id not in simulation.entities
            or simulation.entities[factory_id].kind is not EntityKind.FACTORY
        ):
            raise PersistenceError("production references an invalid factory")
        rally_data = data.get("rally_point")
        target_count = _integer(data.get("target_count"), "production.target_count", minimum=1)
        produced_count = _integer(
            data.get("produced_count"), "production.produced_count", minimum=0
        )
        if produced_count > target_count:
            raise PersistenceError("production count exceeds target")
        return ProductionParameters(
            factory_id=factory_id,
            unit_kind=unit_kind,
            target_count=target_count,
            build_ticks=_integer(data.get("build_ticks"), "production.build_ticks", minimum=1),
            rally_point=None if rally_data is None else _point(rally_data, "rally_point"),
            produced_count=produced_count,
            progress_ticks=_integer(
                data.get("progress_ticks"), "production.progress_ticks", minimum=0
            ),
            produced_entity_ids=_string_list(
                data.get("produced_entity_ids"), "production.produced_entity_ids"
            ),
            cost_paid=_boolean(data.get("cost_paid"), "production.cost_paid"),
        )
    if kind is AutomationKind.REINFORCEMENT:
        return ReinforcementParameters(
            target_automation_id=_string(
                data.get("target_automation_id"), "reinforcement.target_automation_id"
            ),
            candidate_entity_ids=_string_list(
                data.get("candidate_entity_ids"), "reinforcement.candidate_entity_ids"
            ),
            minimum_units=_integer(
                data.get("minimum_units"), "reinforcement.minimum_units", minimum=1
            ),
            transferred_entity_ids=_string_list(
                data.get("transferred_entity_ids"), "reinforcement.transferred_entity_ids"
            ),
        )
    if kind is AutomationKind.ECONOMY:
        generator_ids = _string_list(data.get("generator_ids"), "economy.generator_ids")
        if generator_ids != entity_ids or any(
            simulation.entities[item].kind is not EntityKind.RESOURCE_GENERATOR
            for item in generator_ids
        ):
            raise PersistenceError("economy generators must match resource-generator entities")
        return EconomyParameters(
            generator_ids,
            _integer(data.get("target_resources"), "economy.target_resources", minimum=1),
            _integer(data.get("income_per_cycle"), "economy.income_per_cycle", minimum=1),
            _integer(data.get("income_cycle_ticks"), "economy.income_cycle_ticks", minimum=1),
            _integer(data.get("collected"), "economy.collected", minimum=0),
        )
    destinations = _string_mapping(data.get("destinations"), "repair.destinations")
    resume_ids = _nullable_string_mapping(
        data.get("resume_automation_ids"), "repair.resume_automation_ids"
    )
    phase_data = _mapping(data.get("phases"), "repair.phases")
    if (
        set(destinations) != set(entity_ids)
        or set(resume_ids) != set(entity_ids)
        or set(phase_data) != set(entity_ids)
    ):
        raise PersistenceError("repair parameter entities do not match automation entities")
    try:
        phases = {
            entity_id: RepairPhase(_string(phase_data[entity_id], "repair phase"))
            for entity_id in entity_ids
        }
    except ValueError as error:
        raise PersistenceError(f"invalid repair phase: {error}") from error
    health_threshold = _number(data.get("health_threshold"), "repair.health_threshold", minimum=0.0)
    if not 0 < health_threshold <= 1:
        raise PersistenceError("repair health_threshold must be greater than zero and at most one")
    return RepairParameters(
        health_threshold=health_threshold,
        repair_rate=_integer(data.get("repair_rate"), "repair.repair_rate", minimum=1),
        destinations=destinations,
        resume_automation_ids=resume_ids,
        phases=phases,
    )


def _load_transition_history(
    raw_data: object, status: AutomationStatus, current_tick: int
) -> list[AutomationTransition]:
    history: list[AutomationTransition] = []
    previous_tick = 0
    for index, raw_transition in enumerate(_list(raw_data, "transition_history")):
        data = _mapping(raw_transition, "transition")
        tick = _past_tick(data.get("tick"), "transition.tick", current_tick)
        if tick < previous_tick:
            raise PersistenceError("transition ticks must be ordered")
        previous_tick = tick
        previous_data = data.get("previous")
        try:
            previous = (
                None
                if previous_data is None
                else AutomationStatus(_string(previous_data, "transition.previous"))
            )
            current = AutomationStatus(_string(data.get("current"), "transition.current"))
        except ValueError as error:
            raise PersistenceError(f"invalid transition status: {error}") from error
        if index == 0 and previous is not None:
            raise PersistenceError("initial transition must not have a previous status")
        if history and previous is not history[-1].current:
            raise PersistenceError("transition history is discontinuous")
        if previous is not None and not transition_is_allowed(previous, current):
            raise PersistenceError("transition history contains an illegal transition")
        history.append(
            AutomationTransition(
                tick, previous, current, _string(data.get("reason_code"), "reason_code")
            )
        )
    if not history or history[-1].current is not status:
        raise PersistenceError("transition history does not match automation status")
    return history


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
            or automation.status.terminal
        ):
            raise PersistenceError(f"invalid assignment: {entity_id} -> {automation_id}")
        assignments[entity_id] = automation_id
    return assignments


def _load_suspended_assignments(raw_data: object, simulation: Simulation) -> dict[str, str]:
    data = _string_mapping(raw_data, "state.suspended_assignments")
    for entity_id, automation_id in data.items():
        current_id = simulation.assignments.get(entity_id)
        current = simulation.automations.get(current_id or "")
        suspended = simulation.automations.get(automation_id)
        if (
            entity_id not in simulation.entities
            or current is None
            or current.kind is not AutomationKind.REPAIR_AND_RETURN
            or suspended is None
            or suspended.status.terminal
            or entity_id not in suspended.entity_ids
        ):
            raise PersistenceError(f"invalid suspended assignment for {entity_id}")
    return data


def _validate_assignment_completeness(simulation: Simulation) -> None:
    for automation in simulation.automations.values():
        assigned = {
            entity_id
            for entity_id, automation_id in simulation.assignments.items()
            if automation_id == automation.automation_id
        }
        suspended = {
            entity_id
            for entity_id, automation_id in simulation.suspended_assignments.items()
            if automation_id == automation.automation_id
        }
        expected = set(automation.entity_ids).difference(suspended)
        if automation.status.terminal:
            if assigned:
                raise PersistenceError("terminal automations cannot retain assignments")
        elif automation.kind is AutomationKind.REINFORCEMENT:
            if assigned:
                raise PersistenceError("reinforcement automations do not own entities")
        elif automation.status is AutomationStatus.PAUSED:
            if not assigned.issubset(expected):
                raise PersistenceError("paused automation assignments are invalid")
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


def _load_resources(raw_data: object, simulation: Simulation) -> dict[str, int]:
    data = _mapping(raw_data, "state.resources")
    owners = {entity.owner_id for entity in simulation.entities.values()}
    if not owners.issubset(data):
        raise PersistenceError("resources are missing an entity owner")
    return {
        owner_id: _integer(value, f"resources.{owner_id}", minimum=0)
        for owner_id, value in data.items()
    }


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


def _optional_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise PersistenceError(f"{field} must be a string")
    return value


def _nullable_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


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


def _string_mapping(value: object, field: str) -> dict[str, str]:
    data = _mapping(value, field)
    result: dict[str, str] = {}
    for key, item in data.items():
        result[key] = _string(item, f"{field}.{key}")
    return result


def _nullable_string_mapping(value: object, field: str) -> dict[str, str | None]:
    data = _mapping(value, field)
    return {key: _nullable_string(item, f"{field}.{key}") for key, item in data.items()}


def _integer(value: object, field: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise PersistenceError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise PersistenceError(f"{field} must be at least {minimum}")
    return value


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise PersistenceError(f"{field} must be a boolean")
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


def _points(value: object, field: str) -> tuple[Point, ...]:
    return tuple(_point(item, field) for item in _list(value, field))
