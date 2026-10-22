"""Validated direct-control and automation command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import (
    AutomationKind,
    DefendParameters,
    EconomyParameters,
    PatrolParameters,
    ProductionParameters,
    ReinforcementParameters,
    RepairParameters,
    RepairPhase,
    assign_formation_slots,
    build_patrol_waypoints,
    defend_parameters,
    production_parameters,
    reinforcement_parameters,
    target_center,
)
from airts.contracts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    Command,
    CommandResult,
    ControlAuthority,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
    SetAIIncomeMultiplierCommand,
    SetDeveloperModeCommand,
    StopCommand,
    ValidationFailure,
    ValidationPhase,
    error_reason,
    validate_auto_repair,
    validate_positive,
    validate_priority,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PolygonRegion, PolylineTarget, SpatialTarget
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.pathfinding import PathfindingError
from airts.systems import (
    automation_assignments,
    automation_lifecycle,
    automation_runtime,
    formations,
    movement,
    production_orders,
    unit_orders,
)
from airts.systems.assignment_state import state_for_assignment
from airts.systems.repair_positions import select_repair_destinations
from airts.systems.validation import validate_entities, validate_geometry
from airts.world.entities import OrderKind, UnitOrder, UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def player_controlled_entity_ids(simulation: Simulation, command: Command) -> tuple[str, ...]:
    """Capture affected fighters before dispatch; accepted external orders stamp them afterward."""
    if isinstance(
        command,
        (
            MoveCommand,
            AttackCommand,
            StopCommand,
            HoldPositionCommand,
            CreatePatrolCommand,
            CreateDefendCommand,
            CreateRepairAndReturnCommand,
        ),
    ):
        owner = command.owner_id
        ids = command.entity_ids
    elif isinstance(
        command,
        (
            ModifyAutomationCommand,
            PauseAutomationCommand,
            ResumeAutomationCommand,
            CancelAutomationCommand,
        ),
    ):
        if isinstance(command, ModifyAutomationCommand) and command.target is None:
            return ()
        owner = command.owner_id
        automation = simulation.automations.get(command.automation_id)
        if automation is None:
            return ()
        ids = tuple(
            key
            for key in automation.entity_ids
            if simulation.assignments.get(key) == automation.automation_id
            or simulation.suspended_assignments.get(key) == automation.automation_id
        )
    else:
        return ()
    if owner != "player":
        return ()
    return tuple(
        key
        for key in ids
        if key in simulation.entities
        and simulation.entities[key].owner_id == "player"
        and simulation.entities[key].kind.profile.combat_support
    )


def set_ai_income_multiplier(
    simulation: Simulation, command: SetAIIncomeMultiplierCommand
) -> CommandResult:
    if type(command.multiplier) is not int or command.multiplier not in (1, 5):
        return simulation._reject_validation(
            "set_ai_income_multiplier",
            ValidationFailure(
                ValidationPhase.SCHEMA, "AI_INCOME_MULTIPLIER_MUST_BE_1_OR_5", "multiplier"
            ),
        )
    if command.owner_id != "player" or command.owner_id not in simulation.resources:
        return simulation._reject_validation(
            "set_ai_income_multiplier",
            ValidationFailure(ValidationPhase.OWNERSHIP, "DEVELOPER_MODE_PLAYER_ONLY", "owner_id"),
        )
    simulation.ai_income_multiplier = command.multiplier
    return simulation._accept("set_ai_income_multiplier")


def set_developer_mode(simulation: Simulation, command: SetDeveloperModeCommand) -> CommandResult:
    if type(command.enabled) is not bool:
        return simulation._reject_validation(
            "set_developer_mode",
            ValidationFailure(ValidationPhase.SCHEMA, "ENABLED_MUST_BE_BOOLEAN", "enabled"),
        )
    if command.owner_id != "player" or command.owner_id not in simulation.resources:
        return simulation._reject_validation(
            "set_developer_mode",
            ValidationFailure(ValidationPhase.OWNERSHIP, "DEVELOPER_MODE_PLAYER_ONLY", "owner_id"),
        )
    if simulation.developer_mode != command.enabled:
        simulation.developer_mode = command.enabled
        simulation.developer_income_ticks = 0
    return simulation._accept("set_developer_mode")


def modify_automation(simulation: Simulation, command: ModifyAutomationCommand) -> CommandResult:
    automation, failure = automation_lifecycle.owned_automation(
        simulation, command.automation_id, command.owner_id
    )
    if failure is not None:
        return simulation._reject_validation("modify_automation", failure)
    assert automation is not None
    if automation.status.terminal:
        return simulation._reject_validation(
            "modify_automation",
            ValidationFailure(ValidationPhase.CAPABILITY, "AUTOMATION_TERMINAL", "automation_id"),
        )
    if all(
        value is None
        for value in (
            command.title,
            command.priority,
            command.target,
            command.minimum_units,
            command.target_count,
            command.auto_repair,
        )
    ):
        return simulation._reject_validation(
            "modify_automation",
            ValidationFailure(ValidationPhase.SCHEMA, "NO_CHANGES", "automation_id"),
        )
    if command.title is not None and not command.title.strip():
        return simulation._reject_validation(
            "modify_automation",
            ValidationFailure(ValidationPhase.SCHEMA, "TITLE_EMPTY", "title"),
        )
    if command.priority is not None:
        failure = validate_priority(command.priority)
        if failure is not None:
            return simulation._reject_validation("modify_automation", failure)
    if command.auto_repair is not None:
        failure = validate_auto_repair(command.auto_repair)
        if failure is not None:
            return simulation._reject_validation("modify_automation", failure)
        if not isinstance(
            automation.parameters, PatrolParameters | DefendParameters | ProductionParameters
        ):
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "AUTO_REPAIR_NOT_EDITABLE", "auto_repair"
                ),
            )
    new_parameters: PatrolParameters | DefendParameters | None = None
    production_target: PolygonRegion | PolylineTarget | None = None
    if command.target is not None:
        if automation.kind is AutomationKind.PATROL:
            try:
                waypoints = build_patrol_waypoints(command.target, simulation.game_map)
                movement.validate_patrol_paths(simulation, tuple(automation.entity_ids), waypoints)
            except (ValueError, PathfindingError) as error:
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(ValidationPhase.PATH, error_reason(error), "target"),
                )
            indices = {entity_id: 0 for entity_id in automation.entity_ids}
            assert isinstance(automation.parameters, PatrolParameters)
            new_parameters = PatrolParameters(
                command.target, waypoints, indices, auto_repair=automation.parameters.auto_repair
            )
        elif automation.kind is AutomationKind.DEFEND:
            existing_defend = defend_parameters(automation)
            if existing_defend.production_defense and not isinstance(
                command.target, PolygonRegion | PolylineTarget
            ):
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(
                        ValidationPhase.SPATIAL,
                        "PRODUCTION_DEFENSE_REQUIRES_LINE_OR_AREA",
                        "target",
                    ),
                )
            if existing_defend.production_defense:
                assert isinstance(command.target, PolygonRegion | PolylineTarget)
                incumbent = production_orders.shared_production_defense(
                    simulation, automation.owner_id, command.target
                )
                if incumbent is not None and incumbent is not automation:
                    return simulation._reject_validation(
                        "modify_automation",
                        ValidationFailure(
                            ValidationPhase.CONFLICT, "PRODUCTION_DEFENSE_TARGET_IN_USE", "target"
                        ),
                    )
            gathering_point = (
                isinstance(command.target, PolygonRegion)
                if existing_defend.production_defense
                else existing_defend.gathering_point
            )
            try:
                stations, slots, _ = _allocate_defend_stations(
                    simulation,
                    command.target,
                    tuple(automation.entity_ids),
                    gathering_point=gathering_point,
                )
                movement.validate_paths(
                    simulation, tuple(automation.entity_ids), tuple(stations.values())
                )
            except (ValueError, PathfindingError) as error:
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(ValidationPhase.PATH, error_reason(error), "target"),
                )
            new_parameters = DefendParameters(
                command.target,
                stations,
                gathering_point=gathering_point,
                auto_repair=existing_defend.auto_repair,
                production_defense=existing_defend.production_defense,
                deployment_slots=slots,
                assembly_radius=(
                    max(point.distance_to(target_center(command.target)) for point in slots)
                    if slots
                    else 0.0
                ),
            )
        elif automation.kind is AutomationKind.PRODUCTION:
            parameters = production_parameters(automation)
            if not parameters.continuous:
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(
                        ValidationPhase.CAPABILITY,
                        "PRODUCTION_DEFENSE_REQUIRES_CONTINUOUS_LOOP",
                        "target",
                    ),
                )
            if not isinstance(command.target, PolygonRegion | PolylineTarget):
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(
                        ValidationPhase.SPATIAL,
                        "PRODUCTION_DEFENSE_REQUIRES_LINE_OR_AREA",
                        "target",
                    ),
                )
            geometry_failure = validate_geometry(simulation, command.target)
            if geometry_failure is not None:
                return simulation._reject_validation("modify_automation", geometry_failure)
            try:
                production_orders.validate_defense_target(
                    simulation, command.target, parameters.current_unit_kind
                )
            except ValueError as error:
                return simulation._reject_validation(
                    "modify_automation",
                    ValidationFailure(ValidationPhase.SPATIAL, error_reason(error), "target"),
                )
            production_target = command.target
        else:
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(ValidationPhase.CAPABILITY, "TARGET_NOT_EDITABLE", "target"),
            )
    if command.minimum_units is not None:
        if automation.kind is not AutomationKind.REINFORCEMENT:
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "MINIMUM_UNITS_NOT_EDITABLE", "minimum_units"
                ),
            )
        failure = validate_positive(command.minimum_units, "minimum_units")
        if failure is not None:
            return simulation._reject_validation("modify_automation", failure)
    if command.target_count is not None:
        if automation.kind is not AutomationKind.PRODUCTION:
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "TARGET_COUNT_NOT_EDITABLE", "target_count"
                ),
            )
        parameters = production_parameters(automation)
        if command.target_count <= 0 or command.target_count < parameters.produced_count:
            return simulation._reject_validation(
                "modify_automation",
                ValidationFailure(
                    ValidationPhase.SCHEMA, "TARGET_COUNT_BELOW_PRODUCED", "target_count"
                ),
            )
    if command.auto_repair is not None:
        assert isinstance(
            automation.parameters, PatrolParameters | DefendParameters | ProductionParameters
        )
        automation.parameters.auto_repair = command.auto_repair
        if new_parameters is not None:
            new_parameters.auto_repair = command.auto_repair
        if isinstance(automation.parameters, ProductionParameters) and production_target is None:
            for linked_id in (
                automation.parameters.defend_automation_id,
                automation.parameters.patrol_automation_id,
            ):
                linked = simulation.automations.get(linked_id or "")
                if linked is not None and not linked.status.terminal:
                    assert isinstance(linked.parameters, PatrolParameters | DefendParameters)
                    linked.parameters.auto_repair = command.auto_repair
                    linked.modified_tick = simulation.tick
                    simulation.events.record(
                        simulation.tick,
                        EventType.AUTOMATION_MODIFIED,
                        linked.automation_id,
                        parameters=linked.parameters.to_dict(),
                    )
                    if (
                        isinstance(linked.parameters, DefendParameters)
                        and linked.parameters.production_defense
                    ):
                        production_orders.synchronize_production_defense(simulation, linked)
    if production_target is not None:
        production_orders.attach_production_defense(
            simulation, automation, production_target, auto_repair=command.auto_repair
        )
    if command.title is not None:
        automation.title = command.title.strip()
    if command.priority is not None:
        automation.priority = command.priority
    if new_parameters is not None:
        previous_parameters = automation.parameters
        automation.parameters = new_parameters
        for entity_id in automation.entity_ids:
            if simulation.assignments.get(entity_id) != automation.automation_id:
                continue
            entity = simulation.entities[entity_id]
            if entity.pursue_target:
                entity.attack_target_id = None
                entity.pursue_target = False
                entity.path.clear()
                entity.move_target = None
                entity.state = state_for_assignment(simulation, entity_id)
                movement.reset_movement_liveness(entity)
            if isinstance(new_parameters, DefendParameters) and isinstance(
                previous_parameters, DefendParameters
            ):
                if previous_parameters.stations.get(entity_id) == new_parameters.stations.get(
                    entity_id
                ):
                    continue
                entity = simulation.entities[entity_id]
                entity.clear_recovery_request()
                entity.defend_arrived = False
                entity.path.clear()
                entity.move_target = None
                movement.reset_movement_liveness(entity)
            else:
                simulation.entities[entity_id].clear_recovery_request()
    if command.minimum_units is not None:
        reinforcement_parameters(automation).minimum_units = command.minimum_units
    if (
        isinstance(automation.parameters, DefendParameters)
        and automation.parameters.production_defense
    ):
        production_orders.synchronize_production_defense(simulation, automation)
    if command.target_count is not None:
        production_parameters(automation).target_count = command.target_count
    automation.modified_tick = simulation.tick
    simulation.events.record(
        simulation.tick,
        EventType.AUTOMATION_MODIFIED,
        automation.automation_id,
        title=automation.title,
        priority=automation.priority,
        parameters=automation.parameters.to_dict(),
    )
    return simulation._accept("modify_automation", automation.automation_id)


def move(simulation: Simulation, command: MoveCommand) -> CommandResult:
    failure = validate_entities(
        simulation, command.entity_ids, command.owner_id, require_movable=True
    )
    if failure is not None:
        return simulation._reject_validation("move", failure)
    if not simulation.game_map.is_passable(command.target):
        return simulation._reject_validation(
            "move",
            ValidationFailure(
                ValidationPhase.SPATIAL,
                "TARGET_NOT_PASSABLE",
                "target",
                {"target": [command.target.x, command.target.y]},
            ),
        )
    try:
        continuing = (
            unit_orders.continuing_ids(simulation, command.entity_ids)
            if command.queued
            else frozenset()
        )
        origins = movement.move_origins(simulation, command.entity_ids, continuing=continuing)
        destinations, formation_goals = formations.destinations(
            simulation, command.entity_ids, command.target, origins
        )
        leg = formations.new_leg(simulation) if formation_goals else None
        if command.queued:
            shared_goal = min(
                destinations.values(),
                key=lambda point: (point.distance_to(command.target), point.y, point.x),
            )
            blocked = simulation._building_cells()
            for key in command.entity_ids:
                simulation._routes.validate_connection(origins[key], destinations[key], blocked)
            replacing = tuple(key for key in command.entity_ids if key not in continuing)
            paths = (
                movement.plan_group_paths(
                    simulation,
                    replacing,
                    {key: destinations[key] for key in replacing},
                    shared_goal=shared_goal,
                    compact_approach=not formation_goals,
                )
                if replacing
                else {}
            )
            automation_assignments.manual_override_many(simulation, replacing)
            for key in replacing:
                entity = simulation.entities[key]
                movement.start_path(
                    simulation,
                    entity,
                    destinations[key],
                    paths[key],
                    "human_queue",
                    UnitState.MOVING,
                )
                entity.formation_goal = formation_goals.get(key)
                entity.formation_leg = leg if key in formation_goals else None
            for key in command.entity_ids:
                if key in continuing:
                    simulation.entities[key].order_queue.append(
                        UnitOrder(
                            OrderKind.MOVE,
                            destinations[key],
                            group_goal=shared_goal,
                            formation_goal=formation_goals.get(key),
                            formation_leg=leg if key in formation_goals else None,
                        )
                    )
            return simulation._accept("move")
        paths = movement.plan_group_paths(
            simulation, command.entity_ids, destinations, compact_approach=not formation_goals
        )
    except PathfindingError as error:
        return simulation._reject_validation(
            "move",
            ValidationFailure(
                ValidationPhase.PATH,
                str(error),
                "target",
                {"target": [command.target.x, command.target.y]},
            ),
        )
    automation_assignments.manual_override_many(simulation, command.entity_ids)
    path_source = "human"
    for entity_id in command.entity_ids:
        simulation.entities[entity_id].pursue_target = False
        movement.start_path(
            simulation,
            simulation.entities[entity_id],
            destinations[entity_id],
            paths[entity_id],
            path_source,
            UnitState.MOVING,
        )
        simulation.entities[entity_id].formation_goal = formation_goals.get(entity_id)
        simulation.entities[entity_id].formation_leg = leg if entity_id in formation_goals else None
    return simulation._accept("move")


def attack(simulation: Simulation, command: AttackCommand) -> CommandResult:
    failure = validate_entities(simulation, command.entity_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("attack", failure)
    target = simulation.entities.get(command.target_entity_id)
    if target is None:
        return simulation._reject_validation(
            "attack",
            ValidationFailure(ValidationPhase.REFERENCE, "UNKNOWN_TARGET", "target_entity_id"),
        )
    if not simulation.are_hostile(target.owner_id, command.owner_id):
        return simulation._reject_validation(
            "attack",
            ValidationFailure(ValidationPhase.OWNERSHIP, "TARGET_IS_FRIENDLY", "target_entity_id"),
        )
    attackers = tuple(
        key
        for key in command.entity_ids
        if simulation.entities[key].profile.attack_damage > 0
        and simulation.entities[key].is_complete
        and (not command.queued or simulation.entities[key].is_movable)
    )
    if not attackers:
        return simulation._reject_validation(
            "attack",
            ValidationFailure(ValidationPhase.CAPABILITY, "ENTITY_CANNOT_ATTACK", "entity_ids"),
        )
    if command.queued:
        unit_orders.append_target_order(
            simulation,
            attackers,
            UnitOrder(OrderKind.ATTACK, target.selection_position, target.entity_id),
        )
        return simulation._accept("attack")
    automation_assignments.manual_override_many(simulation, attackers)
    for entity_id in attackers:
        entity = simulation.entities[entity_id]
        entity.path.clear()
        entity.move_target = None
        movement.reset_movement_liveness(entity)
        entity.attack_target_id = target.entity_id
        entity.attack_gather_point = target.selection_position if entity.is_movable else None
        entity.pursue_target = entity.is_movable
        entity.state = UnitState.ATTACKING
    return simulation._accept("attack")


def stop(
    simulation: Simulation, command: StopCommand | HoldPositionCommand, *, hold: bool
) -> CommandResult:
    failure = validate_entities(simulation, command.entity_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("hold_position" if hold else "stop", failure)
    automation_assignments.manual_override_many(simulation, command.entity_ids)
    for entity_id in command.entity_ids:
        entity = simulation.entities[entity_id]
        entity.path.clear()
        entity.move_target = None
        entity.pursue_target = False
        entity.state = UnitState.HOLDING if hold and entity.is_movable else UnitState.IDLE
        entity.hold_anchor = entity.position if hold and entity.is_movable else None
        movement.reset_movement_liveness(entity)
        simulation._movement_blocked.discard(entity_id)
    return simulation._accept("hold_position" if hold else "stop")


def create_patrol(simulation: Simulation, command: CreatePatrolCommand) -> CommandResult:
    failure = validate_auto_repair(command.auto_repair)
    if failure is not None:
        return simulation._reject_validation("create_patrol", failure)
    failure = validate_automation_common(
        simulation,
        command.entity_ids,
        command.owner_id,
        command.priority,
        command.title,
        require_movable=True,
    )
    if failure is not None:
        return simulation._reject_validation("create_patrol", failure)
    try:
        waypoints = build_patrol_waypoints(command.target, simulation.game_map)
        movement.validate_patrol_paths(simulation, command.entity_ids, waypoints)
    except (ValueError, PathfindingError) as error:
        return simulation._reject_validation(
            "create_patrol",
            ValidationFailure(ValidationPhase.PATH, error_reason(error), "target"),
        )
    automation = automation_lifecycle.new_automation(
        simulation,
        AutomationKind.PATROL,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(command.entity_ids),
        PatrolParameters(command.target, waypoints, auto_repair=command.auto_repair),
    )
    failure = automation_lifecycle.validate_claims(
        simulation, automation, command.entity_ids, replace_existing=True
    )
    if failure is not None:
        return simulation._reject_validation("create_patrol", failure)
    automation_assignments.activate(simulation, automation, command.entity_ids)
    return simulation._accept("create_patrol", automation.automation_id)


def create_defend(simulation: Simulation, command: CreateDefendCommand) -> CommandResult:
    failure = validate_auto_repair(command.auto_repair)
    if failure is not None:
        return simulation._reject_validation("create_defend", failure)
    failure = validate_automation_common(
        simulation,
        command.entity_ids,
        command.owner_id,
        command.priority,
        command.title,
        require_movable=True,
    )
    if failure is not None:
        return simulation._reject_validation("create_defend", failure)
    geometry_failure = validate_geometry(simulation, command.target)
    if geometry_failure is not None:
        return simulation._reject_validation("create_defend", geometry_failure)
    try:
        stations, slots, expanded = _allocate_defend_stations(
            simulation,
            command.target,
            command.entity_ids,
            gathering_point=command.gathering_point,
        )
        if expanded:
            reachable = simulation._gathering_reachable(command.target)
            if any(
                not reachable[
                    simulation.game_map.cell_for(simulation.entities[entity_id].position)[1],
                    simulation.game_map.cell_for(simulation.entities[entity_id].position)[0],
                ]
                for entity_id in command.entity_ids
            ):
                raise PathfindingError("NO_PATH")
        else:
            movement.validate_paths(simulation, command.entity_ids, tuple(stations.values()))
    except (ValueError, PathfindingError) as error:
        return simulation._reject_validation(
            "create_defend",
            ValidationFailure(ValidationPhase.PATH, error_reason(error), "target"),
        )
    automation = automation_lifecycle.new_automation(
        simulation,
        AutomationKind.DEFEND,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(command.entity_ids),
        DefendParameters(
            command.target,
            stations,
            gathering_point=command.gathering_point,
            auto_repair=command.auto_repair,
            deployment_slots=slots,
            assembly_radius=(
                max(point.distance_to(target_center(command.target)) for point in slots)
                if slots
                else 0.0
            ),
        ),
    )
    failure = automation_lifecycle.validate_claims(
        simulation, automation, command.entity_ids, replace_existing=True
    )
    if failure is not None:
        return simulation._reject_validation("create_defend", failure)
    automation_assignments.activate(simulation, automation, command.entity_ids)
    automation_runtime.coordinate_shared_defend_stations(
        simulation, command.target, command.owner_id
    )
    return simulation._accept("create_defend", automation.automation_id)


def create_reinforcement(
    simulation: Simulation, command: CreateReinforcementCommand
) -> CommandResult:
    priority_failure = validate_priority(command.priority)
    count_failure = validate_positive(command.minimum_units, "minimum_units")
    if priority_failure or count_failure:
        failure = priority_failure if priority_failure is not None else count_failure
        assert failure is not None
        return simulation._reject_validation("create_reinforcement", failure)
    failure = validate_entities(
        simulation, command.candidate_entity_ids, command.owner_id, require_movable=True
    )
    if failure is not None:
        return simulation._reject_validation("create_reinforcement", failure)
    target = simulation.automations.get(command.target_automation_id)
    if target is None:
        return simulation._reject_validation(
            "create_reinforcement",
            ValidationFailure(
                ValidationPhase.REFERENCE,
                "UNKNOWN_AUTOMATION",
                "target_automation_id",
            ),
        )
    if target.owner_id != command.owner_id:
        return simulation._reject_validation(
            "create_reinforcement",
            ValidationFailure(
                ValidationPhase.OWNERSHIP,
                "AUTOMATION_NOT_OWNED",
                "target_automation_id",
            ),
        )
    if target.kind not in {AutomationKind.PATROL, AutomationKind.DEFEND} or target.status.terminal:
        return simulation._reject_validation(
            "create_reinforcement",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "INVALID_REINFORCEMENT_TARGET",
                "target_automation_id",
            ),
        )
    automation = automation_lifecycle.new_automation(
        simulation,
        AutomationKind.REINFORCEMENT,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        [],
        ReinforcementParameters(
            command.target_automation_id,
            list(command.candidate_entity_ids),
            command.minimum_units,
        ),
    )
    automation_assignments.activate(simulation, automation, ())
    return simulation._accept("create_reinforcement", automation.automation_id)


def create_repair(simulation: Simulation, command: CreateRepairAndReturnCommand) -> CommandResult:
    if not 0 < command.health_threshold <= 1:
        return simulation._reject_validation(
            "create_repair_and_return",
            ValidationFailure(
                ValidationPhase.SCHEMA,
                "HEALTH_THRESHOLD_OUT_OF_RANGE",
                "health_threshold",
            ),
        )
    rate_failure = validate_positive(command.repair_rate, "repair_rate")
    if rate_failure is not None:
        return simulation._reject_validation("create_repair_and_return", rate_failure)
    failure = validate_entities(
        simulation,
        command.entity_ids,
        command.owner_id,
        require_movable=True,
    )
    if failure is not None:
        return simulation._reject_validation("create_repair_and_return", failure)
    eligible_ids = tuple(
        entity_id
        for entity_id in command.entity_ids
        if simulation.entities[entity_id].health
        / simulation.entities[entity_id].kind.profile.max_health
        < command.health_threshold
    )
    if not eligible_ids:
        return simulation._reject_validation(
            "create_repair_and_return",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "NO_UNITS_BELOW_REPAIR_THRESHOLD",
                "entity_ids",
                {"health_threshold": command.health_threshold},
            ),
        )
    failure = validate_automation_metadata(command.priority, command.title)
    if failure is not None:
        return simulation._reject_validation("create_repair_and_return", failure)
    try:
        destinations = select_repair_destinations(simulation, eligible_ids)
    except PathfindingError as error:
        return simulation._reject_validation(
            "create_repair_and_return",
            ValidationFailure(ValidationPhase.PATH, str(error), "entity_ids"),
        )
    automation = automation_lifecycle.new_automation(
        simulation,
        AutomationKind.REPAIR_AND_RETURN,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(eligible_ids),
        RepairParameters(
            command.health_threshold,
            command.repair_rate,
            destinations,
            {
                entity_id: simulation.suspended_assignments.get(entity_id)
                or simulation.assignments.get(entity_id)
                for entity_id in eligible_ids
            },
            {entity_id: RepairPhase.TRAVELING for entity_id in eligible_ids},
            {entity_id: simulation.entities[entity_id].position for entity_id in eligible_ids},
        ),
    )
    failure = automation_lifecycle.validate_claims(
        simulation, automation, eligible_ids, authority=ControlAuthority.EMERGENCY
    )
    if failure is not None:
        return simulation._reject_validation("create_repair_and_return", failure)
    automation_assignments.activate(
        simulation,
        automation,
        eligible_ids,
        authority=ControlAuthority.EMERGENCY,
        suspend=True,
    )
    return simulation._accept("create_repair_and_return", automation.automation_id)


def create_economy(simulation: Simulation, command: CreateEconomyCommand) -> CommandResult:
    priority_failure = validate_priority(command.priority)
    target_failure = validate_positive(command.target_resources, "target_resources")
    if priority_failure or target_failure:
        return simulation._reject_validation("create_economy", priority_failure or target_failure)  # type: ignore[arg-type]
    failure = validate_entities(simulation, command.generator_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("create_economy", failure)
    if any(
        not simulation.entities[entity_id].kind.profile.income_per_second
        for entity_id in command.generator_ids
    ):
        return simulation._reject_validation(
            "create_economy",
            ValidationFailure(
                ValidationPhase.CAPABILITY, "ENTITY_NOT_RESOURCE_GENERATOR", "generator_ids"
            ),
        )
    automation = automation_lifecycle.new_automation(
        simulation,
        AutomationKind.ECONOMY,
        command.title,
        command.owner_id,
        command.priority,
        command.original_instruction,
        list(command.generator_ids),
        EconomyParameters(
            list(command.generator_ids),
            command.target_resources,
            starting_resources=simulation.resources.get(command.owner_id, 0),
        ),
    )
    failure = automation_lifecycle.validate_claims(simulation, automation, command.generator_ids)
    if failure is not None:
        return simulation._reject_validation("create_economy", failure)
    automation_assignments.activate(simulation, automation, command.generator_ids)
    return simulation._accept("create_economy", automation.automation_id)


def validate_automation_common(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    owner_id: str,
    priority: int,
    title: str,
    *,
    require_movable: bool,
) -> ValidationFailure | None:
    failure = validate_automation_metadata(priority, title)
    if failure is not None:
        return failure
    return validate_entities(simulation, entity_ids, owner_id, require_movable=require_movable)


def validate_automation_metadata(priority: int, title: str) -> ValidationFailure | None:
    if not title.strip():
        return ValidationFailure(ValidationPhase.SCHEMA, "EMPTY_TITLE", "title")
    return validate_priority(priority)


def _allocate_defend_stations(
    simulation: Simulation,
    target: SpatialTarget,
    entity_ids: tuple[str, ...],
    *,
    gathering_point: bool,
) -> tuple[dict[str, Point], tuple[Point, ...], bool]:
    if isinstance(target, PolylineTarget):
        stations, slots = automation_runtime.allocate_line_defense(simulation, target, entity_ids)
        expanded = bool(slots)
        if gathering_point and not slots:
            slots = tuple(stations.values())
        return stations, slots, expanded
    spread = not gathering_point
    radius = max(collision_radius(simulation.entities[key].kind) for key in entity_ids)
    slots = simulation._gathering_slots(target, len(entity_ids), radius, spread=spread)
    stations = assign_formation_slots(
        {key: simulation.entities[key].position for key in entity_ids}, slots, target_center(target)
    )
    return stations, slots, True
