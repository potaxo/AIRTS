"""Deterministic command, automation, spatial-grounding, and simulation runtime."""

from __future__ import annotations

from collections import deque
from math import isclose

from airts.automations import (
    Automation,
    AutomationKind,
    AutomationParameters,
    AutomationStatus,
    DefendParameters,
    EconomyParameters,
    PatrolParameters,
    ProductionParameters,
    ReinforcementParameters,
    RepairParameters,
    RepairPhase,
    build_defend_stations,
    build_patrol_waypoints,
    target_contains,
)
from airts.commands import (
    AttackCommand,
    Command,
    CommandResult,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    CreateSpatialReferenceCommand,
    DeleteRegionCommand,
    EditSpatialReferenceCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    RemoveEntityCommand,
    RenameRegionCommand,
    ResumeAutomationCommand,
    SetSelectionCommand,
    StopCommand,
    command_to_dict,
)
from airts.control import ControlAuthority, ControlClaim, claim_precedes
from airts.entities import Entity, UnitState
from airts.events import EventLog, EventType
from airts.geometry import Point, PointTarget, SpatialTarget
from airts.map_model import Cell, EntityCategory, EntityKind, GameMap
from airts.movement import steering_candidates
from airts.occupancy import OccupancyError, OccupancyGrid
from airts.pathfinding import PathfindingError, PathResult, find_path
from airts.spatial import GroundingSelection, SpatialKind, SpatialStore
from airts.validation import (
    ValidationFailure,
    ValidationPhase,
    validate_positive,
    validate_priority,
)
from airts.visibility import VisibilitySystem


class Simulation:
    TICKS_PER_SECOND = 10
    TICK_SECONDS = 1.0 / TICKS_PER_SECOND

    def __init__(self, game_map: GameMap, random_seed: int = 0) -> None:
        self.game_map = game_map
        self.random_seed = random_seed
        self.tick = 0
        self.entities = {
            spec.entity_id: Entity(
                entity_id=spec.entity_id,
                kind=spec.kind,
                owner_id=spec.owner_id,
                position=spec.position,
                health=spec.kind.profile.max_health,
            )
            for spec in game_map.entities
        }
        self.occupancy = OccupancyGrid(game_map.width, game_map.height)
        for entity in self.entities.values():
            self.occupancy.place(entity.entity_id, entity.occupied_cells)
        self.automations: dict[str, Automation] = {}
        self.assignments: dict[str, str] = {}
        self.suspended_assignments: dict[str, str] = {}
        self.events = EventLog()
        self.visibility = VisibilitySystem(game_map)
        self.resources = {
            owner_id: 500 for owner_id in {entity.owner_id for entity in self.entities.values()}
        }
        self.spatial = SpatialStore()
        self.selection = GroundingSelection()
        self._next_automation_number = 1
        self._next_entity_number = 1
        self._command_history: list[dict[str, object]] = []
        self._movement_blocked: set[str] = set()
        self._blocked_ticks: dict[str, int] = {}
        self._update_visibility()

    @property
    def command_history(self) -> tuple[dict[str, object], ...]:
        return tuple(self._command_history)

    @property
    def live_automations(self) -> tuple[Automation, ...]:
        """Automations that still own work and belong in the live management panel."""

        return tuple(
            sorted(
                (
                    automation
                    for automation in self.automations.values()
                    if not automation.status.terminal
                    and (automation.entity_ids or automation.has_future_source)
                ),
                key=lambda automation: (automation.created_tick, automation.automation_id),
                reverse=True,
            )
        )

    def execute(self, command: Command) -> CommandResult:
        self._command_history.append({"tick": self.tick, "command": command_to_dict(command)})
        if isinstance(command, CreateSpatialReferenceCommand):
            return self._create_spatial_reference(command)
        if isinstance(command, EditSpatialReferenceCommand):
            return self._edit_spatial_reference(command)
        if isinstance(command, DeleteRegionCommand):
            return self._delete_region(command)
        if isinstance(command, RenameRegionCommand):
            return self._rename_region(command)
        if isinstance(command, SetSelectionCommand):
            return self._set_selection(command)
        if isinstance(command, ModifyAutomationCommand):
            return self._modify_automation(command)
        if isinstance(command, AttackCommand):
            return self._attack(command)
        if isinstance(command, MoveCommand):
            return self._move(command)
        if isinstance(command, StopCommand):
            return self._stop(command, hold=False)
        if isinstance(command, HoldPositionCommand):
            return self._stop(command, hold=True)
        if isinstance(command, RemoveEntityCommand):
            return self._remove_entity(command)
        if isinstance(command, CreatePatrolCommand):
            return self._create_patrol(command)
        if isinstance(command, CreateDefendCommand):
            return self._create_defend(command)
        if isinstance(command, CreateProductionCommand):
            return self._create_production(command)
        if isinstance(command, CreateReinforcementCommand):
            return self._create_reinforcement(command)
        if isinstance(command, CreateRepairAndReturnCommand):
            return self._create_repair(command)
        if isinstance(command, CreateEconomyCommand):
            return self._create_economy(command)
        if isinstance(command, PauseAutomationCommand):
            return self._pause(command.automation_id, command.owner_id)
        if isinstance(command, ResumeAutomationCommand):
            return self._resume(command.automation_id, command.owner_id)
        return self._cancel(command.automation_id, command.owner_id)

    def advance(self, ticks: int = 1) -> None:
        if ticks < 0:
            raise ValueError("tick count cannot be negative")
        for _ in range(ticks):
            self.tick += 1
            self._generate_income()
            self._drive_automations()
            self._move_entities()
            self._drive_combat()
            self._update_visibility()

    def remove_entity(self, entity_id: str, reason: str = "ENTITY_REMOVED") -> CommandResult:
        """Submit an authoritative, replayable entity-removal system command."""

        return self.execute(RemoveEntityCommand(entity_id, reason))

    def _remove_entity(self, command: RemoveEntityCommand) -> CommandResult:
        entity_id = command.entity_id
        if entity_id not in self.entities:
            return self._reject_validation(
                "remove_entity",
                ValidationFailure(
                    ValidationPhase.REFERENCE,
                    f"UNKNOWN_ENTITY:{entity_id}",
                    "entity_id",
                ),
            )
        current_id = self.assignments.pop(entity_id, None)
        suspended_id = self.suspended_assignments.pop(entity_id, None)
        if current_id is not None:
            current = self.automations[current_id]
            current.remove_entity(entity_id)
            if current.kind is AutomationKind.PRODUCTION and not current.status.terminal:
                self._transition(current, AutomationStatus.FAILED, "SOURCE_ENTITY_REMOVED")
            else:
                self._handle_automation_without_entities(current)
        if suspended_id is not None:
            suspended = self.automations[suspended_id]
            suspended.remove_entity(entity_id)
            self._handle_automation_without_entities(suspended)
        self.occupancy.remove(entity_id)
        del self.entities[entity_id]
        for entity in self.entities.values():
            if entity.attack_target_id == entity_id:
                entity.attack_target_id = None
        self._movement_blocked.discard(entity_id)
        self.events.record(
            self.tick,
            EventType.ENTITY_REMOVED,
            entity_id,
            previous_automation_id=current_id,
            automation_id=None,
            reason=command.reason,
        )
        return self._accept("remove_entity")

    def snapshot(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "random_seed": self.random_seed,
            "map": {"id": self.game_map.map_id, "version": self.game_map.map_version},
            "entities": {
                entity_id: entity.to_dict() for entity_id, entity in sorted(self.entities.items())
            },
            "occupancy": self.occupancy.snapshot(),
            "assignments": dict(sorted(self.assignments.items())),
            "suspended_assignments": dict(sorted(self.suspended_assignments.items())),
            "automations": {
                automation_id: automation.to_dict()
                for automation_id, automation in sorted(self.automations.items())
            },
            "visibility": self.visibility.to_dict(),
            "resources": dict(sorted(self.resources.items())),
            "spatial": self.spatial.to_dict(),
            "selection": self.selection.to_dict(),
        }

    def export_state(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "random_seed": self.random_seed,
            "entities": {
                entity_id: entity.to_dict() for entity_id, entity in sorted(self.entities.items())
            },
            "assignments": dict(sorted(self.assignments.items())),
            "suspended_assignments": dict(sorted(self.suspended_assignments.items())),
            "automations": {
                automation_id: automation.to_dict()
                for automation_id, automation in sorted(self.automations.items())
            },
            "visibility": self.visibility.to_dict(),
            "resources": dict(sorted(self.resources.items())),
            "spatial": self.spatial.to_dict(),
            "selection": self.selection.to_dict(),
            "events": [event.to_dict() for event in self.events.events],
            "command_history": list(self._command_history),
            "next_automation_number": self._next_automation_number,
            "next_entity_number": self._next_entity_number,
            "movement_blocked": sorted(self._movement_blocked),
            "blocked_ticks": dict(sorted(self._blocked_ticks.items())),
        }

    def _validate_geometry(self, target: SpatialTarget) -> ValidationFailure | None:
        points = (target.point,) if isinstance(target, PointTarget) else target.points
        if any(not self.game_map.contains(point) for point in points):
            return ValidationFailure(ValidationPhase.SPATIAL, "TARGET_OUTSIDE_MAP", "target")
        return None

    def _create_spatial_reference(self, command: CreateSpatialReferenceCommand) -> CommandResult:
        failure = self._validate_geometry(command.target)
        if failure is not None:
            return self._reject_validation("create_spatial_reference", failure)
        try:
            reference = self.spatial.create(command.target, self.tick, command.name)
        except ValueError as error:
            return self._reject_validation(
                "create_spatial_reference",
                ValidationFailure(ValidationPhase.SCHEMA, str(error), "name"),
            )
        self.events.record(
            self.tick,
            EventType.SPATIAL_REFERENCE_CREATED,
            reference.reference_id,
            kind=reference.kind.value,
            name=reference.name,
        )
        return self._accept("create_spatial_reference", reference_id=reference.reference_id)

    def _edit_spatial_reference(self, command: EditSpatialReferenceCommand) -> CommandResult:
        failure = self._validate_geometry(command.target)
        if failure is not None:
            return self._reject_validation("edit_spatial_reference", failure)
        try:
            reference = self.spatial.edit(command.reference_id, command.target, self.tick)
        except ValueError as error:
            return self._reject_validation(
                "edit_spatial_reference",
                ValidationFailure(ValidationPhase.REFERENCE, str(error), "reference_id"),
            )
        self.events.record(
            self.tick,
            EventType.SPATIAL_REFERENCE_EDITED,
            reference.reference_id,
            kind=reference.kind.value,
        )
        return self._accept("edit_spatial_reference", reference_id=reference.reference_id)

    def _rename_region(self, command: RenameRegionCommand) -> CommandResult:
        try:
            reference = self.spatial.rename_region(command.reference_id, command.name, self.tick)
        except ValueError as error:
            return self._reject_validation(
                "rename_region", ValidationFailure(ValidationPhase.SCHEMA, str(error), "name")
            )
        self.events.record(
            self.tick,
            EventType.SPATIAL_REFERENCE_NAMED,
            reference.reference_id,
            name=reference.name,
        )
        return self._accept("rename_region", reference_id=reference.reference_id)

    def _delete_region(self, command: DeleteRegionCommand) -> CommandResult:
        reference = self.spatial.references.get(command.reference_id)
        if reference is None:
            return self._reject_validation(
                "delete_region",
                ValidationFailure(
                    ValidationPhase.REFERENCE, "UNKNOWN_SPATIAL_REFERENCE", "reference_id"
                ),
            )
        if reference.kind is not SpatialKind.REGION:
            return self._reject_validation(
                "delete_region",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "ONLY_REGIONS_CAN_BE_DELETED", "reference_id"
                ),
            )
        affected = [
            automation
            for automation in self.automations.values()
            if not automation.status.terminal
            and isinstance(automation.parameters, PatrolParameters | DefendParameters)
            and automation.parameters.target == reference.geometry
        ]
        for automation in affected:
            self._cancel(automation.automation_id, command.owner_id)
        self.spatial.delete_region(command.reference_id)
        self.selection = GroundingSelection(
            self.selection.entity_ids,
            self.selection.point_ids,
            self.selection.route_ids,
            tuple(item for item in self.selection.region_ids if item != command.reference_id),
        )
        self.events.record(
            self.tick,
            EventType.SPATIAL_REFERENCE_DELETED,
            command.reference_id,
            affected_automation_ids=[item.automation_id for item in affected],
        )
        reason = "REGION_DELETED"
        if affected:
            reason += ":CANCELED:" + ",".join(item.automation_id for item in affected)
        return CommandResult(True, reason, reference_id=command.reference_id)

    def _set_selection(self, command: SetSelectionCommand) -> CommandResult:
        if len(set(command.entity_ids)) != len(command.entity_ids):
            return self._reject_validation(
                "set_selection",
                ValidationFailure(ValidationPhase.SCHEMA, "DUPLICATE_SELECTION", "entity_ids"),
            )
        for entity_id in command.entity_ids:
            entity = self.entities.get(entity_id)
            if entity is None:
                return self._reject_validation(
                    "set_selection",
                    ValidationFailure(ValidationPhase.REFERENCE, "UNKNOWN_ENTITY", "entity_ids"),
                )
            if entity.owner_id != command.owner_id:
                return self._reject_validation(
                    "set_selection",
                    ValidationFailure(ValidationPhase.OWNERSHIP, "ENTITY_NOT_OWNED", "entity_ids"),
                )
        groups = (
            (command.point_ids, SpatialKind.POINT),
            (command.route_ids, SpatialKind.ROUTE),
            (command.region_ids, SpatialKind.REGION),
        )
        for reference_ids, kind in groups:
            if len(set(reference_ids)) != len(reference_ids):
                return self._reject_validation(
                    "set_selection",
                    ValidationFailure(
                        ValidationPhase.SCHEMA, "DUPLICATE_SELECTION", f"{kind.value}_ids"
                    ),
                )
            for reference_id in reference_ids:
                reference = self.spatial.references.get(reference_id)
                if reference is None or reference.kind is not kind:
                    return self._reject_validation(
                        "set_selection",
                        ValidationFailure(
                            ValidationPhase.REFERENCE,
                            "INVALID_SPATIAL_SELECTION",
                            f"{kind.value}_ids",
                        ),
                    )
        self.selection = GroundingSelection(
            command.entity_ids, command.point_ids, command.route_ids, command.region_ids
        )
        self.events.record(
            self.tick, EventType.SELECTION_CHANGED, command.owner_id, **self.selection.to_dict()
        )
        return self._accept("set_selection")

    def _modify_automation(self, command: ModifyAutomationCommand) -> CommandResult:
        automation, failure = self._owned_automation(command.automation_id, command.owner_id)
        if failure is not None:
            return self._reject_validation("modify_automation", failure)
        assert automation is not None
        if automation.status.terminal:
            return self._reject_validation(
                "modify_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "AUTOMATION_TERMINAL", "automation_id"
                ),
            )
        if all(
            value is None
            for value in (
                command.title,
                command.priority,
                command.target,
                command.minimum_units,
                command.target_count,
            )
        ):
            return self._reject_validation(
                "modify_automation",
                ValidationFailure(ValidationPhase.SCHEMA, "NO_CHANGES", "automation_id"),
            )
        if command.title is not None and not command.title.strip():
            return self._reject_validation(
                "modify_automation",
                ValidationFailure(ValidationPhase.SCHEMA, "TITLE_EMPTY", "title"),
            )
        if command.priority is not None:
            failure = validate_priority(command.priority)
            if failure is not None:
                return self._reject_validation("modify_automation", failure)
        new_parameters: object | None = None
        if command.target is not None:
            if automation.kind is AutomationKind.PATROL:
                try:
                    waypoints = build_patrol_waypoints(command.target, self.game_map)
                    self._validate_paths(tuple(automation.entity_ids), waypoints)
                except (ValueError, PathfindingError) as error:
                    return self._reject_validation(
                        "modify_automation",
                        ValidationFailure(ValidationPhase.PATH, _reason(error), "target"),
                    )
                indices = {entity_id: 0 for entity_id in automation.entity_ids}
                new_parameters = PatrolParameters(command.target, waypoints, indices)
            elif automation.kind is AutomationKind.DEFEND:
                try:
                    stations = build_defend_stations(
                        command.target, tuple(automation.entity_ids), self.game_map
                    )
                    self._validate_paths(tuple(automation.entity_ids), tuple(stations.values()))
                except (ValueError, PathfindingError) as error:
                    return self._reject_validation(
                        "modify_automation",
                        ValidationFailure(ValidationPhase.PATH, _reason(error), "target"),
                    )
                new_parameters = DefendParameters(command.target, stations)
            else:
                return self._reject_validation(
                    "modify_automation",
                    ValidationFailure(ValidationPhase.CAPABILITY, "TARGET_NOT_EDITABLE", "target"),
                )
        if command.minimum_units is not None:
            if automation.kind is not AutomationKind.REINFORCEMENT:
                return self._reject_validation(
                    "modify_automation",
                    ValidationFailure(
                        ValidationPhase.CAPABILITY, "MINIMUM_UNITS_NOT_EDITABLE", "minimum_units"
                    ),
                )
            failure = validate_positive(command.minimum_units, "minimum_units")
            if failure is not None:
                return self._reject_validation("modify_automation", failure)
        if command.target_count is not None:
            if automation.kind is not AutomationKind.PRODUCTION:
                return self._reject_validation(
                    "modify_automation",
                    ValidationFailure(
                        ValidationPhase.CAPABILITY, "TARGET_COUNT_NOT_EDITABLE", "target_count"
                    ),
                )
            parameters = _production_parameters(automation)
            if command.target_count <= 0 or command.target_count < parameters.produced_count:
                return self._reject_validation(
                    "modify_automation",
                    ValidationFailure(
                        ValidationPhase.SCHEMA, "TARGET_COUNT_BELOW_PRODUCED", "target_count"
                    ),
                )
        if command.title is not None:
            automation.title = command.title.strip()
        if command.priority is not None:
            automation.priority = command.priority
        if new_parameters is not None:
            automation.parameters = new_parameters  # type: ignore[assignment]
        if command.minimum_units is not None:
            _reinforcement_parameters(automation).minimum_units = command.minimum_units
        if command.target_count is not None:
            _production_parameters(automation).target_count = command.target_count
        automation.modified_tick = self.tick
        self.events.record(
            self.tick,
            EventType.AUTOMATION_MODIFIED,
            automation.automation_id,
            title=automation.title,
            priority=automation.priority,
            parameters=automation.parameters.to_dict(),
        )
        return self._accept("modify_automation", automation.automation_id)

    def _move(self, command: MoveCommand) -> CommandResult:
        failure = self._validate_entities(
            command.entity_ids, command.owner_id, require_movable=True
        )
        if failure is not None:
            return self._reject_validation("move", failure)
        if not self.game_map.is_passable(command.target):
            return self._reject_validation(
                "move",
                ValidationFailure(
                    ValidationPhase.SPATIAL,
                    "TARGET_NOT_PASSABLE",
                    "target",
                    {"target": [command.target.x, command.target.y]},
                ),
            )
        try:
            destinations = self._allocate_destinations(command.entity_ids, command.target)
            blocked = self.occupancy.blocked_cells(frozenset(command.entity_ids))
            paths = {
                entity_id: find_path(
                    self.game_map,
                    self.entities[entity_id].position,
                    destinations[entity_id],
                    blocked,
                )
                for entity_id in command.entity_ids
            }
        except PathfindingError as error:
            return self._reject_validation(
                "move",
                ValidationFailure(
                    ValidationPhase.PATH,
                    str(error),
                    "target",
                    {"target": [command.target.x, command.target.y]},
                ),
            )
        for entity_id in command.entity_ids:
            self._manual_override(entity_id)
            self._start_path(
                self.entities[entity_id],
                destinations[entity_id],
                paths[entity_id],
                "human",
                UnitState.MOVING,
            )
        return self._accept("move")

    def _attack(self, command: AttackCommand) -> CommandResult:
        failure = self._validate_entities(
            command.entity_ids, command.owner_id, require_movable=True
        )
        if failure is not None:
            return self._reject_validation("attack", failure)
        target = self.entities.get(command.target_entity_id)
        if target is None:
            return self._reject_validation(
                "attack",
                ValidationFailure(ValidationPhase.REFERENCE, "UNKNOWN_TARGET", "target_entity_id"),
            )
        if target.owner_id == command.owner_id:
            return self._reject_validation(
                "attack",
                ValidationFailure(
                    ValidationPhase.OWNERSHIP, "TARGET_IS_FRIENDLY", "target_entity_id"
                ),
            )
        for entity_id in command.entity_ids:
            entity = self.entities[entity_id]
            if entity.kind.profile.attack_damage <= 0:
                return self._reject_validation(
                    "attack",
                    ValidationFailure(
                        ValidationPhase.CAPABILITY, "ENTITY_CANNOT_ATTACK", "entity_ids"
                    ),
                )
        for entity_id in command.entity_ids:
            self._manual_override(entity_id)
            entity = self.entities[entity_id]
            entity.attack_target_id = target.entity_id
            entity.state = UnitState.ATTACKING
        return self._accept("attack")

    def _stop(self, command: StopCommand | HoldPositionCommand, *, hold: bool) -> CommandResult:
        failure = self._validate_entities(command.entity_ids, command.owner_id)
        if failure is not None:
            return self._reject_validation("hold_position" if hold else "stop", failure)
        for entity_id in command.entity_ids:
            self._manual_override(entity_id)
            entity = self.entities[entity_id]
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.HOLDING if hold and entity.is_movable else UnitState.IDLE
            self._movement_blocked.discard(entity_id)
        return self._accept("hold_position" if hold else "stop")

    def _create_patrol(self, command: CreatePatrolCommand) -> CommandResult:
        failure = self._validate_automation_common(
            command.entity_ids,
            command.owner_id,
            command.priority,
            command.title,
            require_movable=True,
        )
        if failure is not None:
            return self._reject_validation("create_patrol", failure)
        try:
            waypoints = build_patrol_waypoints(command.target, self.game_map)
            self._validate_paths(command.entity_ids, waypoints)
        except (ValueError, PathfindingError) as error:
            return self._reject_validation(
                "create_patrol",
                ValidationFailure(ValidationPhase.PATH, _reason(error), "target"),
            )
        automation = self._new_automation(
            AutomationKind.PATROL,
            command.title,
            command.owner_id,
            command.priority,
            command.original_instruction,
            list(command.entity_ids),
            PatrolParameters(command.target, waypoints),
        )
        failure = self._validate_claims(automation, command.entity_ids)
        if failure is not None:
            return self._reject_validation("create_patrol", failure)
        self._activate(automation, command.entity_ids)
        return self._accept("create_patrol", automation.automation_id)

    def _create_defend(self, command: CreateDefendCommand) -> CommandResult:
        failure = self._validate_automation_common(
            command.entity_ids,
            command.owner_id,
            command.priority,
            command.title,
            require_movable=True,
        )
        if failure is not None:
            return self._reject_validation("create_defend", failure)
        try:
            stations = build_defend_stations(command.target, command.entity_ids, self.game_map)
            self._validate_paths(command.entity_ids, tuple(stations.values()))
        except (ValueError, PathfindingError) as error:
            return self._reject_validation(
                "create_defend",
                ValidationFailure(ValidationPhase.PATH, _reason(error), "target"),
            )
        automation = self._new_automation(
            AutomationKind.DEFEND,
            command.title,
            command.owner_id,
            command.priority,
            command.original_instruction,
            list(command.entity_ids),
            DefendParameters(command.target, stations),
        )
        failure = self._validate_claims(automation, command.entity_ids)
        if failure is not None:
            return self._reject_validation("create_defend", failure)
        self._activate(automation, command.entity_ids)
        return self._accept("create_defend", automation.automation_id)

    def _create_production(self, command: CreateProductionCommand) -> CommandResult:
        priority_failure = validate_priority(command.priority)
        count_failure = validate_positive(command.target_count, "target_count")
        if priority_failure or count_failure:
            failure = priority_failure if priority_failure is not None else count_failure
            assert failure is not None
            return self._reject_validation("create_production", failure)
        failure = self._validate_entities((command.factory_id,), command.owner_id)
        if failure is not None:
            return self._reject_validation("create_production", failure)
        factory = self.entities[command.factory_id]
        if factory.kind is not EntityKind.FACTORY:
            return self._reject_validation(
                "create_production",
                ValidationFailure(
                    ValidationPhase.CAPABILITY,
                    "ENTITY_NOT_FACTORY",
                    "factory_id",
                    {"entity_id": command.factory_id, "kind": factory.kind.value},
                ),
            )
        if command.unit_kind.profile.category is not EntityCategory.UNIT:
            return self._reject_validation(
                "create_production",
                ValidationFailure(
                    ValidationPhase.CAPABILITY,
                    "UNSUPPORTED_PRODUCTION_KIND",
                    "unit_kind",
                ),
            )
        if command.rally_point is not None and not self.game_map.is_passable(command.rally_point):
            return self._reject_validation(
                "create_production",
                ValidationFailure(ValidationPhase.SPATIAL, "TARGET_NOT_PASSABLE", "rally_point"),
            )
        build_ticks = {
            EntityKind.SCOUT: 10,
            EntityKind.LIGHT_TANK: 20,
            EntityKind.HEAVY_TANK: 30,
        }[command.unit_kind]
        automation = self._new_automation(
            AutomationKind.PRODUCTION,
            command.title,
            command.owner_id,
            command.priority,
            command.original_instruction,
            [command.factory_id],
            ProductionParameters(
                command.factory_id,
                command.unit_kind,
                command.target_count,
                build_ticks,
                command.rally_point,
            ),
        )
        failure = self._validate_claims(automation, (command.factory_id,))
        if failure is not None:
            return self._reject_validation("create_production", failure)
        self._activate(automation, (command.factory_id,))
        factory.state = UnitState.PRODUCING
        self.events.record(
            self.tick,
            EventType.PRODUCTION_STARTED,
            automation.automation_id,
            factory_id=command.factory_id,
            unit_kind=command.unit_kind.value,
            target_count=command.target_count,
        )
        return self._accept("create_production", automation.automation_id)

    def _create_reinforcement(self, command: CreateReinforcementCommand) -> CommandResult:
        priority_failure = validate_priority(command.priority)
        count_failure = validate_positive(command.minimum_units, "minimum_units")
        if priority_failure or count_failure:
            failure = priority_failure if priority_failure is not None else count_failure
            assert failure is not None
            return self._reject_validation("create_reinforcement", failure)
        failure = self._validate_entities(
            command.candidate_entity_ids, command.owner_id, require_movable=True
        )
        if failure is not None:
            return self._reject_validation("create_reinforcement", failure)
        target = self.automations.get(command.target_automation_id)
        if target is None:
            return self._reject_validation(
                "create_reinforcement",
                ValidationFailure(
                    ValidationPhase.REFERENCE,
                    "UNKNOWN_AUTOMATION",
                    "target_automation_id",
                ),
            )
        if target.owner_id != command.owner_id:
            return self._reject_validation(
                "create_reinforcement",
                ValidationFailure(
                    ValidationPhase.OWNERSHIP,
                    "AUTOMATION_NOT_OWNED",
                    "target_automation_id",
                ),
            )
        if (
            target.kind not in {AutomationKind.PATROL, AutomationKind.DEFEND}
            or target.status.terminal
        ):
            return self._reject_validation(
                "create_reinforcement",
                ValidationFailure(
                    ValidationPhase.CAPABILITY,
                    "INVALID_REINFORCEMENT_TARGET",
                    "target_automation_id",
                ),
            )
        automation = self._new_automation(
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
        self._activate(automation, ())
        return self._accept("create_reinforcement", automation.automation_id)

    def _create_repair(self, command: CreateRepairAndReturnCommand) -> CommandResult:
        failure = self._validate_automation_common(
            command.entity_ids,
            command.owner_id,
            command.priority,
            command.title,
            require_movable=True,
        )
        if failure is not None:
            return self._reject_validation("create_repair_and_return", failure)
        if not 0 < command.health_threshold <= 1:
            return self._reject_validation(
                "create_repair_and_return",
                ValidationFailure(
                    ValidationPhase.SCHEMA,
                    "HEALTH_THRESHOLD_OUT_OF_RANGE",
                    "health_threshold",
                ),
            )
        rate_failure = validate_positive(command.repair_rate, "repair_rate")
        if rate_failure is not None:
            return self._reject_validation("create_repair_and_return", rate_failure)
        destinations: dict[str, str] = {}
        try:
            for entity_id in command.entity_ids:
                destinations[entity_id] = self._nearest_repair_destination(
                    self.entities[entity_id]
                )[0]
        except PathfindingError as error:
            return self._reject_validation(
                "create_repair_and_return",
                ValidationFailure(ValidationPhase.PATH, str(error), "entity_ids"),
            )
        automation = self._new_automation(
            AutomationKind.REPAIR_AND_RETURN,
            command.title,
            command.owner_id,
            command.priority,
            command.original_instruction,
            list(command.entity_ids),
            RepairParameters(
                command.health_threshold,
                command.repair_rate,
                destinations,
                {
                    entity_id: self.suspended_assignments.get(entity_id)
                    or self.assignments.get(entity_id)
                    for entity_id in command.entity_ids
                },
                {entity_id: RepairPhase.TRAVELING for entity_id in command.entity_ids},
            ),
        )
        failure = self._validate_claims(
            automation, command.entity_ids, authority=ControlAuthority.EMERGENCY
        )
        if failure is not None:
            return self._reject_validation("create_repair_and_return", failure)
        self._activate(
            automation,
            command.entity_ids,
            authority=ControlAuthority.EMERGENCY,
            suspend=True,
        )
        return self._accept("create_repair_and_return", automation.automation_id)

    def _create_economy(self, command: CreateEconomyCommand) -> CommandResult:
        priority_failure = validate_priority(command.priority)
        target_failure = validate_positive(command.target_resources, "target_resources")
        if priority_failure or target_failure:
            return self._reject_validation("create_economy", priority_failure or target_failure)  # type: ignore[arg-type]
        failure = self._validate_entities(command.generator_ids, command.owner_id)
        if failure is not None:
            return self._reject_validation("create_economy", failure)
        if any(
            self.entities[entity_id].kind is not EntityKind.RESOURCE_GENERATOR
            for entity_id in command.generator_ids
        ):
            return self._reject_validation(
                "create_economy",
                ValidationFailure(
                    ValidationPhase.CAPABILITY, "ENTITY_NOT_RESOURCE_GENERATOR", "generator_ids"
                ),
            )
        automation = self._new_automation(
            AutomationKind.ECONOMY,
            command.title,
            command.owner_id,
            command.priority,
            command.original_instruction,
            list(command.generator_ids),
            EconomyParameters(
                list(command.generator_ids),
                command.target_resources,
                starting_resources=self.resources.get(command.owner_id, 0),
            ),
        )
        failure = self._validate_claims(automation, command.generator_ids)
        if failure is not None:
            return self._reject_validation("create_economy", failure)
        self._activate(automation, command.generator_ids)
        return self._accept("create_economy", automation.automation_id)

    def _pause(self, automation_id: str, owner_id: str) -> CommandResult:
        automation, failure = self._owned_automation(automation_id, owner_id)
        if failure is not None:
            return self._reject_validation("pause_automation", failure)
        assert automation is not None
        if automation.status not in {
            AutomationStatus.ACTIVE,
            AutomationStatus.WAITING,
            AutomationStatus.BLOCKED,
        }:
            return self._reject_validation(
                "pause_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY,
                    "AUTOMATION_NOT_PAUSABLE",
                    evidence={"status": automation.status.value},
                ),
            )
        self._transition(automation, AutomationStatus.PAUSED, "PLAYER_PAUSED")
        for entity_id in automation.entity_ids:
            if self.assignments.get(entity_id) != automation_id:
                continue
            entity = self.entities[entity_id]
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.IDLE
        return self._accept("pause_automation", automation_id)

    def _resume(self, automation_id: str, owner_id: str) -> CommandResult:
        automation, failure = self._owned_automation(automation_id, owner_id)
        if failure is not None:
            return self._reject_validation("resume_automation", failure)
        assert automation is not None
        if automation.status is not AutomationStatus.PAUSED:
            return self._reject_validation(
                "resume_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY,
                    "AUTOMATION_NOT_PAUSED",
                    evidence={"status": automation.status.value},
                ),
            )
        if automation.kind is AutomationKind.PRODUCTION:
            parameters = _production_parameters(automation)
            failure = self._validate_claims(automation, (parameters.factory_id,))
            if failure is not None:
                return self._reject_validation("resume_automation", failure)
            if parameters.factory_id not in automation.entity_ids:
                automation.entity_ids.append(parameters.factory_id)
            self._assign(parameters.factory_id, automation)
            self.entities[parameters.factory_id].state = UnitState.PRODUCING
        self._transition(automation, AutomationStatus.ACTIVE, "PLAYER_RESUMED")
        return self._accept("resume_automation", automation_id)

    def _cancel(self, automation_id: str, owner_id: str) -> CommandResult:
        automation, failure = self._owned_automation(automation_id, owner_id)
        if failure is not None:
            return self._reject_validation("cancel_automation", failure)
        assert automation is not None
        if automation.status.terminal:
            return self._reject_validation(
                "cancel_automation",
                ValidationFailure(
                    ValidationPhase.CAPABILITY,
                    "AUTOMATION_TERMINAL",
                    evidence={"status": automation.status.value},
                ),
            )
        self._transition(automation, AutomationStatus.CANCELED, "PLAYER_CANCELED")
        if automation.kind is AutomationKind.REPAIR_AND_RETURN:
            for entity_id in automation.entity_ids:
                if self.assignments.get(entity_id) == automation.automation_id:
                    self._resume_suspended_assignment(automation, entity_id)
        else:
            self._release_automation(automation, clear_suspended=True)
        return self._accept("cancel_automation", automation_id)

    def _drive_automations(self) -> None:
        for automation_id in sorted(self.automations):
            automation = self.automations[automation_id]
            if automation.status not in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}:
                continue
            if automation.kind is AutomationKind.PATROL:
                self._drive_patrol(automation)
            elif automation.kind is AutomationKind.DEFEND:
                self._drive_defend(automation)
            elif automation.kind is AutomationKind.PRODUCTION:
                self._drive_production(automation)
            elif automation.kind is AutomationKind.REINFORCEMENT:
                self._drive_reinforcement(automation)
            elif automation.kind is AutomationKind.ECONOMY:
                self._drive_economy(automation)
            else:
                self._drive_repair(automation)

    def _drive_patrol(self, automation: Automation) -> None:
        building_cells = self._building_cells()
        for entity_id in tuple(automation.entity_ids):
            if self.assignments.get(entity_id) != automation.automation_id:
                continue
            entity = self.entities[entity_id]
            if entity.move_target is not None or entity.path:
                continue
            target = automation.take_next_waypoint(entity_id)
            try:
                path = find_path(self.game_map, entity.position, target, building_cells)
            except PathfindingError as error:
                self._transition(automation, AutomationStatus.BLOCKED, str(error))
                self.events.record(
                    self.tick,
                    EventType.PATHFINDING_FAILED,
                    entity_id,
                    reason=str(error),
                    automation_id=automation.automation_id,
                )
                return
            self._start_path(entity, target, path, automation.automation_id, UnitState.PATROLLING)

    def _drive_defend(self, automation: Automation) -> None:
        parameters = _defend_parameters(automation)
        building_cells = self._building_cells()
        for entity_id in tuple(automation.entity_ids):
            if self.assignments.get(entity_id) != automation.automation_id:
                continue
            entity = self.entities[entity_id]
            if entity.path:
                continue
            if target_contains(parameters.target, entity.position):
                entity.move_target = None
                entity.state = UnitState.DEFENDING
                continue
            station = parameters.stations[entity_id]
            try:
                path = find_path(self.game_map, entity.position, station, building_cells)
            except PathfindingError as error:
                self._transition(automation, AutomationStatus.BLOCKED, str(error))
                return
            self._start_path(entity, station, path, automation.automation_id, UnitState.DEFENDING)

    def _drive_production(self, automation: Automation) -> None:
        parameters = _production_parameters(automation)
        if self.assignments.get(parameters.factory_id) != automation.automation_id:
            if automation.status is not AutomationStatus.PAUSED:
                self._transition(automation, AutomationStatus.PAUSED, "FACTORY_UNAVAILABLE")
            return
        cost = parameters.unit_kind.profile.production_cost
        if not parameters.cost_paid:
            balance = self.resources.get(automation.owner_id, 0)
            if balance < cost:
                if automation.status is AutomationStatus.ACTIVE:
                    self._transition(automation, AutomationStatus.WAITING, "INSUFFICIENT_RESOURCES")
                return
            self.resources[automation.owner_id] = balance - cost
            parameters.cost_paid = True
            if automation.status is AutomationStatus.WAITING:
                self._transition(automation, AutomationStatus.ACTIVE, "RESOURCES_AVAILABLE")
            self.events.record(
                self.tick,
                EventType.RESOURCE_CHANGED,
                automation.owner_id,
                amount=-cost,
                balance=self.resources[automation.owner_id],
                reason="PRODUCTION_COST",
                automation_id=automation.automation_id,
            )
        if automation.status is AutomationStatus.ACTIVE:
            parameters.progress_ticks += 1
            if parameters.progress_ticks < parameters.build_ticks:
                return
        spawn = self._find_spawn_point(self.entities[parameters.factory_id])
        if spawn is None:
            if automation.status is AutomationStatus.ACTIVE:
                self._transition(automation, AutomationStatus.WAITING, "SPAWN_BLOCKED")
            return
        if automation.status is AutomationStatus.WAITING:
            self._transition(automation, AutomationStatus.ACTIVE, "SPAWN_AVAILABLE")
        parameters.progress_ticks = 0
        parameters.cost_paid = False
        entity_id = self._spawn_unit(automation, parameters, spawn)
        parameters.produced_count += 1
        parameters.produced_entity_ids.append(entity_id)
        self.events.record(
            self.tick,
            EventType.PRODUCTION_COMPLETED,
            entity_id,
            automation_id=automation.automation_id,
            factory_id=parameters.factory_id,
            produced_count=parameters.produced_count,
        )
        if parameters.produced_count >= parameters.target_count:
            self._transition(automation, AutomationStatus.COMPLETED, "TARGET_COUNT_REACHED")
            self._release_automation(automation)

    def _drive_economy(self, automation: Automation) -> None:
        parameters = _economy_parameters(automation)
        parameters.collected = max(
            parameters.collected,
            self.resources.get(automation.owner_id, 0) - parameters.starting_resources,
        )
        if self.resources.get(automation.owner_id, 0) >= parameters.target_resources:
            self._transition(automation, AutomationStatus.COMPLETED, "RESOURCE_TARGET_REACHED")
            self._release_automation(automation)
            return
        active = [
            generator_id
            for generator_id in parameters.generator_ids
            if self.assignments.get(generator_id) == automation.automation_id
            and generator_id in self.entities
        ]
        if not active:
            self._transition(automation, AutomationStatus.FAILED, "NO_RESOURCE_GENERATORS")
            return

    def _generate_income(self) -> None:
        if self.tick % 10:
            return
        generators: dict[str, int] = {}
        for entity in self.entities.values():
            if entity.kind is EntityKind.RESOURCE_GENERATOR:
                generators[entity.owner_id] = generators.get(entity.owner_id, 0) + 1
        for owner_id, count in sorted(generators.items()):
            amount = 10 * count
            self.resources[owner_id] = self.resources.get(owner_id, 0) + amount
            self.events.record(
                self.tick,
                EventType.RESOURCE_CHANGED,
                owner_id,
                amount=amount,
                balance=self.resources[owner_id],
                reason="GENERATOR_INCOME",
            )

    def _drive_reinforcement(self, automation: Automation) -> None:
        parameters = _reinforcement_parameters(automation)
        target = self.automations.get(parameters.target_automation_id)
        if target is None or target.status.terminal:
            self._transition(automation, AutomationStatus.FAILED, "TARGET_AUTOMATION_UNAVAILABLE")
            return
        if len(target.entity_ids) >= parameters.minimum_units:
            self._transition(automation, AutomationStatus.COMPLETED, "MINIMUM_FORCE_REACHED")
            return
        transferred = False
        for entity_id in parameters.candidate_entity_ids:
            if entity_id in target.entity_ids or entity_id not in self.entities:
                continue
            if not self._claim_wins(target, entity_id):
                continue
            self._assign(entity_id, target)
            target.entity_ids.append(entity_id)
            self._initialize_runtime_entity(target, entity_id)
            parameters.transferred_entity_ids.append(entity_id)
            transferred = True
            if len(target.entity_ids) >= parameters.minimum_units:
                break
        if len(target.entity_ids) >= parameters.minimum_units:
            if target.status is AutomationStatus.WAITING:
                self._transition(target, AutomationStatus.ACTIVE, "REINFORCED")
            self._transition(automation, AutomationStatus.COMPLETED, "MINIMUM_FORCE_REACHED")
        elif not transferred and automation.status is AutomationStatus.ACTIVE:
            self._transition(automation, AutomationStatus.WAITING, "NO_ELIGIBLE_UNITS")
        elif transferred and automation.status is AutomationStatus.WAITING:
            self._transition(automation, AutomationStatus.ACTIVE, "UNITS_AVAILABLE")

    def _drive_repair(self, automation: Automation) -> None:
        parameters = _repair_parameters(automation)
        for entity_id in tuple(automation.entity_ids):
            if self.assignments.get(entity_id) != automation.automation_id:
                continue
            phase = parameters.phases[entity_id]
            entity = self.entities[entity_id]
            if phase is RepairPhase.TRAVELING:
                if entity.path or entity.move_target is not None:
                    continue
                health_ratio = entity.health / entity.kind.profile.max_health
                if health_ratio > parameters.health_threshold:
                    parameters.phases[entity_id] = RepairPhase.RETURNING
                    continue
                building = self.entities.get(parameters.destinations[entity_id])
                if building is None:
                    self._transition(automation, AutomationStatus.FAILED, "REPAIR_SOURCE_REMOVED")
                    self._release_automation(automation, clear_suspended=True)
                    return
                interaction_cells = {
                    self.game_map.cell_for(point) for point in self._interaction_points(building)
                }
                if self.game_map.cell_for(entity.position) in interaction_cells:
                    parameters.phases[entity_id] = RepairPhase.REPAIRING
                    entity.state = UnitState.REPAIRING
                    continue
                try:
                    _, point, path = self._nearest_repair_destination(entity, building.entity_id)
                except PathfindingError as error:
                    self._transition(automation, AutomationStatus.BLOCKED, str(error))
                    return
                self._start_path(entity, point, path, automation.automation_id, UnitState.RETURNING)
            elif phase is RepairPhase.REPAIRING:
                if entity.path:
                    continue
                if entity.health < entity.kind.profile.max_health:
                    if entity.state is not UnitState.REPAIRING:
                        entity.state = UnitState.REPAIRING
                        self.events.record(
                            self.tick,
                            EventType.REPAIR_STARTED,
                            entity_id,
                            automation_id=automation.automation_id,
                            destination_id=parameters.destinations[entity_id],
                        )
                    entity.health = min(
                        entity.kind.profile.max_health,
                        entity.health + parameters.repair_rate,
                    )
                if entity.health >= entity.kind.profile.max_health:
                    parameters.phases[entity_id] = RepairPhase.RETURNING
                    self.events.record(
                        self.tick,
                        EventType.REPAIR_COMPLETED,
                        entity_id,
                        automation_id=automation.automation_id,
                    )
            elif phase is RepairPhase.RETURNING:
                self._resume_suspended_assignment(automation, entity_id)
                parameters.phases[entity_id] = RepairPhase.DONE
        if all(phase is RepairPhase.DONE for phase in parameters.phases.values()):
            self._transition(automation, AutomationStatus.COMPLETED, "ALL_UNITS_REPAIRED")

    def _drive_combat(self) -> None:
        for entity_id in sorted(tuple(self.entities)):
            attacker = self.entities.get(entity_id)
            if attacker is None or attacker.kind.profile.attack_damage <= 0:
                continue
            assigned_id = self.assignments.get(entity_id)
            if (
                assigned_id is not None
                and self.automations[assigned_id].kind is AutomationKind.REPAIR_AND_RETURN
            ):
                continue
            if attacker.attack_cooldown > 0:
                attacker.attack_cooldown -= 1
            target = self.entities.get(attacker.attack_target_id or "")
            if target is None or target.owner_id == attacker.owner_id:
                target = self._nearest_enemy_in_range(attacker)
                attacker.attack_target_id = None if target is None else target.entity_id
            if target is None:
                continue
            distance = attacker.selection_position.distance_to(target.selection_position)
            if distance > attacker.kind.profile.attack_range:
                if attacker.state is UnitState.ATTACKING and not attacker.path:
                    self._chase_target(attacker, target)
                continue
            attacker.path.clear()
            attacker.move_target = None
            attacker.state = UnitState.ATTACKING
            if attacker.attack_cooldown:
                continue
            damage = attacker.kind.profile.attack_damage
            target.health = max(0, target.health - damage)
            attacker.attack_cooldown = 10
            self.events.record(
                self.tick,
                EventType.COMBAT_ATTACK,
                attacker.entity_id,
                target_id=target.entity_id,
                damage=damage,
                target_health=target.health,
            )
            if target.health == 0:
                self.events.record(
                    self.tick,
                    EventType.ENTITY_DESTROYED,
                    target.entity_id,
                    attacker_id=attacker.entity_id,
                )
                self._remove_entity(RemoveEntityCommand(target.entity_id, "COMBAT_DESTROYED"))

    def _nearest_enemy_in_range(self, attacker: Entity) -> Entity | None:
        candidates = [
            (
                attacker.selection_position.distance_to(entity.selection_position),
                entity.entity_id,
                entity,
            )
            for entity in self.entities.values()
            if entity.owner_id != attacker.owner_id
            and attacker.selection_position.distance_to(entity.selection_position)
            <= attacker.kind.profile.attack_range
        ]
        return min(candidates)[2] if candidates else None

    def _chase_target(self, attacker: Entity, target: Entity) -> None:
        paths: list[tuple[float, Point, PathResult]] = []
        blocked = self._building_cells()
        for point in self._interaction_points(target):
            try:
                path = find_path(self.game_map, attacker.position, point, blocked)
                paths.append((path.cost, point, path))
            except PathfindingError:
                continue
        if paths:
            _, point, path = min(paths, key=lambda item: (item[0], item[1].y, item[1].x))
            self._start_path(attacker, point, path, "combat", UnitState.ATTACKING)

    def _move_entities(self) -> None:
        for entity_id in sorted(self.entities):
            entity = self.entities[entity_id]
            if not entity.path:
                continue
            self._skip_crowded_waypoints(entity)
            target = entity.path[0]
            maximum_step = entity.speed * self.TICK_SECONDS
            neighbors = tuple(
                other.position
                for other_id, other in sorted(self.entities.items())
                if other_id != entity_id and other.is_movable
            )
            next_position = next(
                (
                    candidate
                    for candidate in steering_candidates(
                        entity.position, target, maximum_step, neighbors
                    )
                    if self._local_move_is_available(entity, candidate)
                ),
                None,
            )
            if next_position is None:
                self._record_movement_blocked(entity, "NO_SAFE_LOCAL_VELOCITY")
                continue
            try:
                self.occupancy.move(entity_id, self._cells_at(entity, next_position))
            except OccupancyError as error:
                self._record_movement_blocked(entity, str(error))
                continue
            self._movement_blocked.discard(entity_id)
            self._blocked_ticks.pop(entity_id, None)
            entity.position = next_position
            arrived = entity.position.distance_to(target) <= 1e-9 or isclose(
                entity.position.distance_to(target), 0.0, abs_tol=1e-9
            )
            if arrived:
                entity.path.pop(0)
                if not entity.path:
                    entity.move_target = None
                    entity.state = self._state_for_assignment(entity_id)
                    self.events.record(
                        self.tick,
                        EventType.MOVEMENT_COMPLETED,
                        entity_id,
                        position=[entity.position.x, entity.position.y],
                        assignment=self.assignments.get(entity_id),
                    )

    def _skip_crowded_waypoints(self, entity: Entity) -> None:
        """Use path lookahead so agents pass a contested cell instead of orbiting its center."""

        while len(entity.path) > 1:
            waypoint = entity.path[0]
            occupants = self.occupancy.occupants(self.game_map.cell_for(waypoint)) - {
                entity.entity_id
            }
            moving_occupants = {
                occupant_id
                for occupant_id in occupants
                if self.entities[occupant_id].is_movable and self.entities[occupant_id].path
            }
            if not moving_occupants:
                return
            entity.path.pop(0)

    def _local_move_is_available(self, entity: Entity, candidate: Point) -> bool:
        if not self.game_map.is_passable(candidate):
            return False
        cells = self._cells_at(entity, candidate)
        if any(self.occupancy.occupants(cell) - {entity.entity_id} for cell in cells):
            return False
        return all(
            other_id == entity.entity_id
            or not other.is_movable
            or candidate.distance_to(other.position) >= 0.62
            for other_id, other in self.entities.items()
        )

    def _record_movement_blocked(self, entity: Entity, evidence: str) -> None:
        entity_id = entity.entity_id
        self._blocked_ticks[entity_id] = self._blocked_ticks.get(entity_id, 0) + 1
        if entity_id not in self._movement_blocked:
            self.events.record(
                self.tick,
                EventType.MOVEMENT_BLOCKED,
                entity_id,
                reason="LOCAL_AVOIDANCE_BLOCKED",
                evidence=evidence,
            )
            self._movement_blocked.add(entity_id)
        if self._blocked_ticks[entity_id] >= self.TICKS_PER_SECOND:
            self._recover_blocked_entity(entity)

    def _recover_blocked_entity(self, entity: Entity) -> None:
        """Choose a deterministic free sidestep, then replan to the original target."""

        destination = entity.move_target
        if destination is None:
            return
        origin_x, origin_y = int(entity.position.x), int(entity.position.y)
        candidates: list[Point] = []
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1)):
            point = Point(origin_x + dx + 0.5, origin_y + dy + 0.5)
            if not self.game_map.is_passable(point):
                continue
            cells = self._cells_at(entity, point)
            if any(self.occupancy.occupants(cell) - {entity.entity_id} for cell in cells):
                continue
            candidates.append(point)
        if not candidates:
            return
        clockwise = sum(ord(character) for character in entity.entity_id) % 2
        candidates.sort(
            key=lambda point: (
                point.distance_to(destination),
                point.y if clockwise else -point.y,
                point.x if clockwise else -point.x,
            )
        )
        sidestep = candidates[0]
        try:
            path = find_path(self.game_map, sidestep, destination, self._building_cells())
        except PathfindingError:
            return
        entity.path = [sidestep, *path.waypoints]
        entity.path_cost = entity.position.distance_to(sidestep) + path.cost
        self._blocked_ticks[entity.entity_id] = 0
        self.events.record(
            self.tick,
            EventType.PATH_COMPUTED,
            entity.entity_id,
            reason="STUCK_REPLAN",
            target=[destination.x, destination.y],
        )

    def _activate(
        self,
        automation: Automation,
        entity_ids: tuple[str, ...],
        *,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
        suspend: bool = False,
    ) -> None:
        self.automations[automation.automation_id] = automation
        self._next_automation_number += 1
        self.events.record(
            self.tick,
            EventType.AUTOMATION_CREATED,
            automation.automation_id,
            template=automation.kind.value,
            owner_id=automation.owner_id,
            priority=automation.priority,
            entity_ids=list(entity_ids),
        )
        self._transition(automation, AutomationStatus.VALIDATING, "VALIDATION_STARTED")
        for entity_id in entity_ids:
            self._assign(entity_id, automation, authority=authority, suspend=suspend)
        self._transition(automation, AutomationStatus.ACTIVE, "VALIDATION_SUCCEEDED")
        for entity_id in entity_ids:
            self._initialize_runtime_entity(automation, entity_id)

    def _new_automation(
        self,
        kind: AutomationKind,
        title: str,
        owner_id: str,
        priority: int,
        original_instruction: str,
        entity_ids: list[str],
        parameters: AutomationParameters,
    ) -> Automation:
        automation_id = f"automation_{self._next_automation_number:03d}"
        return Automation(
            automation_id=automation_id,
            title=title.strip(),
            kind=kind,
            owner_id=owner_id,
            priority=priority,
            created_tick=self.tick,
            modified_tick=self.tick,
            original_instruction=original_instruction,
            entity_ids=entity_ids,
            parameters=parameters,
        )

    def _transition(self, automation: Automation, status: AutomationStatus, reason: str) -> None:
        automation.transition(status, self.tick, reason)
        self.events.record(
            self.tick,
            EventType.AUTOMATION_STATE_CHANGED,
            automation.automation_id,
            previous=automation.transition_history[-1].previous.value
            if automation.transition_history[-1].previous is not None
            else None,
            status=status.value,
            reason=reason,
        )

    def _assign(
        self,
        entity_id: str,
        automation: Automation,
        *,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
        suspend: bool = False,
    ) -> None:
        previous_id = self.assignments.get(entity_id)
        if previous_id == automation.automation_id:
            return
        if previous_id is not None:
            if suspend:
                previous = self.automations[previous_id]
                existing_suspended = self.suspended_assignments.get(entity_id)
                self.suspended_assignments[entity_id] = existing_suspended or previous_id
                if previous.kind is AutomationKind.REPAIR_AND_RETURN:
                    previous.remove_entity(entity_id)
                    self._handle_automation_without_entities(previous)
            else:
                previous = self.automations[previous_id]
                previous.remove_entity(entity_id)
                self._handle_automation_without_entities(previous)
                self.suspended_assignments.pop(entity_id, None)
        self.assignments[entity_id] = automation.automation_id
        self.events.record(
            self.tick,
            EventType.ASSIGNMENT_CHANGED,
            entity_id,
            previous_automation_id=previous_id,
            automation_id=automation.automation_id,
            authority=authority.name.lower(),
        )

    def _manual_override(self, entity_id: str) -> None:
        automation_id = self.assignments.pop(entity_id, None)
        suspended_id = self.suspended_assignments.pop(entity_id, None)
        affected = [item for item in (automation_id, suspended_id) if item is not None]
        for affected_id in dict.fromkeys(affected):
            automation = self.automations[affected_id]
            if automation.kind is AutomationKind.PRODUCTION:
                if automation.status in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}:
                    self._transition(automation, AutomationStatus.PAUSED, "FACTORY_MANUAL_OVERRIDE")
            else:
                automation.remove_entity(entity_id)
                self._handle_automation_without_entities(automation)
        if automation_id is not None:
            self.events.record(
                self.tick,
                EventType.MANUAL_OVERRIDE,
                entity_id,
                automation_id=automation_id,
                suspended_automation_id=suspended_id,
            )

    def _handle_automation_without_entities(self, automation: Automation) -> None:
        if automation.entity_ids or automation.status.terminal:
            return
        if automation.has_future_source:
            if automation.status is AutomationStatus.ACTIVE:
                self._transition(automation, AutomationStatus.WAITING, "NO_ASSIGNED_ENTITIES")
        elif automation.status in {
            AutomationStatus.ACTIVE,
            AutomationStatus.WAITING,
            AutomationStatus.BLOCKED,
            AutomationStatus.PAUSED,
        }:
            if automation.status is AutomationStatus.PAUSED:
                self._transition(automation, AutomationStatus.CANCELED, "NO_ASSIGNED_ENTITIES")
            else:
                self._transition(automation, AutomationStatus.CANCELED, "NO_ASSIGNED_ENTITIES")

    def _release_automation(self, automation: Automation, *, clear_suspended: bool = False) -> None:
        for entity_id in tuple(automation.entity_ids):
            if self.assignments.get(entity_id) == automation.automation_id:
                self.assignments.pop(entity_id, None)
                entity = self.entities[entity_id]
                entity.path.clear()
                entity.move_target = None
                entity.state = UnitState.IDLE
            if clear_suspended:
                suspended_id = self.suspended_assignments.pop(entity_id, None)
                if suspended_id is not None and suspended_id in self.automations:
                    suspended = self.automations[suspended_id]
                    suspended.remove_entity(entity_id)
                    self._handle_automation_without_entities(suspended)

    def _resume_suspended_assignment(self, repair_automation: Automation, entity_id: str) -> None:
        resume_id = self.suspended_assignments.pop(entity_id, None)
        if self.assignments.get(entity_id) == repair_automation.automation_id:
            self.assignments.pop(entity_id, None)
        entity = self.entities[entity_id]
        if resume_id is not None:
            resume = self.automations.get(resume_id)
            if resume is not None and not resume.status.terminal and entity_id in resume.entity_ids:
                self.assignments[entity_id] = resume_id
                if resume.status in {AutomationStatus.WAITING, AutomationStatus.BLOCKED}:
                    self._transition(resume, AutomationStatus.ACTIVE, "REPAIRED_UNIT_RETURNED")
                entity.state = self._state_for_assignment(entity_id)
                return
        entity.state = UnitState.IDLE

    def _initialize_runtime_entity(self, automation: Automation, entity_id: str) -> None:
        entity = self.entities[entity_id]
        entity.path.clear()
        entity.move_target = None
        if automation.kind is AutomationKind.PATROL:
            patrol_parameters = _patrol_parameters(automation)
            patrol_parameters.waypoint_indices.setdefault(entity_id, 0)
            entity.state = UnitState.PATROLLING
        elif automation.kind is AutomationKind.DEFEND:
            defend_parameters = _defend_parameters(automation)
            if entity_id not in defend_parameters.stations:
                defend_parameters.stations[entity_id] = next(
                    iter(defend_parameters.stations.values())
                )
            entity.state = UnitState.DEFENDING
        elif automation.kind is AutomationKind.PRODUCTION:
            entity.state = UnitState.PRODUCING
        elif automation.kind is AutomationKind.REPAIR_AND_RETURN:
            entity.state = UnitState.RETURNING
        elif automation.kind is AutomationKind.ECONOMY:
            entity.state = UnitState.IDLE

    def _spawn_unit(
        self, automation: Automation, parameters: ProductionParameters, position: Point
    ) -> str:
        while True:
            entity_id = f"{parameters.unit_kind.value}_{self._next_entity_number:03d}"
            self._next_entity_number += 1
            if entity_id not in self.entities:
                break
        entity = Entity(
            entity_id=entity_id,
            kind=parameters.unit_kind,
            owner_id=automation.owner_id,
            position=position,
            health=parameters.unit_kind.profile.max_health,
        )
        self.entities[entity_id] = entity
        self.occupancy.place(entity_id, entity.occupied_cells)
        if parameters.rally_point is not None:
            try:
                path = find_path(
                    self.game_map,
                    position,
                    parameters.rally_point,
                    self.occupancy.blocked_cells(frozenset({entity_id})),
                )
            except PathfindingError:
                entity.state = UnitState.IDLE
                self.events.record(
                    self.tick,
                    EventType.PATHFINDING_FAILED,
                    entity_id,
                    automation_id=automation.automation_id,
                    reason="RALLY_POINT_UNREACHABLE",
                    target=[parameters.rally_point.x, parameters.rally_point.y],
                )
            else:
                self._start_path(
                    entity,
                    parameters.rally_point,
                    path,
                    automation.automation_id,
                    UnitState.MOVING,
                )
        return entity_id

    def _find_spawn_point(self, factory: Entity) -> Point | None:
        occupied = factory.occupied_cells
        candidates: set[Cell] = set()
        for x, y in occupied:
            candidates.update({(x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)})
        for cell in sorted(candidates, key=lambda item: (item[1], item[0])):
            if (
                cell not in occupied
                and self.game_map.is_cell_passable(cell)
                and not self.occupancy.occupants(cell)
            ):
                return Point(cell[0] + 0.5, cell[1] + 0.5)
        return None

    def _nearest_repair_destination(
        self, entity: Entity, required_id: str | None = None
    ) -> tuple[str, Point, PathResult]:
        order = {
            EntityKind.REPAIR_HUB: 0,
            EntityKind.FACTORY: 1,
            EntityKind.COMMAND_CENTER: 2,
        }
        candidates: list[tuple[int, float, str, Point, PathResult]] = []
        for building in self.entities.values():
            if (
                building.owner_id != entity.owner_id
                or building.kind not in order
                or (required_id is not None and building.entity_id != required_id)
            ):
                continue
            for point in self._interaction_points(building):
                try:
                    path = find_path(
                        self.game_map,
                        entity.position,
                        point,
                        self._building_cells(),
                    )
                except PathfindingError:
                    continue
                candidates.append(
                    (order[building.kind], path.cost, building.entity_id, point, path)
                )
        if not candidates:
            raise PathfindingError("NO_REPAIR_DESTINATION")
        _, _, building_id, point, path = min(
            candidates, key=lambda item: (item[0], item[1], item[2], item[3].y, item[3].x)
        )
        return building_id, point, path

    def _interaction_points(self, building: Entity) -> tuple[Point, ...]:
        occupied = building.occupied_cells
        cells: set[Cell] = set()
        for x, y in occupied:
            cells.update({(x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)})
        return tuple(
            Point(x + 0.5, y + 0.5)
            for x, y in sorted(cells.difference(occupied), key=lambda item: (item[1], item[0]))
            if self.game_map.is_cell_passable((x, y))
        )

    def _validate_automation_common(
        self,
        entity_ids: tuple[str, ...],
        owner_id: str,
        priority: int,
        title: str,
        *,
        require_movable: bool,
    ) -> ValidationFailure | None:
        if not title.strip():
            return ValidationFailure(ValidationPhase.SCHEMA, "EMPTY_TITLE", "title")
        priority_failure = validate_priority(priority)
        if priority_failure is not None:
            return priority_failure
        return self._validate_entities(entity_ids, owner_id, require_movable=require_movable)

    def _validate_entities(
        self,
        entity_ids: tuple[str, ...],
        owner_id: str,
        *,
        require_movable: bool = False,
    ) -> ValidationFailure | None:
        if not entity_ids:
            return ValidationFailure(ValidationPhase.REFERENCE, "NO_ENTITIES", "entity_ids")
        if len(set(entity_ids)) != len(entity_ids):
            return ValidationFailure(ValidationPhase.REFERENCE, "DUPLICATE_ENTITY", "entity_ids")
        unknown = next((item for item in entity_ids if item not in self.entities), None)
        if unknown is not None:
            return ValidationFailure(
                ValidationPhase.REFERENCE,
                f"UNKNOWN_ENTITY:{unknown}",
                "entity_ids",
                {"entity_id": unknown},
            )
        unowned = next(
            (item for item in entity_ids if self.entities[item].owner_id != owner_id), None
        )
        if unowned is not None:
            return ValidationFailure(
                ValidationPhase.OWNERSHIP,
                f"ENTITY_NOT_OWNED:{unowned}",
                "entity_ids",
                {"entity_id": unowned, "owner_id": self.entities[unowned].owner_id},
            )
        if require_movable:
            immovable = next(
                (item for item in entity_ids if not self.entities[item].is_movable), None
            )
            if immovable is not None:
                return ValidationFailure(
                    ValidationPhase.CAPABILITY,
                    f"ENTITY_NOT_MOVABLE:{immovable}",
                    "entity_ids",
                    {"entity_id": immovable},
                )
        return None

    def _validate_claims(
        self,
        automation: Automation,
        entity_ids: tuple[str, ...],
        *,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
    ) -> ValidationFailure | None:
        for entity_id in entity_ids:
            if not self._claim_wins(automation, entity_id, authority):
                return ValidationFailure(
                    ValidationPhase.CONFLICT,
                    "CONTROL_CONFLICT",
                    "entity_ids",
                    {
                        "entity_id": entity_id,
                        "incumbent": self.assignments.get(entity_id),
                        "challenger": automation.automation_id,
                    },
                )
        return None

    def _claim_wins(
        self,
        automation: Automation,
        entity_id: str,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
    ) -> bool:
        incumbent_id = self.assignments.get(entity_id)
        if incumbent_id is None:
            return True
        incumbent = self.automations[incumbent_id]
        incumbent_authority = (
            ControlAuthority.EMERGENCY
            if incumbent.kind is AutomationKind.REPAIR_AND_RETURN
            else ControlAuthority.AUTOMATION
        )
        return claim_precedes(
            ControlClaim(
                automation.automation_id, authority, automation.priority, automation.created_tick
            ),
            ControlClaim(
                incumbent.automation_id,
                incumbent_authority,
                incumbent.priority,
                incumbent.created_tick,
            ),
        )

    def _owned_automation(
        self, automation_id: str, owner_id: str
    ) -> tuple[Automation | None, ValidationFailure | None]:
        automation = self.automations.get(automation_id)
        if automation is None:
            return None, ValidationFailure(
                ValidationPhase.REFERENCE, "UNKNOWN_AUTOMATION", "automation_id"
            )
        if automation.owner_id != owner_id:
            return None, ValidationFailure(
                ValidationPhase.OWNERSHIP,
                "AUTOMATION_NOT_OWNED",
                "automation_id",
                {"owner_id": automation.owner_id},
            )
        return automation, None

    def _validate_paths(self, entity_ids: tuple[str, ...], waypoints: tuple[Point, ...]) -> None:
        building_cells = self._building_cells()
        for index, entity_id in enumerate(entity_ids):
            find_path(
                self.game_map,
                self.entities[entity_id].position,
                waypoints[index % len(waypoints)],
                building_cells,
            )
        if len(waypoints) > 1:
            for start, end in zip(waypoints, waypoints[1:] + waypoints[:1], strict=True):
                find_path(self.game_map, start, end, building_cells)

    def _start_path(
        self,
        entity: Entity,
        destination: Point,
        path: PathResult,
        source: str,
        state: UnitState,
    ) -> None:
        self._movement_blocked.discard(entity.entity_id)
        entity.path = list(path.waypoints)
        entity.path_cost = path.cost
        entity.move_target = destination if entity.path else None
        entity.state = state if entity.path else self._state_for_assignment(entity.entity_id)
        self.events.record(
            self.tick,
            EventType.PATH_COMPUTED,
            entity.entity_id,
            destination=[destination.x, destination.y],
            cell_count=len(path.cells),
            cost=path.cost,
            source=source,
        )
        if entity.path:
            self.events.record(
                self.tick,
                EventType.MOVEMENT_STARTED,
                entity.entity_id,
                target=[destination.x, destination.y],
                source=source,
            )

    def _state_for_assignment(self, entity_id: str) -> UnitState:
        automation_id = self.assignments.get(entity_id)
        if automation_id is None:
            return UnitState.IDLE
        automation = self.automations[automation_id]
        return {
            AutomationKind.PATROL: UnitState.PATROLLING,
            AutomationKind.DEFEND: UnitState.DEFENDING,
            AutomationKind.PRODUCTION: UnitState.PRODUCING,
            AutomationKind.REINFORCEMENT: UnitState.WAITING,
            AutomationKind.REPAIR_AND_RETURN: UnitState.REPAIRING,
            AutomationKind.ECONOMY: UnitState.IDLE,
        }[automation.kind]

    def _fail_movement(self, entity: Entity, reason: str, position: Point) -> None:
        entity.move_target = None
        entity.path.clear()
        entity.state = UnitState.IDLE
        self.events.record(
            self.tick,
            EventType.MOVEMENT_FAILED,
            entity.entity_id,
            reason=reason,
            position=[position.x, position.y],
        )

    def _allocate_destinations(
        self, entity_ids: tuple[str, ...], target: Point
    ) -> dict[str, Point]:
        blocked = self.occupancy.blocked_cells(frozenset(entity_ids))
        target_cell = self.game_map.cell_for(target)
        frontier = deque([target_cell])
        visited = {target_cell}
        candidates: list[Cell] = []
        while frontier and len(candidates) < len(entity_ids):
            cell = frontier.popleft()
            if self.game_map.is_cell_passable(cell) and cell not in blocked:
                candidates.append(cell)
            for neighbor in self._neighbor_cells(cell):
                if neighbor not in visited and self.game_map.contains_cell(neighbor):
                    visited.add(neighbor)
                    frontier.append(neighbor)
        if len(candidates) < len(entity_ids):
            raise PathfindingError("INSUFFICIENT_DESTINATIONS")
        return {
            entity_id: (
                target
                if index == 0 and candidates[index] == target_cell
                else Point(candidates[index][0] + 0.5, candidates[index][1] + 0.5)
            )
            for index, entity_id in enumerate(entity_ids)
        }

    def _building_cells(self) -> frozenset[Cell]:
        return frozenset(
            cell
            for entity in self.entities.values()
            if entity.category is EntityCategory.BUILDING
            for cell in entity.occupied_cells
        )

    def _cells_at(self, entity: Entity, position: Point) -> frozenset[Cell]:
        width, height = entity.kind.profile.footprint
        origin_x = int(position.x)
        origin_y = int(position.y)
        return frozenset(
            (x, y)
            for y in range(origin_y, origin_y + height)
            for x in range(origin_x, origin_x + width)
        )

    def _update_visibility(self) -> None:
        for player_id, (newly_visible, newly_explored, no_longer_visible) in self.visibility.update(
            self.entities, self.tick
        ).items():
            if newly_visible or newly_explored or no_longer_visible:
                self.events.record(
                    self.tick,
                    EventType.VISIBILITY_CHANGED,
                    player_id,
                    newly_visible=newly_visible,
                    newly_explored=newly_explored,
                    no_longer_visible=no_longer_visible,
                )

    def _accept(
        self, command: str, automation_id: str | None = None, reference_id: str | None = None
    ) -> CommandResult:
        self.events.record(
            self.tick,
            EventType.COMMAND_ACCEPTED,
            automation_id,
            command=command,
        )
        return CommandResult(True, "ACCEPTED", automation_id, reference_id)

    def _reject_validation(self, command: str, failure: ValidationFailure) -> CommandResult:
        if failure.phase is ValidationPhase.PATH:
            self.events.record(
                self.tick,
                EventType.PATHFINDING_FAILED,
                None,
                command=command,
                reason=failure.code,
                evidence=failure.evidence or {},
            )
        self.events.record(
            self.tick,
            EventType.VALIDATION_FAILED,
            None,
            command=command,
            **failure.to_dict(),
        )
        self.events.record(
            self.tick,
            EventType.COMMAND_REJECTED,
            None,
            command=command,
            reason=failure.code,
            validation_phase=failure.phase.value,
        )
        return CommandResult(False, failure.code)

    @staticmethod
    def _neighbor_cells(cell: Cell) -> tuple[Cell, ...]:
        x, y = cell
        return ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1))


def _reason(error: Exception) -> str:
    return str(error).upper().replace(" ", "_")


def _patrol_parameters(automation: Automation) -> PatrolParameters:
    if not isinstance(automation.parameters, PatrolParameters):
        raise TypeError("automation does not have patrol parameters")
    return automation.parameters


def _defend_parameters(automation: Automation) -> DefendParameters:
    if not isinstance(automation.parameters, DefendParameters):
        raise TypeError("automation does not have defend parameters")
    return automation.parameters


def _production_parameters(automation: Automation) -> ProductionParameters:
    if not isinstance(automation.parameters, ProductionParameters):
        raise TypeError("automation does not have production parameters")
    return automation.parameters


def _reinforcement_parameters(automation: Automation) -> ReinforcementParameters:
    if not isinstance(automation.parameters, ReinforcementParameters):
        raise TypeError("automation does not have reinforcement parameters")
    return automation.parameters


def _economy_parameters(automation: Automation) -> EconomyParameters:
    if not isinstance(automation.parameters, EconomyParameters):
        raise TypeError("automation does not have economy parameters")
    return automation.parameters


def _repair_parameters(automation: Automation) -> RepairParameters:
    if not isinstance(automation.parameters, RepairParameters):
        raise TypeError("automation does not have repair parameters")
    return automation.parameters
