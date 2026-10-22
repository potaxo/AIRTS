"""Versioned JSON persistence adapter for complete simulation state."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from gc import collect as collect_garbage
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile

from airts.contracts.automations import (
    PRODUCTION_HISTORY_LIMIT,
    Automation,
    AutomationKind,
    AutomationStatus,
    AutomationTransition,
    ConstructionParameters,
    DefendParameters,
    EconomyParameters,
    PatrolParameters,
    ProductionParameters,
    ReinforcementParameters,
    RepairParameters,
    RepairPhase,
    build_patrol_waypoints,
    target_center,
    transition_is_allowed,
)
from airts.contracts.commands import command_from_dict, command_to_dict
from airts.contracts.events import Event, EventLog, EventType
from airts.contracts.spatial import (
    GroundingSelection,
    Point,
    PolygonRegion,
    PolylineTarget,
    SpatialKind,
    SpatialReference,
    SpatialStore,
    SpatialTarget,
    spatial_kind,
    target_from_dict,
)
from airts.navigation.collision.geometry import collision_radius
from airts.simulation import Simulation
from airts.systems import economy
from airts.systems.movement import CONTACT_WAIT_TICKS
from airts.world.entities import (
    Entity,
    OrderKind,
    ProductionDeployment,
    Projectile,
    ProjectileTrace,
    UnitOrder,
    UnitState,
    UpgradeState,
)
from airts.world.map import (
    GameMap,
    MapValidationError,
    OccupancyError,
    OccupancyGrid,
    load_map_data,
)
from airts.world.opponents import OpponentState, Squad, SquadPhase
from airts.world.units.catalog import BUILDABLE_KINDS, EntityKind
from airts.world.units.schema import EntityCategory
from airts.world.visibility import PlayerVisibility, VisibilitySystem

SAVE_SCHEMA = "airts-save-v30"
MATCH_SAVE_SCHEMA = "airts-match-save-v24"


class PersistenceError(ValueError):
    """Raised when saved state is malformed or incompatible."""


@dataclass(frozen=True, slots=True)
class SaveSnapshot:
    schema: str
    game_map: dict[str, object]
    state: dict[str, object]
    events: tuple[Event, ...]


def capture_save(simulation: Simulation) -> SaveSnapshot:
    """Capture at a tick boundary; a writer never reads the live simulation.

    Recorded events are append-only frozen records. Retain their packed paths until
    the writer encodes each event, rather than expanding the entire history in the UI.
    """
    state = simulation.export_state(include_events=False)
    state["command_history"] = deepcopy(state["command_history"])
    if "match_result" in state:
        state["match_result"] = deepcopy(state["match_result"])
    return SaveSnapshot(
        MATCH_SAVE_SCHEMA if simulation.game_map.match_slots else SAVE_SCHEMA,
        simulation.game_map.to_dict(),
        state,
        simulation.events.events,
    )


def write_save(snapshot: SaveSnapshot, path: str | Path) -> None:
    """Encode bounded pieces using the C JSON encoder, then replace atomically."""
    destination = Path(path)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write('{"schema":' + json.dumps(snapshot.schema) + ',"map":')
            stream.write(json.dumps(snapshot.game_map, separators=(",", ":")))
            stream.write(',"state":{')
            for key, value in snapshot.state.items():
                stream.write(json.dumps(key) + ":")
                stream.write(json.dumps(value, separators=(",", ":")))
                stream.write(",")
            stream.write('"events":[')
            for index, event in enumerate(snapshot.events):
                if index:
                    stream.write(",")
                stream.write(json.dumps(event.to_dict(), separators=(",", ":")))
            stream.write("]}}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_simulation(simulation: Simulation, path: str | Path) -> None:
    write_save(capture_save(simulation), path)


def load_simulation(path: str | Path, *, record_history: bool = True) -> Simulation:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            simulation = load_simulation_data(json.load(stream), record_history=record_history)
    except json.JSONDecodeError as error:
        raise PersistenceError(f"invalid save JSON: {error.msg}") from error
    if (
        len(simulation.entities) >= simulation.LARGE_SCENE_GC_ENTITY_THRESHOLD
        or simulation.game_map.width * simulation.game_map.height
        >= simulation.LARGE_SCENE_GC_CELL_THRESHOLD
    ):
        # The constructor drained the opening world, before JSON restoration.
        # Drain and age the restored world after decoding temporaries are gone,
        # at the load boundary rather than repeatedly scanning it in live frames.
        collect_garbage()
    return simulation


def load_simulation_data(raw_data: object, *, record_history: bool = True) -> Simulation:
    document = _mapping(raw_data, "save document")
    if document.get("schema") not in {SAVE_SCHEMA, MATCH_SAVE_SCHEMA}:
        raise PersistenceError(
            f"unsupported save schema: {document.get('schema')}; only current formats "
            f"{SAVE_SCHEMA} and {MATCH_SAVE_SCHEMA} are supported"
        )
    try:
        game_map = load_map_data(document.get("map"))
    except MapValidationError as error:
        raise PersistenceError(f"invalid saved map: {error}") from error
    if bool(game_map.match_slots) != (document.get("schema") == MATCH_SAVE_SCHEMA):
        raise PersistenceError("match maps require the match save schema")
    state = _mapping(document.get("state"), "state")
    tick = _integer(state.get("tick"), "state.tick", minimum=0)
    random_seed = _integer(state.get("random_seed"), "state.random_seed")
    ambient_enemy_spawns = _boolean(
        state.get("ambient_enemy_spawns", False), "state.ambient_enemy_spawns"
    )
    enemy_spawn_interval_ticks = _integer(
        state.get(
            "enemy_spawn_interval_ticks",
            Simulation.DEFAULT_ENEMY_SPAWN_INTERVAL_TICKS,
        ),
        "state.enemy_spawn_interval_ticks",
        minimum=1,
    )
    enemy_spawn_cap = _integer(
        state.get("enemy_spawn_cap", Simulation.DEFAULT_ENEMY_SPAWN_CAP),
        "state.enemy_spawn_cap",
        minimum=0,
    )
    if game_map.match_slots and ambient_enemy_spawns:
        raise PersistenceError("team matches cannot use ambient enemy spawning")
    simulation = Simulation(
        game_map,
        random_seed,
        ambient_enemy_spawns=ambient_enemy_spawns,
        enemy_spawn_interval_ticks=enemy_spawn_interval_ticks,
        enemy_spawn_cap=enemy_spawn_cap,
        record_history=record_history,
    )
    simulation.tick = tick
    simulation.entities = _load_entities(
        state.get("entities"),
        game_map,
        tick,
    )
    economy.rebuild_income(simulation)
    simulation._invalidate_navigation_cache()
    simulation.occupancy = _build_occupancy(simulation)
    simulation.projectiles = _load_projectiles(state.get("projectiles", {}), simulation)
    simulation.projectile_traces = _load_projectile_traces(
        state.get("projectile_traces", []), simulation
    )
    simulation.automations = _load_automations(state.get("automations"), simulation)
    _validate_construction_sites(simulation)
    simulation._rebuild_live_automations(simulation.automations.values())
    simulation.assignments = _load_assignments(state.get("assignments"), simulation)
    if any(
        entity.entity_id in simulation.assignments
        and (entity.order_queue or entity.attack_gather_point is not None)
        for entity in simulation.entities.values()
    ):
        raise PersistenceError("direct movement or attack intent conflicts with automation control")
    for entity in simulation.entities.values():
        if entity.repair_target_id is None and entity.reclaim_target_id is None:
            continue
        assigned = simulation.automations.get(simulation.assignments.get(entity.entity_id, ""))
        if entity.hold_anchor is not None or (
            assigned is not None
            and (
                entity.reclaim_target_id is not None
                or assigned.kind is not AutomationKind.PATROL
                or assigned.status not in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}
            )
        ):
            raise PersistenceError("builder repair conflicts with another controlling order")
    simulation.suspended_assignments = _load_suspended_assignments(
        state.get("suspended_assignments", {}), simulation
    )
    _validate_assignment_completeness(simulation)
    _validate_recovery_request_owners(simulation)
    simulation.visibility = _load_visibility(state.get("visibility"), simulation)
    simulation.resources = _load_resources(state.get("resources"), simulation)
    simulation.ai_income_multiplier = _integer(
        state.get("ai_income_multiplier"), "state.ai_income_multiplier"
    )
    if simulation.ai_income_multiplier not in (1, 5):
        raise PersistenceError("state.ai_income_multiplier must be 1 or 5")
    simulation.developer_mode = _boolean(state.get("developer_mode"), "state.developer_mode")
    simulation.developer_income_ticks = _integer(
        state.get("developer_income_ticks"), "state.developer_income_ticks", minimum=0
    )
    if (
        simulation.developer_income_ticks >= Simulation.TICKS_PER_SECOND
        or simulation.developer_income_ticks > tick
        or (not simulation.developer_mode and simulation.developer_income_ticks != 0)
        or (simulation.developer_mode and "player" not in simulation.resources)
    ):
        raise PersistenceError("invalid developer mode income state")
    simulation.spatial = _load_spatial(state.get("spatial"), simulation, tick)
    simulation.selection = _load_selection(state.get("selection"), simulation)
    event_log = EventLog(enabled=record_history)
    try:
        event_log.restore(_load_events(state.get("events"), tick, retain=record_history))
    except ValueError as error:
        raise PersistenceError(f"invalid saved event sequence: {error}") from error
    simulation.events = event_log
    simulation._command_history = _load_command_history(
        state.get("command_history"), tick, retain=record_history
    )
    recorded_count = len(_list(state.get("command_history"), "state.command_history"))
    simulation._command_count = _integer(
        state.get("command_count", recorded_count), "state.command_count", minimum=recorded_count
    )
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
    simulation._next_projectile_number = _integer(
        state.get("next_projectile_number", 1),
        "state.next_projectile_number",
        minimum=1,
    )
    projectile_numbers = [
        int(projectile_id.removeprefix("projectile_"))
        for projectile_id in simulation.projectiles
        if projectile_id.startswith("projectile_")
        and projectile_id.removeprefix("projectile_").isdigit()
    ]
    if projectile_numbers and simulation._next_projectile_number <= max(projectile_numbers):
        raise PersistenceError("next_projectile_number would overwrite an active projectile")
    simulation._movement_blocked = _string_set(
        state.get("movement_blocked", []), "state.movement_blocked"
    )
    if not simulation._movement_blocked.issubset(simulation.entities):
        raise PersistenceError("movement_blocked references an unknown entity")
    if state.get("traffic_reservations") is not None:
        raise PersistenceError("traffic reservations are unsupported by current saves")
    if game_map.match_slots:
        from airts.systems.skirmish import evaluate_result, validate_match_state

        if "match_result" not in state:
            raise PersistenceError("match_result is required for match saves")
        result = state["match_result"]
        teams = {slot.team for slot in game_map.match_slots}
        if result is not None and (type(result) is not int or result not in {0, *teams}):
            raise PersistenceError("invalid match_result")
        simulation.match_result = result
        simulation.opponents = _load_opponents(state.get("opponents"), simulation)
        try:
            validate_match_state(simulation)
        except ValueError as error:
            raise PersistenceError(str(error)) from error
        if tick > 0 and result != evaluate_result(simulation):
            raise PersistenceError("saved match result disagrees with surviving assets")
    elif state.get("match_result") is not None:
        raise PersistenceError("legacy maps cannot have a match result")
    return simulation


def _load_opponent_squad(
    raw_squad: object, simulation: Simulation, owner: str, assigned: set[str]
) -> Squad:
    field = f"opponents.{owner}"
    squad_data = _mapping(raw_squad, f"{field}.squad")
    members = _string_list(squad_data.get("entity_ids"), f"{field}.entity_ids")
    if len(members) > 32:
        raise PersistenceError("opponent squad exceeds its unit budget")
    for key in members:
        entity = simulation.entities.get(key)
        if (
            entity is None
            or entity.owner_id != owner
            or not entity.is_movable
            or entity.kind.profile.attack_damage <= 0
            or key in assigned
        ):
            raise PersistenceError("opponent squad has an invalid or duplicate member")
        assigned.add(key)
    try:
        phase = SquadPhase(_string(squad_data.get("phase"), f"{field}.phase"))
    except ValueError as error:
        raise PersistenceError(f"invalid opponent squad phase: {error}") from error
    target = _nullable_string(squad_data.get("target_id"), f"{field}.target_id")
    if target is not None and (
        target not in simulation.entities
        or not simulation.are_hostile(owner, simulation.entities[target].owner_id)
    ):
        raise PersistenceError("opponent target must reference a surviving hostile entity")
    destination = (
        None
        if squad_data.get("destination") is None
        else _point(squad_data["destination"], f"{field}.destination")
    )
    if destination is not None and not simulation.game_map.contains(destination):
        raise PersistenceError("opponent destination lies outside the map")
    retry_tick = _integer(squad_data.get("retry_tick"), f"{field}.retry_tick", minimum=0)
    if retry_tick > simulation.tick + 50:
        raise PersistenceError("opponent retry tick exceeds its order cooldown")
    return Squad(
        entity_ids=members,
        phase=phase,
        phase_tick=_past_tick(squad_data.get("phase_tick"), f"{field}.phase_tick", simulation.tick),
        target_id=target,
        destination=destination,
        last_order_tick=_past_tick(
            squad_data.get("last_order_tick"), f"{field}.last_order_tick", simulation.tick
        ),
        retry_tick=retry_tick,
        last_failure=_nullable_string(squad_data.get("last_failure"), f"{field}.last_failure"),
    )


def _load_opponents(raw_data: object, simulation: Simulation) -> dict[str, OpponentState]:
    data = _mapping(raw_data, "state.opponents")
    owners = {slot.owner_id for slot in simulation.game_map.match_slots if slot.controller == "ai"}
    if set(data) != owners:
        raise PersistenceError("opponents must contain exactly the AI-controlled owners")
    opponents: dict[str, OpponentState] = {}
    for owner, raw_opponent in data.items():
        field = f"opponents.{owner}"
        item = _mapping(raw_opponent, field)
        assigned: set[str] = set()

        reserve = _load_opponent_squad(item.get("reserve"), simulation, owner, assigned)
        if len(reserve.entity_ids) > 4:
            raise PersistenceError("opponent reserve exceeds its unit budget")
        squads = [
            _load_opponent_squad(raw_squad, simulation, owner, assigned)
            for raw_squad in _list(item.get("squads"), f"{field}.squads")
        ]
        paused = _string_list(item.get("budget_paused_ids"), f"{field}.budget_paused_ids")
        for key in paused:
            job = simulation.automations.get(key)
            if (
                job is None
                or job.owner_id != owner
                or not isinstance(job.parameters, ProductionParameters)
                or not job.parameters.continuous
            ):
                raise PersistenceError(
                    "opponent budget pause must reference its continuous production"
                )
        credit = _number(item.get("growth_credit"), f"{field}.growth_credit", minimum=0)
        if not isfinite(credit):
            raise PersistenceError("opponent growth credit must be finite")
        budget_tick = _past_tick(item.get("budget_tick"), f"{field}.budget_tick", simulation.tick)
        opponents[owner] = OpponentState(reserve, squads, paused, credit, budget_tick)
    return opponents


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


def _load_entities(
    raw_data: object,
    game_map: GameMap,
    current_tick: int,
) -> dict[str, Entity]:
    entities_data = _mapping(raw_data, "state.entities")
    entities: dict[str, Entity] = {}
    # JSON expands shared routes into independent coordinate pairs. Points are
    # immutable; restore sharing within this load without retaining a global cache.
    waypoints: dict[Point, Point] = {}

    def waypoint(value: object, field: str) -> Point:
        point = _point(value, field)
        return waypoints.setdefault(point, point)

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
        owner_id = _string(entity.get("owner"), "entity.owner")
        if "construction_progress" not in entity:
            raise PersistenceError("entity.construction_progress is required")
        progress_raw = entity.get("construction_progress")
        construction_progress = (
            None
            if progress_raw is None
            else _number(progress_raw, "entity.construction_progress", minimum=0)
        )
        if construction_progress is not None and (
            kind not in BUILDABLE_KINDS
            or not 0 <= construction_progress < 1
            or state is not UnitState.IDLE
        ):
            raise PersistenceError("invalid unfinished building state")
        upgrade_data = _mapping(entity.get("upgrade"), "entity.upgrade")
        tier = _integer(upgrade_data.get("tier"), "entity.upgrade.tier", minimum=1)
        remaining = _integer(
            upgrade_data.get("remaining_ticks"), "entity.upgrade.remaining_ticks", minimum=0
        )
        if tier > 1 + len(kind.upgrades):
            raise PersistenceError("entity.upgrade.tier exceeds the configured tiers")
        next_upgrade = kind.upgrades[tier - 1] if tier <= len(kind.upgrades) else None
        if remaining and (next_upgrade is None or remaining > next_upgrade.duration_ticks):
            raise PersistenceError("entity.upgrade.remaining_ticks has no valid paid transition")
        if construction_progress is not None and (tier != 1 or remaining):
            raise PersistenceError("unfinished entity cannot have upgrade progress")
        if "production_deployment" not in entity:
            raise PersistenceError("entity.production_deployment is required")
        deployment_raw = entity["production_deployment"]
        deployment = None
        if deployment_raw is not None:
            if kind is not EntityKind.FACTORY:
                raise PersistenceError("production deployment requires a factory")
            deployment_data = _mapping(deployment_raw, "entity.production_deployment")
            try:
                deployment_target = target_from_dict(deployment_data.get("target"))
            except ValueError as error:
                raise PersistenceError(f"invalid production deployment target: {error}") from error
            if not isinstance(deployment_target, PolygonRegion | PolylineTarget) or any(
                not game_map.contains(point) for point in deployment_target.points
            ):
                raise PersistenceError(
                    "production deployment requires a line or area inside the map"
                )
            deployment = ProductionDeployment(
                deployment_target,
                _boolean(deployment_data.get("auto_repair"), "production_deployment.auto_repair"),
                _nullable_string(
                    deployment_data.get("defend_automation_id"),
                    "production_deployment.defend_automation_id",
                ),
            )
        if "recovery_request_tick" not in entity or "recovery_released" not in entity:
            raise PersistenceError("entity recovery request fields are required in v11")
        request_raw = entity["recovery_request_tick"]
        recovery_request_tick = (
            None
            if request_raw is None
            else _past_tick(request_raw, "entity.recovery_request_tick", current_tick)
        )
        recovery_released = _boolean(entity["recovery_released"], "entity.recovery_released")
        if recovery_released and recovery_request_tick is None:
            raise PersistenceError("entity.recovery_released requires a recovery request")
        if "hold_anchor" not in entity:
            raise PersistenceError("entity.hold_anchor is required")
        anchor_raw = entity.get("hold_anchor")
        hold_anchor = None if anchor_raw is None else _point(anchor_raw, "entity.hold_anchor")
        recovery_path = [
            waypoint(p, "entity.recovery_path item")
            for p in _list(entity.get("recovery_path"), "entity.recovery_path")
        ]
        recovery_retry_tick = _integer(
            entity.get("recovery_retry_tick"), "entity.recovery_retry_tick", minimum=0
        )
        contact_wait_ticks = _integer(
            entity.get("contact_wait_ticks"), "entity.contact_wait_ticks", minimum=0
        )
        defend_arrived = _boolean(entity.get("defend_arrived"), "entity.defend_arrived")
        if "recovery_blocked_cells" not in entity:
            raise PersistenceError("entity.recovery_blocked_cells is required")
        blocked_raw = entity.get("recovery_blocked_cells")
        recovery_blocked_cells = (
            None
            if blocked_raw is None
            else frozenset(
                _cell(cell, "entity.recovery_blocked_cells", game_map)
                for cell in _list(blocked_raw, "entity.recovery_blocked_cells")
            )
        )
        if contact_wait_ticks > CONTACT_WAIT_TICKS:
            raise PersistenceError("entity.contact_wait_ticks exceeds the contact wait interval")
        if (state is UnitState.HOLDING) != (hold_anchor is not None):
            raise PersistenceError("entity Hold state and hold_anchor must agree")
        if hold_anchor is not None and (
            not kind.profile.movable or not game_map.is_passable(hold_anchor)
        ):
            raise PersistenceError("entity.hold_anchor must be passable for a mobile holder")
        if recovery_path and (
            hold_anchor is None or any(not game_map.is_passable(p) for p in recovery_path)
        ):
            raise PersistenceError("entity.recovery_path requires a holder and passable waypoints")
        if recovery_retry_tick and hold_anchor is None:
            raise PersistenceError("entity.recovery_retry_tick requires a holder")
        if recovery_blocked_cells is not None and (hold_anchor is None or recovery_path):
            raise PersistenceError("blocked Hold recovery requires an anchor and no recovery path")
        health = _integer(entity.get("health"), "entity.health", minimum=0)
        if health > kind.profile.max_health:
            raise PersistenceError(f"entity {entity_id} health exceeds its maximum")
        move_target_raw = entity.get("move_target")
        move_target = (
            None if move_target_raw is None else _point(move_target_raw, "entity.move_target")
        )
        path = [waypoint(item, "entity.path item") for item in _list(entity.get("path"), "path")]
        order_queue = _load_unit_orders(entity.get("order_queue"))
        formation_fields = {
            "formation_group",
            "formation_offset",
            "formation_goal",
            "formation_leg",
        }
        if not formation_fields.issubset(entity):
            raise PersistenceError("entity formation fields are required")
        formation_group = _nullable_string(entity["formation_group"], "entity.formation_group")
        formation_leg = _nullable_string(entity["formation_leg"], "entity.formation_leg")
        formation_offset = (
            None
            if entity["formation_offset"] is None
            else _point(entity["formation_offset"], "entity.formation_offset")
        )
        formation_goal = (
            None
            if entity["formation_goal"] is None
            else _point(entity["formation_goal"], "entity.formation_goal")
        )
        if (
            (formation_group is None) != (formation_offset is None)
            or (formation_goal is None) != (formation_leg is None)
            or (formation_group is not None and not kind.profile.movable)
            or (formation_leg is not None and formation_group is None)
        ):
            raise PersistenceError("invalid entity formation membership or active leg")
        for order in order_queue:
            if order.formation_leg is not None and formation_group is None:
                raise PersistenceError("queued formation move has no formation membership")
        attack_gather_raw = entity.get("attack_gather_point")
        attack_gather_point = (
            None
            if attack_gather_raw is None
            else _point(attack_gather_raw, "entity.attack_gather_point")
        )
        path_cost = _number(entity.get("path_cost"), "entity.path_cost", minimum=0.0)
        progress_target_raw = entity.get("progress_target")
        progress_target = (
            None
            if progress_target_raw is None
            else _point(progress_target_raw, "entity.progress_target")
        )
        progress_distance_raw = entity.get("progress_distance")
        progress_distance = (
            None
            if progress_distance_raw is None
            else _number(progress_distance_raw, "entity.progress_distance", minimum=0.0)
        )
        no_progress_ticks = _integer(
            entity.get("no_progress_ticks", 0), "entity.no_progress_ticks", minimum=0
        )
        route_ticks = _integer(entity.get("route_ticks", 0), "entity.route_ticks", minimum=0)
        collision_pressure = _integer(
            entity.get("collision_pressure", 0), "entity.collision_pressure", minimum=0
        )
        pursue_target = _boolean(entity.get("pursue_target", False), "entity.pursue_target")
        last_attacker_id = _nullable_string(
            entity.get("last_attacker_id"), "entity.last_attacker_id"
        )
        last_attacked_tick_raw = entity.get("last_attacked_tick")
        last_attacked_tick = (
            None
            if last_attacked_tick_raw is None
            else _integer(last_attacked_tick_raw, "entity.last_attacked_tick", minimum=0)
        )
        if (last_attacker_id is None) != (last_attacked_tick is None):
            raise PersistenceError(
                f"entity {entity_id} attack-source fields must both be set or null"
            )
        if last_attacked_tick is not None and last_attacked_tick > current_tick:
            raise PersistenceError(f"entity {entity_id} was attacked in the future")
        if "last_player_control_tick" not in entity:
            raise PersistenceError(f"entity {entity_id} is missing last_player_control_tick")
        player_tick_raw = entity["last_player_control_tick"]
        last_player_control_tick = (
            None
            if player_tick_raw is None
            else _past_tick(player_tick_raw, "entity.last_player_control_tick", current_tick)
        )
        if last_player_control_tick is not None and (
            owner_id != "player" or not kind.profile.combat_support
        ):
            raise PersistenceError(
                f"entity {entity_id} player control timestamp requires a player combat unit"
            )
        if (progress_target is None) != (progress_distance is None):
            raise PersistenceError(
                f"entity {entity_id} movement progress fields must both be set or null"
            )
        hold_return_progress = (
            hold_anchor is not None
            and recovery_request_tick is not None
            and progress_target == hold_anchor
        )
        if (
            progress_target is not None
            and (not path or progress_target != path[0])
            and not hold_return_progress
        ):
            raise PersistenceError(
                f"entity {entity_id} movement progress target must match its next waypoint"
            )
        if no_progress_ticks and progress_target is None:
            raise PersistenceError(
                f"entity {entity_id} cannot have stalled ticks without movement progress"
            )
        if route_ticks and not path and not hold_return_progress:
            route_ticks = 0
        attack_target_id = _nullable_string(
            entity.get("attack_target_id"), "entity.attack_target_id"
        )
        if pursue_target and attack_target_id is None:
            raise PersistenceError(f"entity {entity_id} cannot pursue without an attack target")
        if attack_gather_point is not None and (
            not pursue_target
            or not kind.profile.movable
            or kind.profile.attack_damage <= 0
            or not game_map.contains(attack_gather_point)
        ):
            raise PersistenceError(f"entity {entity_id} has an invalid attack gathering intent")
        if order_queue and (hold_anchor is not None or not kind.profile.movable):
            raise PersistenceError(f"entity {entity_id} cannot carry a direct order queue")
        for order in order_queue:
            if not game_map.contains(order.position):
                raise PersistenceError(f"entity {entity_id} has an out-of-map queued order")
            if order.kind is OrderKind.MOVE and (
                not game_map.is_passable(order.position)
                or (order.group_goal is not None and not game_map.is_passable(order.group_goal))
            ):
                raise PersistenceError(f"entity {entity_id} has an invalid queued movement target")
        if hold_anchor is not None and (path or move_target is not None or pursue_target):
            raise PersistenceError(
                "Hold recovery cannot carry an ordinary movement or pursuit order"
            )
        if kind.profile.category is EntityCategory.BUILDING and (
            path
            or move_target is not None
            or progress_target is not None
            or pursue_target
            or (
                state not in {UnitState.IDLE, UnitState.PRODUCING}
                and not (
                    state is UnitState.ATTACKING
                    and kind.profile.attack_damage > 0
                    and construction_progress is None
                )
            )
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
            owner_id=owner_id,
            position=position,
            health=health,
            state=state,
            move_target=move_target,
            path=path,
            path_cost=path_cost,
            order_queue=order_queue,
            formation_group=formation_group,
            formation_offset=formation_offset,
            formation_goal=formation_goal,
            formation_leg=formation_leg,
            attack_gather_point=attack_gather_point,
            attack_target_id=attack_target_id,
            repair_target_id=_nullable_string(
                entity.get("repair_target_id"), "entity.repair_target_id"
            ),
            reclaim_target_id=_nullable_string(
                entity.get("reclaim_target_id"), "entity.reclaim_target_id"
            ),
            pursue_target=pursue_target,
            attack_cooldown=_integer(
                entity.get("attack_cooldown"), "entity.attack_cooldown", minimum=0
            ),
            last_attacker_id=last_attacker_id,
            last_attacked_tick=last_attacked_tick,
            last_player_control_tick=last_player_control_tick,
            progress_target=progress_target,
            progress_distance=progress_distance,
            no_progress_ticks=no_progress_ticks,
            collision_pressure=collision_pressure,
            route_ticks=route_ticks,
            hold_anchor=hold_anchor,
            recovery_path=recovery_path,
            recovery_retry_tick=recovery_retry_tick,
            contact_wait_ticks=contact_wait_ticks,
            defend_arrived=defend_arrived,
            recovery_blocked_cells=recovery_blocked_cells,
            recovery_request_tick=recovery_request_tick,
            recovery_released=recovery_released,
            construction_progress=construction_progress,
            upgrade=UpgradeState(tier=tier, remaining_ticks=remaining),
            production_deployment=deployment,
        )
    formation_owners: dict[str, str] = {}
    formation_goals: dict[tuple[str, str], Point] = {}
    for loaded_entity in entities.values():
        group = loaded_entity.formation_group
        offset = loaded_entity.formation_offset
        if group is not None:
            assert offset is not None
            if abs(offset.x) > game_map.width or abs(offset.y) > game_map.height:
                raise PersistenceError("formation offset exceeds map dimensions")
            if formation_owners.setdefault(group, loaded_entity.owner_id) != loaded_entity.owner_id:
                raise PersistenceError("formation cannot mix owners")
        for goal, leg, destination in (
            (loaded_entity.formation_goal, loaded_entity.formation_leg, loaded_entity.move_target),
            *(
                (order.formation_goal, order.formation_leg, order.position)
                for order in loaded_entity.order_queue
            ),
        ):
            if goal is None:
                continue
            assert group is not None and leg is not None and offset is not None
            if (
                destination is not None
                and destination.distance_to(Point(goal.x + offset.x, goal.y + offset.y)) > 1e-7
            ):
                raise PersistenceError("formation destination disagrees with its offset")
            previous = formation_goals.setdefault((group, leg), goal)
            if previous.distance_to(goal) > 1e-7:
                raise PersistenceError("formation leg has inconsistent goals")
        if loaded_entity.formation_leg is not None and (
            loaded_entity.pursue_target
            or loaded_entity.repair_target_id is not None
            or loaded_entity.reclaim_target_id is not None
            or loaded_entity.hold_anchor is not None
        ):
            raise PersistenceError("formation movement conflicts with active work")
        for order in loaded_entity.order_queue:
            if order.kind is OrderKind.MOVE:
                continue
            target = entities.get(order.target_entity_id or "")
            if target is None:
                raise PersistenceError("queued order has an unknown target")
            if order.kind is OrderKind.ATTACK:
                if (
                    loaded_entity.profile.attack_damage <= 0
                    or target.owner_id == loaded_entity.owner_id
                ):
                    raise PersistenceError("queued attack has an invalid target or attacker")
            elif (
                not loaded_entity.profile.can_repair
                or (
                    not target.is_complete
                    and loaded_entity.is_movable
                    and not loaded_entity.profile.can_build
                )
                or target.category is not EntityCategory.BUILDING
                or target.owner_id != loaded_entity.owner_id
            ):
                raise PersistenceError("queued repair has an invalid target or builder")
        if loaded_entity.reclaim_target_id is not None:
            target = entities.get(loaded_entity.reclaim_target_id)
            if (
                not loaded_entity.profile.can_reclaim
                or target is None
                or target.owner_id != loaded_entity.owner_id
                or target.category is not EntityCategory.BUILDING
                or loaded_entity.repair_target_id is not None
                or loaded_entity.pursue_target
            ):
                raise PersistenceError("entity has an invalid reclaim target")
        if loaded_entity.repair_target_id is not None:
            target = entities.get(loaded_entity.repair_target_id)
            if (
                target is None
                or target.owner_id != loaded_entity.owner_id
                or not loaded_entity.is_complete
                or (
                    not target.is_complete
                    and loaded_entity.is_movable
                    and not loaded_entity.profile.can_build
                )
                or (not loaded_entity.is_movable and target is loaded_entity)
                or (
                    loaded_entity.profile.movable
                    and loaded_entity.profile.can_repair
                    and (
                        target.category is not EntityCategory.BUILDING
                        or loaded_entity.pursue_target
                    )
                )
                or not loaded_entity.profile.can_repair
            ):
                raise PersistenceError("entity has an invalid repair target")
        if (
            loaded_entity.attack_target_id is not None
            and loaded_entity.attack_target_id not in entities
        ):
            raise PersistenceError(f"entity {loaded_entity.entity_id} has an unknown attack target")
        if (
            loaded_entity.last_attacker_id is not None
            and loaded_entity.last_attacker_id not in entities
        ):
            raise PersistenceError(f"entity {loaded_entity.entity_id} has an unknown last attacker")
    if not entities:
        raise PersistenceError("saved state must contain at least one entity")
    return entities


def _load_unit_orders(value: object) -> list[UnitOrder]:
    orders = []
    for raw in _list(value, "entity.order_queue"):
        data = _mapping(raw, "entity.order_queue item")
        try:
            kind = OrderKind(_string(data.get("kind"), "queued order.kind"))
        except ValueError as error:
            raise PersistenceError("queued order has an invalid kind") from error
        position = _point(data.get("position"), "queued order.position")
        target = _nullable_string(data.get("target_entity_id"), "queued order.target_entity_id")
        goal_raw = data.get("group_goal")
        group_goal = None if goal_raw is None else _point(goal_raw, "queued order.group_goal")
        if (kind is OrderKind.MOVE) != (target is None):
            raise PersistenceError("queued order kind disagrees with its target")
        if group_goal is not None and kind is not OrderKind.MOVE:
            raise PersistenceError("only queued movement may carry a group goal")
        formation_goal = (
            None
            if data.get("formation_goal") is None
            else _point(data["formation_goal"], "queued formation goal")
        )
        formation_leg = _nullable_string(data.get("formation_leg"), "queued formation leg")
        if (formation_goal is None) != (formation_leg is None) or (
            formation_leg is not None and kind is not OrderKind.MOVE
        ):
            raise PersistenceError("invalid queued formation leg")
        orders.append(UnitOrder(kind, position, target, group_goal, formation_goal, formation_leg))
    return orders


def _build_occupancy(simulation: Simulation) -> OccupancyGrid:
    occupancy = OccupancyGrid(simulation.game_map.width, simulation.game_map.height)
    mobile_ids = frozenset(key for key, entity in simulation.entities.items() if entity.is_movable)
    for entity_id in sorted(simulation.entities):
        entity = simulation.entities[entity_id]
        if any(not simulation.game_map.is_cell_passable(cell) for cell in entity.occupied_cells):
            raise PersistenceError(f"entity {entity_id} occupies impassable terrain")
        try:
            occupancy.place(
                entity_id, entity.occupied_cells, mobile_ids if entity.is_movable else frozenset()
            )
        except OccupancyError as error:
            raise PersistenceError(f"invalid saved occupancy: {error}") from error
    return occupancy


def _load_projectiles(raw_data: object, simulation: Simulation) -> dict[str, Projectile]:
    data = _mapping(raw_data, "state.projectiles")
    projectiles: dict[str, Projectile] = {}
    for projectile_id, raw_projectile in data.items():
        item = _mapping(raw_projectile, f"projectile {projectile_id}")
        if item.get("id") != projectile_id:
            raise PersistenceError("projectile key and ID differ")
        try:
            weapon_kind = EntityKind(_string(item.get("weapon_kind"), "projectile.weapon_kind"))
        except ValueError as error:
            raise PersistenceError(f"invalid projectile weapon kind: {error}") from error
        speed = _number(item.get("speed"), "projectile.speed", minimum=0.01)
        damage = _integer(item.get("damage"), "projectile.damage", minimum=1)
        if weapon_kind.profile.projectile_speed <= 0 or damage != weapon_kind.profile.attack_damage:
            raise PersistenceError("projectile weapon profile is invalid")
        position = _point(item.get("position"), "projectile.position")
        destination = _point(item.get("destination"), "projectile.destination")
        trajectory = list(_points(item.get("trajectory"), "projectile.trajectory"))
        if not trajectory or trajectory[-1] != position:
            raise PersistenceError("projectile trajectory must end at its current position")
        if any(not simulation.game_map.contains(point) for point in trajectory):
            raise PersistenceError("projectile trajectory leaves the map")
        if not simulation.game_map.contains(destination):
            raise PersistenceError("projectile destination leaves the map")
        projectiles[projectile_id] = Projectile(
            projectile_id=projectile_id,
            source_entity_id=_string(item.get("source_entity_id"), "projectile.source_entity_id"),
            target_entity_id=_string(item.get("target_entity_id"), "projectile.target_entity_id"),
            owner_id=_string(item.get("owner_id"), "projectile.owner_id"),
            weapon_kind=weapon_kind,
            position=position,
            destination=destination,
            damage=damage,
            speed=speed,
            trajectory=trajectory,
        )
    return projectiles


def _load_projectile_traces(raw_data: object, simulation: Simulation) -> list[ProjectileTrace]:
    traces: list[ProjectileTrace] = []
    for raw_trace in _list(raw_data, "state.projectile_traces"):
        item = _mapping(raw_trace, "projectile trace")
        try:
            weapon_kind = EntityKind(
                _string(item.get("weapon_kind"), "projectile trace weapon_kind")
            )
        except ValueError as error:
            raise PersistenceError(f"invalid projectile trace weapon kind: {error}") from error
        points = _points(item.get("points"), "projectile trace points")
        if not points or any(not simulation.game_map.contains(point) for point in points):
            raise PersistenceError("projectile trace has invalid points")
        expires_tick = _integer(
            item.get("expires_tick"), "projectile trace expires_tick", minimum=0
        )
        if expires_tick <= simulation.tick:
            raise PersistenceError("saved projectile trace is already expired")
        traces.append(
            ProjectileTrace(
                _string(item.get("projectile_id"), "projectile trace projectile_id"),
                weapon_kind,
                points,
                expires_tick,
            )
        )
    return traces


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
        created_tick = _past_tick(
            data.get("created_tick"), "automation.created_tick", simulation.tick
        )
        modified_tick = _past_tick(
            data.get("modified_tick"), "automation.modified_tick", simulation.tick
        )
        if modified_tick < created_tick:
            raise PersistenceError("automation modified_tick cannot precede created_tick")
        parameters = _load_automation_parameters(
            kind, data.get("parameters"), entity_ids, simulation, terminal=status.terminal
        )
        if not status.terminal and any(
            entity_id not in simulation.entities
            and not (
                isinstance(parameters, RepairParameters)
                and parameters.phases[entity_id] is RepairPhase.DONE
            )
            for entity_id in entity_ids
        ):
            raise PersistenceError(f"automation {automation_id} references an invalid entity")
        if kind is AutomationKind.CONSTRUCTION:
            if "site_entity_id" not in _mapping(data.get("parameters"), "construction parameters"):
                raise PersistenceError("construction.site_entity_id is required")
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
    for entity in simulation.entities.values():
        deployment = entity.production_deployment
        if deployment is None or deployment.defend_automation_id is None:
            continue
        defense = automations.get(deployment.defend_automation_id)
        if (
            defense is None
            or defense.owner_id != entity.owner_id
            or not isinstance(defense.parameters, DefendParameters)
            or not defense.parameters.production_defense
            or defense.parameters.target != deployment.target
            or defense.parameters.auto_repair != deployment.auto_repair
        ):
            raise PersistenceError("production deployment references an invalid defense")
    production_defenses: set[tuple[str, SpatialTarget]] = set()
    for automation in automations.values():
        if (
            isinstance(automation.parameters, DefendParameters)
            and automation.parameters.production_defense
        ):
            if not isinstance(automation.parameters.target, PolygonRegion | PolylineTarget):
                raise PersistenceError("production defense requires a line or area")
            if not automation.status.terminal:
                key = automation.owner_id, automation.parameters.target
                if key in production_defenses:
                    raise PersistenceError(
                        "duplicate production defense for the same owner and target"
                    )
                production_defenses.add(key)
        if automation.transition_history[-1].reason_code != automation.reason_code:
            raise PersistenceError("automation reason does not match transition history")
        if automation.transition_history[-1].tick > automation.modified_tick:
            raise PersistenceError("automation modified tick precedes its last transition")
        if automation.transition_history[0].tick != automation.created_tick:
            raise PersistenceError("automation created tick does not match transition history")
        if any(
            simulation.entities[entity_id].owner_id != automation.owner_id
            for entity_id in automation.entity_ids
            if entity_id in simulation.entities
        ):
            raise PersistenceError("automation references an entity owned by another player")
        if isinstance(automation.parameters, ReinforcementParameters):
            target = automations.get(automation.parameters.target_automation_id)
            if target is None or target.owner_id != automation.owner_id:
                raise PersistenceError("reinforcement references an invalid target automation")
        elif isinstance(automation.parameters, ProductionParameters):
            defend_id = automation.parameters.defend_automation_id
            if defend_id is not None:
                target = automations.get(defend_id)
                if (
                    target is None
                    or target.kind is not AutomationKind.DEFEND
                    or target.owner_id != automation.owner_id
                    or not isinstance(target.parameters, DefendParameters)
                    or not target.parameters.production_defense
                    or target.parameters.target != automation.parameters.defend_target
                ):
                    raise PersistenceError("production references an invalid defend automation")
            patrol_id = automation.parameters.patrol_automation_id
            if patrol_id is not None:
                target = automations.get(patrol_id)
                if (
                    target is None
                    or target.kind is not AutomationKind.PATROL
                    or target.owner_id != automation.owner_id
                    or not isinstance(target.parameters, PatrolParameters)
                    or target.parameters.target != automation.parameters.patrol_target
                ):
                    raise PersistenceError("production references an invalid patrol automation")
        elif isinstance(automation.parameters, RepairParameters):
            for entity_id, building_id in automation.parameters.destinations.items():
                if automation.status.terminal or automation.parameters.phases[entity_id] in {
                    RepairPhase.DONE,
                    RepairPhase.RETURNING,
                }:
                    continue
                building = simulation.entities.get(building_id)
                if building is None or building.kind not in {
                    EntityKind.REPAIR_HUB,
                    EntityKind.FACTORY,
                    EntityKind.COMMAND_CENTER,
                }:
                    raise PersistenceError("repair references an invalid destination")
                if not building.is_complete:
                    raise PersistenceError("repair destination is unfinished")
            for resume_id in automation.parameters.resume_automation_ids.values():
                if resume_id is not None and resume_id not in automations:
                    raise PersistenceError("repair references an invalid resume automation")


def _validate_construction_sites(simulation: Simulation) -> None:
    claimed: set[str] = set()
    for automation in simulation.automations.values():
        parameters = automation.parameters
        if not isinstance(parameters, ConstructionParameters):
            continue
        site_id = parameters.site_entity_id
        if site_id is None:
            continue
        site = simulation.entities.get(site_id)
        if site is None:
            if not automation.status.terminal:
                raise PersistenceError("construction references a missing site")
            continue
        if (
            site.kind is not parameters.building_kind
            or site.position != parameters.position
            or site.owner_id != automation.owner_id
            or not parameters.cost_paid
        ):
            raise PersistenceError("construction site does not match its job")
        if not automation.status.terminal:
            if (
                site_id in claimed
                or site.is_complete
                or site.construction_progress
                != parameters.construction_value / parameters.required_value
            ):
                raise PersistenceError("construction site progress or ownership is inconsistent")
            claimed.add(site_id)


def _load_automation_parameters(
    kind: AutomationKind,
    raw_data: object,
    entity_ids: list[str],
    simulation: Simulation,
    *,
    terminal: bool = False,
) -> (
    PatrolParameters
    | DefendParameters
    | ProductionParameters
    | ReinforcementParameters
    | RepairParameters
    | EconomyParameters
    | ConstructionParameters
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
        return PatrolParameters(
            target,
            waypoints,
            indices,
            auto_repair=_boolean(data.get("auto_repair"), "patrol.auto_repair"),
        )
    if kind is AutomationKind.DEFEND:
        try:
            target = target_from_dict(data.get("target"))
        except ValueError as error:
            raise PersistenceError(f"invalid defend target: {error}") from error
        stations_data = _mapping(data.get("stations"), "defend.stations")
        if set(stations_data) != set(entity_ids):
            raise PersistenceError("defend stations must match its entities")
        gathering_point = _boolean(
            data.get("gathering_point", data.get("compact_center", False)),
            "defend.gathering_point",
        )
        deployment_slots = _points(data.get("deployment_slots", []), "defend.deployment_slots")
        stations = {
            entity_id: _point(stations_data[entity_id], "defend station")
            for entity_id in entity_ids
        }
        center = target_center(target)
        default_radius = max(
            (point.distance_to(center) for point in stations.values()),
            default=0.0,
        )
        assembly_radius = _number(
            data.get("assembly_radius", default_radius),
            "defend.assembly_radius",
            minimum=0.0,
        )
        if gathering_point and not deployment_slots:
            raise PersistenceError("gathering defense deployment slots are invalid")
        if deployment_slots:
            if len(set(deployment_slots)) != len(deployment_slots):
                raise PersistenceError("gathering defense deployment slots are invalid")
            if not terminal and any(
                not simulation.game_map.is_passable(point)
                or simulation.game_map.cell_for(point) in simulation._building_cells()
                for point in deployment_slots
            ):
                raise PersistenceError("gathering defense uses an impassable deployment slot")
            if assembly_radius + 1e-9 < default_radius:
                raise PersistenceError("gathering defense radius excludes a station")
        return DefendParameters(
            target,
            stations,
            gathering_point,
            deployment_slots,
            assembly_radius,
            auto_repair=_boolean(data.get("auto_repair"), "defend.auto_repair"),
            production_defense=_boolean(
                data.get("production_defense"), "defend.production_defense"
            ),
        )
    if kind is AutomationKind.PRODUCTION:
        try:
            unit_kind = EntityKind(_string(data.get("unit_kind"), "production.unit_kind"))
        except ValueError as error:
            raise PersistenceError(f"invalid production kind: {error}") from error
        if unit_kind.profile.category is not EntityCategory.UNIT:
            raise PersistenceError("production unit_kind must be a unit")
        factory_id = _string(data.get("factory_id"), "production.factory_id")
        if not terminal and (
            factory_id not in simulation.entities
            or unit_kind not in simulation.entities[factory_id].kind.production_kinds
            or not simulation.entities[factory_id].is_complete
        ):
            raise PersistenceError("production references an invalid factory")
        rally_data = data.get("rally_point")
        defend_data = data.get("defend_target")
        patrol_data = data.get("patrol_target")
        try:
            defend_target = None if defend_data is None else target_from_dict(defend_data)
            if defend_target is not None and not terminal:
                simulation._gathering_slots(defend_target, 1, collision_radius(unit_kind))
            patrol_target = None if patrol_data is None else target_from_dict(patrol_data)
            if patrol_target is not None:
                build_patrol_waypoints(patrol_target, simulation.game_map)
        except ValueError as error:
            raise PersistenceError(f"invalid production automation target: {error}") from error
        if defend_target is not None and patrol_target is not None:
            raise PersistenceError("production cannot have defend and patrol targets")
        continuous = _boolean(data.get("continuous", False), "production.continuous")
        target_count = _integer(data.get("target_count"), "production.target_count", minimum=1)
        produced_count = _integer(
            data.get("produced_count"), "production.produced_count", minimum=0
        )
        if not continuous and produced_count > target_count:
            raise PersistenceError("production count exceeds target")
        raw_sequence = _list(data.get("sequence", []), "production.sequence")
        sequence: list[tuple[EntityKind, int]] = []
        for index, raw_item in enumerate(raw_sequence):
            item = _list(raw_item, f"production.sequence[{index}]")
            if len(item) != 2:
                raise PersistenceError("production sequence entry must contain kind and quantity")
            try:
                sequence_kind = EntityKind(_string(item[0], "production sequence kind"))
            except ValueError as error:
                raise PersistenceError(f"invalid production sequence kind: {error}") from error
            quantity = _integer(item[1], "production sequence quantity", minimum=1)
            if sequence_kind.profile.category is not EntityCategory.UNIT:
                raise PersistenceError("production sequence kind must be a unit")
            if (
                not terminal
                and sequence_kind not in simulation.entities[factory_id].kind.production_kinds
            ):
                raise PersistenceError("production sequence kind is unsupported by its source")
            sequence.append((sequence_kind, quantity))
        sequence_index = _integer(
            data.get("sequence_index", 0), "production.sequence_index", minimum=0
        )
        if sequence and sequence_index >= len(sequence) and produced_count < target_count:
            raise PersistenceError("production sequence index is out of range")
        if sequence and sequence_index > len(sequence):
            raise PersistenceError("production sequence index is out of range")
        timing_kind = sequence[min(sequence_index, len(sequence) - 1)][0] if sequence else unit_kind
        build_ticks = _integer(data.get("build_ticks"), "production.build_ticks", minimum=1)
        if build_ticks != timing_kind.profile.production_ticks:
            raise PersistenceError("production build_ticks differs from its unit profile")
        progress_ticks = _integer(
            data.get("progress_ticks"), "production.progress_ticks", minimum=0
        )
        cost_paid = _boolean(data.get("cost_paid"), "production.cost_paid")
        # A paid unit can wait at a blocked exit across pause/resume cycles.
        # Earlier runtime increments retained that completed progress on resume.
        if progress_ticks > build_ticks and not cost_paid:
            raise PersistenceError("production progress exceeds its build time")
        return ProductionParameters(
            factory_id=factory_id,
            unit_kind=unit_kind,
            target_count=target_count,
            build_ticks=build_ticks,
            rally_point=None if rally_data is None else _point(rally_data, "rally_point"),
            produced_count=produced_count,
            progress_ticks=progress_ticks,
            produced_entity_ids=_bounded_production_history(data.get("produced_entity_ids")),
            cost_paid=cost_paid,
            continuous=continuous,
            auto_repair=_boolean(data.get("auto_repair"), "production.auto_repair"),
            defend_target=defend_target,
            defend_automation_id=_nullable_string(
                data.get("defend_automation_id"),
                "production.defend_automation_id",
            ),
            patrol_target=patrol_target,
            patrol_automation_id=_nullable_string(
                data.get("patrol_automation_id"),
                "production.patrol_automation_id",
            ),
            sequence=tuple(sequence),
            sequence_index=sequence_index,
            sequence_produced=_integer(
                data.get("sequence_produced", 0),
                "production.sequence_produced",
                minimum=0,
            ),
        )
    if kind is AutomationKind.CONSTRUCTION:
        try:
            building_kind = EntityKind(
                _string(data.get("building_kind"), "construction.building_kind")
            )
        except ValueError as error:
            raise PersistenceError(f"invalid construction kind: {error}") from error
        builder_id = _string(data.get("builder_id"), "construction.builder_id")
        builder_ids = _string_list(
            data.get("builder_ids", [builder_id]), "construction.builder_ids"
        )
        if (
            builder_ids != entity_ids
            or (not builder_ids and not terminal)
            or (
                not terminal
                and any(
                    item not in simulation.entities
                    or not simulation.entities[item].profile.can_build
                    for item in builder_ids
                )
            )
        ):
            raise PersistenceError("construction references an invalid builder")
        required_value = _number(
            data.get("required_value"), "construction.required_value", minimum=1
        )
        construction_value = _number(
            data.get("construction_value", 0),
            "construction.construction_value",
            minimum=0,
        )
        if construction_value > required_value:
            raise PersistenceError("construction value cannot exceed required value")
        return ConstructionParameters(
            builder_id,
            building_kind,
            _point(data.get("position"), "construction.position"),
            required_value,
            construction_value,
            _boolean(data.get("cost_paid", False), "construction.cost_paid"),
            _nullable_string(
                data.get("constructed_entity_id"), "construction.constructed_entity_id"
            ),
            builder_ids,
            _nullable_string(data.get("site_entity_id"), "construction.site_entity_id"),
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
        if generator_ids != entity_ids or (
            not terminal
            and any(
                item not in simulation.entities
                or not simulation.entities[item].kind.profile.income_per_second
                for item in generator_ids
            )
        ):
            raise PersistenceError("economy generators must match resource-generator entities")
        return EconomyParameters(
            generator_ids,
            _integer(data.get("target_resources"), "economy.target_resources", minimum=1),
            _integer(data.get("income_per_cycle"), "economy.income_per_cycle", minimum=1),
            _integer(data.get("income_cycle_ticks"), "economy.income_cycle_ticks", minimum=1),
            _integer(data.get("collected"), "economy.collected", minimum=0),
            _integer(data.get("starting_resources", 0), "economy.starting_resources", minimum=0),
        )
    destinations = _string_mapping(data.get("destinations"), "repair.destinations")
    resume_ids = _nullable_string_mapping(
        data.get("resume_automation_ids"), "repair.resume_automation_ids"
    )
    phase_data = _mapping(data.get("phases"), "repair.phases")
    return_data = _mapping(data.get("return_positions", {}), "repair.return_positions")
    if not return_data:
        if any(entity_id not in simulation.entities for entity_id in entity_ids):
            raise PersistenceError("historical repair members require saved return positions")
        return_data = {
            entity_id: [
                simulation.entities[entity_id].position.x,
                simulation.entities[entity_id].position.y,
            ]
            for entity_id in entity_ids
        }
    if (
        set(destinations) != set(entity_ids)
        or set(resume_ids) != set(entity_ids)
        or set(phase_data) != set(entity_ids)
        or set(return_data) != set(entity_ids)
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
        return_positions={
            entity_id: _point(return_data[entity_id], "repair return position")
            for entity_id in entity_ids
        },
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
        if isinstance(automation.parameters, RepairParameters):
            expected.difference_update(
                entity_id
                for entity_id, phase in automation.parameters.phases.items()
                if phase is RepairPhase.DONE
            )
        if automation.status.terminal:
            if assigned:
                raise PersistenceError("terminal automations cannot retain assignments")
        elif automation.kind is AutomationKind.REINFORCEMENT:
            if assigned:
                raise PersistenceError("reinforcement automations do not own entities")
        elif (
            automation.kind is AutomationKind.PRODUCTION
            and automation.status is AutomationStatus.WAITING
            and automation.reason_code == "FACTORY_QUEUED"
        ):
            if assigned:
                raise PersistenceError("queued production cannot own its factory")
        elif (
            automation.kind is AutomationKind.CONSTRUCTION
            and automation.status is AutomationStatus.WAITING
            and automation.reason_code == "BUILDERS_QUEUED"
        ):
            if assigned:
                raise PersistenceError("queued construction cannot own builders")
        elif automation.status is AutomationStatus.PAUSED:
            if not assigned.issubset(expected):
                raise PersistenceError("paused automation assignments are invalid")
        elif assigned != expected:
            raise PersistenceError(
                f"automation {automation.automation_id} assignments are incomplete"
            )


def _validate_recovery_request_owners(simulation: Simulation) -> None:
    """Return phases belong only to holders or established defense stations."""
    for entity in simulation.entities.values():
        if entity.recovery_request_tick is None:
            continue
        if entity.is_movable and entity.hold_anchor is not None:
            continue
        automation = simulation.automations.get(simulation.assignments.get(entity.entity_id, ""))
        if (
            entity.is_movable
            and entity.defend_arrived
            and entity.state is UnitState.DEFENDING
            and automation is not None
            and automation.status is not AutomationStatus.PAUSED
            and isinstance(automation.parameters, DefendParameters)
            and entity.entity_id in automation.parameters.stations
        ):
            continue
        raise PersistenceError(
            f"entity {entity.entity_id} recovery request has no weak return owner"
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


def _load_events(raw_data: object, current_tick: int, *, retain: bool = True) -> list[Event]:
    events: list[Event] = []
    for expected_sequence, raw_event in enumerate(_list(raw_data, "state.events"), start=1):
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
        sequence = _integer(data.get("sequence"), "event.sequence", minimum=1)
        if sequence != expected_sequence:
            raise PersistenceError(
                "invalid saved event sequence: must be contiguous and start at one"
            )
        if retain:
            events.append(
                Event(
                    sequence=sequence,
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


def _load_command_history(
    raw_data: object, current_tick: int, *, retain: bool = True
) -> list[dict[str, object]]:
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
        if retain:
            history.append({"tick": tick, "command": command_to_dict(command)})
    return history


def _cell_set(raw_data: object, simulation: Simulation, field: str) -> set[tuple[int, int]]:
    return {_cell(cell, field, simulation.game_map) for cell in _list(raw_data, field)}


def _cell(value: object, field: str, game_map: GameMap) -> tuple[int, int]:
    data = _list(value, field)
    if len(data) != 2:
        raise PersistenceError(f"{field} cells must contain x and y")
    cell = (_integer(data[0], f"{field}.x", minimum=0), _integer(data[1], f"{field}.y", minimum=0))
    if not game_map.contains_cell(cell):
        raise PersistenceError(f"{field} contains an out-of-bounds cell")
    return cell


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


def _bounded_production_history(value: object) -> list[str]:
    history = _string_list(value, "production.produced_entity_ids")
    if len(history) > PRODUCTION_HISTORY_LIMIT:
        raise PersistenceError(
            "production.produced_entity_ids exceeds the current bounded history limit"
        )
    return history


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
