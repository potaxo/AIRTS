"""Deterministic command, automation, spatial-grounding, and simulation runtime."""

from __future__ import annotations

from collections import deque
from gc import collect as collect_garbage
from math import ceil, floor, sqrt

from airts.automations import (
    Automation,
    AutomationKind,
    AutomationParameters,
    AutomationStatus,
    DefendParameters,
    PatrolParameters,
    ProductionParameters,
    ReinforcementParameters,
    RepairParameters,
    target_center,
)
from airts.commands import (
    AttackCommand,
    Command,
    CommandResult,
    CreateConstructionCommand,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    CreateSpatialReferenceCommand,
    DeleteRegionCommand,
    DeleteSpatialReferenceCommand,
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
from airts.control import ControlAuthority
from airts.events import EventLog, EventType
from airts.geometry import Point, PolygonRegion, PolylineTarget, SpatialTarget
from airts.navigation.collision import SETTLED_FORMATION_SPACING
from airts.navigation.pathfinding import PathfindingError, PathResult, RoutingService
from airts.spatial import GroundingSelection, SpatialStore
from airts.systems import (
    automation_lifecycle,
    automation_runtime,
    command_handlers,
    spatial_commands,
)
from airts.systems import combat as combat_system
from airts.systems import construction as construction_system
from airts.systems import economy as economy_system
from airts.systems import movement as movement_system
from airts.systems import production as production_system
from airts.validation import (
    ValidationFailure,
    ValidationPhase,
)
from airts.world.entities import Entity, UnitState
from airts.world.map_model import Cell, EntityCategory, EntityKind, GameMap
from airts.world.occupancy import OccupancyGrid
from airts.world.projectiles import Projectile, ProjectileTrace
from airts.world.visibility import VisibilitySystem

type LocalCollider = tuple[str, Point, float, bool]


class Simulation:
    LARGE_SCENE_GC_ENTITY_THRESHOLD = 512
    TICKS_PER_SECOND = 10
    TICK_SECONDS = 1.0 / TICKS_PER_SECOND
    NO_PROGRESS_YIELD_TICKS = 30
    DESTINATION_REPATH_TICKS = 50
    MIN_PROGRESS_DISTANCE = 0.02
    CONGESTION_RETRY_TICKS = 5
    BLOCKED_RECOVERY_BUDGET = 4
    DEFEND_RESPONSE_RADIUS = 4.0
    DEFEND_PURSUIT_RADIUS = 7.0
    DEFEND_ATTACK_MEMORY_TICKS = 30
    DEFEND_STATION_TOLERANCE = 0.05
    DEFEND_FORMATION_TOLERANCE = 1.0
    DEFEND_FORMATION_SETTLE_TICKS = 500
    DEFAULT_ENEMY_SPAWN_INTERVAL_TICKS = 10
    DEFAULT_ENEMY_SPAWN_CAP = 100
    GATHERING_PATH_BUDGET = 4
    AUTOMATION_ROUTE_BUDGET = 16
    TOTAL_AUTOMATION_ROUTE_BUDGET = 32
    STALLED_REPATH_BUDGET = 4
    COMBAT_PATH_BUDGET = 16
    MILITARY_OBSTACLE_PATH_PENALTY = 1.5
    PRODUCTION_BUILD_TICKS = 5
    CONSTRUCTION_BUILD_TICKS = 20
    CONSTRUCTION_REQUIRED_VALUE = 100.0

    def __init__(
        self,
        game_map: GameMap,
        random_seed: int = 0,
        *,
        ambient_enemy_spawns: bool = False,
        enemy_spawn_interval_ticks: int = DEFAULT_ENEMY_SPAWN_INTERVAL_TICKS,
        enemy_spawn_cap: int = DEFAULT_ENEMY_SPAWN_CAP,
    ) -> None:
        if enemy_spawn_interval_ticks <= 0:
            raise ValueError("enemy_spawn_interval_ticks must be positive")
        if enemy_spawn_cap < 0:
            raise ValueError("enemy_spawn_cap cannot be negative")
        if len(game_map.entities) >= self.LARGE_SCENE_GC_ENTITY_THRESHOLD:
            # Starting a large match is a natural non-frame boundary.  Drain cycles retained by a
            # previous scenario here so an unrelated generation-2 collection cannot become a live
            # simulation or presentation p99 stall several ticks later.
            collect_garbage()
        self.game_map = game_map
        self._all_terrain_passable = all(
            terrain.passable for row in game_map.terrain for terrain in row
        )
        self.random_seed = random_seed
        self.ambient_enemy_spawns = ambient_enemy_spawns
        self.enemy_spawn_interval_ticks = enemy_spawn_interval_ticks
        self.enemy_spawn_cap = enemy_spawn_cap
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
        self._next_projectile_number = 1
        self.projectiles: dict[str, Projectile] = {}
        self.projectile_traces: list[ProjectileTrace] = []
        self._command_history: list[dict[str, object]] = []
        self._movement_blocked: set[str] = set()
        self._blocked_ticks: dict[str, int] = {}
        self._push_events_this_tick: set[str] = set()
        self._stalled_repaths_this_tick = 0
        self._movement_step_attempt_count = 0
        self._collision_pair_check_count = 0
        self._blocked_recoveries_this_tick = 0
        self._open_force_slots: (
            tuple[
                float,
                dict[str, tuple[int, int]],
                dict[tuple[int, int], str],
            ]
            | None
        ) = None
        self._building_cells_cache: frozenset[Cell] | None = None
        self._routes = RoutingService(
            game_map,
            automation_budget=self.TOTAL_AUTOMATION_ROUTE_BUDGET,
            combat_budget=self.COMBAT_PATH_BUDGET,
        )
        self._gathering_slot_cache: dict[tuple[SpatialTarget, float], tuple[Point, ...]] = {}
        self._gathering_reachable_cache: dict[SpatialTarget, frozenset[Cell]] = {}
        self._waypoint_corridor_cache: dict[tuple[Cell, Cell], bool] = {}
        self._update_visibility()

    @property
    def command_history(self) -> tuple[dict[str, object], ...]:
        return tuple(self._command_history)

    @property
    def command_count(self) -> int:
        """Return the history size without copying the replayable command records."""

        return len(self._command_history)

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

    def production_queue(self, factory_id: str) -> tuple[Automation, ...]:
        """Return one factory's unfinished production jobs in FIFO order."""

        return production_system.factory_production_jobs(self, factory_id)

    def continuous_production(self, factory_id: str) -> Automation | None:
        """Return the factory's current unfinished background production loop."""

        return next(
            (
                automation
                for automation in production_system.factory_production_jobs(self, factory_id)
                if _production_parameters(automation).continuous
            ),
            None,
        )

    @property
    def navigation_field_build_count(self) -> int:
        """Expose shared-navigation work for deterministic performance regression tests."""

        return self._routes.field_build_count

    @property
    def automation_route_count(self) -> int:
        """Automation routes admitted by the shared scheduler this tick."""

        return self._routes.automation_route_count

    @property
    def movement_step_attempt_count(self) -> int:
        """Movement controllers evaluated during the most recent tick."""

        return self._movement_step_attempt_count

    @property
    def collision_pair_check_count(self) -> int:
        """Broadphase pairs evaluated during the most recent tick."""

        return self._collision_pair_check_count

    def execute(self, command: Command) -> CommandResult:
        self._command_history.append({"tick": self.tick, "command": command_to_dict(command)})
        if isinstance(command, CreateSpatialReferenceCommand):
            return spatial_commands.create_spatial_reference(self, command)
        if isinstance(command, EditSpatialReferenceCommand):
            return spatial_commands.edit_spatial_reference(self, command)
        if isinstance(command, DeleteRegionCommand | DeleteSpatialReferenceCommand):
            return spatial_commands.delete_spatial_reference(self, command)
        if isinstance(command, RenameRegionCommand):
            return spatial_commands.rename_region(self, command)
        if isinstance(command, SetSelectionCommand):
            return spatial_commands.set_selection(self, command)
        if isinstance(command, ModifyAutomationCommand):
            return command_handlers.modify_automation(self, command)
        if isinstance(command, AttackCommand):
            return command_handlers.attack(self, command)
        if isinstance(command, MoveCommand):
            return command_handlers.move(self, command)
        if isinstance(command, StopCommand):
            return command_handlers.stop(self, command, hold=False)
        if isinstance(command, HoldPositionCommand):
            return command_handlers.stop(self, command, hold=True)
        if isinstance(command, RemoveEntityCommand):
            return self._remove_entity(command)
        if isinstance(command, CreatePatrolCommand):
            return command_handlers.create_patrol(self, command)
        if isinstance(command, CreateDefendCommand):
            return command_handlers.create_defend(self, command)
        if isinstance(command, CreateProductionCommand):
            return production_system.create_production(self, command)
        if isinstance(command, CreateProductionBatchCommand):
            return production_system.create_production_batch(self, command)
        if isinstance(command, CreateConstructionCommand):
            return construction_system.create_construction(self, command)
        if isinstance(command, CreateReinforcementCommand):
            return command_handlers.create_reinforcement(self, command)
        if isinstance(command, CreateRepairAndReturnCommand):
            return command_handlers.create_repair(self, command)
        if isinstance(command, CreateEconomyCommand):
            return command_handlers.create_economy(self, command)
        if isinstance(command, PauseAutomationCommand):
            return automation_lifecycle.pause(self, command.automation_id, command.owner_id)
        if isinstance(command, ResumeAutomationCommand):
            return automation_lifecycle.resume(self, command.automation_id, command.owner_id)
        return self._cancel(command.automation_id, command.owner_id)

    def advance(self, ticks: int = 1) -> None:
        if ticks < 0:
            raise ValueError("tick count cannot be negative")
        for _ in range(ticks):
            self.tick += 1
            self._push_events_this_tick.clear()
            self._stalled_repaths_this_tick = 0
            self._routes.begin_tick()
            self._movement_step_attempt_count = 0
            self._collision_pair_check_count = 0
            self._blocked_recoveries_this_tick = 0
            economy_system.generate_income(self)
            economy_system.spawn_ambient_enemy(self)
            automation_runtime.drive_automations(self)
            movement_system.move_entities(self)
            automation_runtime.settle_automation_formations(self)
            combat_system.drive_projectiles(self)
            combat_system.drive_combat(self)
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
        removed_entity = self.entities[entity_id]
        current_id = self.assignments.pop(entity_id, None)
        suspended_id = self.suspended_assignments.pop(entity_id, None)
        if current_id is not None:
            current = self.automations[current_id]
            current.remove_entity(entity_id)
            self._refresh_gathering_formation(current)
            if current.kind is AutomationKind.PRODUCTION and not current.status.terminal:
                self._transition(current, AutomationStatus.FAILED, "SOURCE_ENTITY_REMOVED")
            else:
                automation_lifecycle.handle_automation_without_entities(self, current)
        if suspended_id is not None:
            suspended = self.automations[suspended_id]
            suspended.remove_entity(entity_id)
            self._refresh_gathering_formation(suspended)
            automation_lifecycle.handle_automation_without_entities(self, suspended)
        for automation in tuple(self.automations.values()):
            if (
                automation.kind is AutomationKind.PRODUCTION
                and not automation.status.terminal
                and _production_parameters(automation).factory_id == entity_id
            ):
                self._transition(automation, AutomationStatus.FAILED, "SOURCE_ENTITY_REMOVED")
            elif (
                automation.kind is AutomationKind.CONSTRUCTION
                and not automation.status.terminal
                and entity_id in automation.entity_ids
            ):
                automation.remove_entity(entity_id)
                if not automation.entity_ids:
                    self._transition(automation, AutomationStatus.FAILED, "BUILDER_UNAVAILABLE")
        self.occupancy.remove(entity_id)
        del self.entities[entity_id]
        if removed_entity.category is EntityCategory.BUILDING:
            self._invalidate_navigation_cache()
        if entity_id in self.selection.entity_ids:
            self.selection = GroundingSelection(
                tuple(item for item in self.selection.entity_ids if item != entity_id),
                self.selection.point_ids,
                self.selection.route_ids,
                self.selection.region_ids,
            )
        for entity in self.entities.values():
            if entity.attack_target_id == entity_id:
                entity.attack_target_id = None
                entity.pursue_target = False
            if entity.last_attacker_id == entity_id:
                entity.last_attacker_id = None
                entity.last_attacked_tick = None
        self._movement_blocked.discard(entity_id)
        self.events.record(
            self.tick,
            EventType.ENTITY_REMOVED,
            entity_id,
            previous_automation_id=current_id,
            automation_id=None,
            reason=command.reason,
        )
        if removed_entity.kind is EntityKind.BUILDER:
            self._start_next_construction()
        return self._accept("remove_entity")

    def snapshot(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "random_seed": self.random_seed,
            "ambient_enemy_spawns": self.ambient_enemy_spawns,
            "enemy_spawn_interval_ticks": self.enemy_spawn_interval_ticks,
            "enemy_spawn_cap": self.enemy_spawn_cap,
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
            "projectiles": {
                projectile_id: projectile.to_dict()
                for projectile_id, projectile in sorted(self.projectiles.items())
            },
            "projectile_traces": [trace.to_dict() for trace in self.projectile_traces],
        }

    def export_state(self) -> dict[str, object]:
        return {
            "tick": self.tick,
            "random_seed": self.random_seed,
            "ambient_enemy_spawns": self.ambient_enemy_spawns,
            "enemy_spawn_interval_ticks": self.enemy_spawn_interval_ticks,
            "enemy_spawn_cap": self.enemy_spawn_cap,
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
            "next_projectile_number": self._next_projectile_number,
            "projectiles": {
                projectile_id: projectile.to_dict()
                for projectile_id, projectile in sorted(self.projectiles.items())
            },
            "projectile_traces": [trace.to_dict() for trace in self.projectile_traces],
            "movement_blocked": sorted(self._movement_blocked),
            "blocked_ticks": dict(sorted(self._blocked_ticks.items())),
            "traffic_reservations": (
                None
                if self._open_force_slots is None
                else {
                    "spacing": self._open_force_slots[0],
                    "slots": {
                        entity_id: list(slot)
                        for entity_id, slot in sorted(self._open_force_slots[1].items())
                        if entity_id in self.entities
                    },
                }
            ),
        }

    def _validate_geometry(self, target: SpatialTarget) -> ValidationFailure | None:
        return spatial_commands.validate_geometry(self, target)

    def _validate_building_placement(
        self,
        kind: EntityKind,
        position: Point,
        *,
        ignore_construction_id: str | None = None,
    ) -> ValidationFailure | None:
        return construction_system.validate_building_placement(
            self, kind, position, ignore_construction_id=ignore_construction_id
        )

    def _cancel(self, automation_id: str, owner_id: str) -> CommandResult:
        return automation_lifecycle.cancel(self, automation_id, owner_id)

    def _drive_construction(self, automation: Automation) -> None:
        construction_system.drive_construction(self, automation)

    def _start_next_construction(self) -> None:
        construction_system.start_next_construction(self)

    def _drive_production(self, automation: Automation) -> None:
        production_system.drive_production(self, automation)

    def _start_next_production(self, factory_id: str) -> None:
        production_system.start_next_production(self, factory_id)

    def _record_production_started(self, automation: Automation) -> None:
        production_system.record_production_started(self, automation)

    def _drive_economy(self, automation: Automation) -> None:
        economy_system.drive_economy(self, automation)

    @staticmethod
    def _reset_movement_liveness(entity: Entity, *, clear_stop: bool = False) -> None:
        movement_system.reset_movement_liveness(entity, clear_stop=clear_stop)

    def _skip_crowded_waypoints(self, entity: Entity) -> None:
        movement_system.skip_crowded_waypoints(self, entity)

    def _activate(
        self,
        automation: Automation,
        entity_ids: tuple[str, ...],
        *,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
        suspend: bool = False,
        assign_entities: bool = True,
    ) -> None:
        automation_lifecycle.activate(
            self,
            automation,
            entity_ids,
            authority=authority,
            suspend=suspend,
            assign_entities=assign_entities,
        )

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
        return automation_lifecycle.new_automation(
            self, kind, title, owner_id, priority, original_instruction, entity_ids, parameters
        )

    def _transition(self, automation: Automation, status: AutomationStatus, reason: str) -> None:
        automation_lifecycle.transition(self, automation, status, reason)

    def _assign(
        self,
        entity_id: str,
        automation: Automation,
        *,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
        suspend: bool = False,
    ) -> None:
        automation_lifecycle.assign(
            self, entity_id, automation, authority=authority, suspend=suspend
        )

    def _refresh_gathering_formation(self, automation: Automation) -> None:
        automation_runtime.refresh_gathering_formation(self, automation)

    def _manual_override_many(self, entity_ids: tuple[str, ...]) -> None:
        automation_lifecycle.manual_override_many(self, entity_ids)

    def _release_automation(self, automation: Automation, *, clear_suspended: bool = False) -> None:
        automation_lifecycle.release_automation(self, automation, clear_suspended=clear_suspended)

    def _resume_suspended_assignment(self, repair_automation: Automation, entity_id: str) -> None:
        automation_lifecycle.resume_suspended_assignment(self, repair_automation, entity_id)

    def _initialize_runtime_entity(self, automation: Automation, entity_id: str) -> None:
        automation_runtime.initialize_runtime_entity(self, automation, entity_id)

    def _attach_production_defense(
        self,
        production: Automation,
        target: PolygonRegion | PolylineTarget,
    ) -> None:
        production_system.attach_production_defense(self, production, target)

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
            try:
                point, path = self._routes.shared_path_to_any(
                    entity.position,
                    self._interaction_points(building),
                    self._building_cells(),
                )
            except PathfindingError:
                continue
            candidates.append((order[building.kind], path.cost, building.entity_id, point, path))
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

    def _validate_entities(
        self,
        entity_ids: tuple[str, ...],
        owner_id: str,
        *,
        require_movable: bool = False,
    ) -> ValidationFailure | None:
        return command_handlers.validate_entities(
            self, entity_ids, owner_id, require_movable=require_movable
        )

    def _validate_claims(
        self,
        automation: Automation,
        entity_ids: tuple[str, ...],
        *,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
        replace_existing: bool = False,
    ) -> ValidationFailure | None:
        return automation_lifecycle.validate_claims(
            self, automation, entity_ids, authority=authority, replace_existing=replace_existing
        )

    def _claim_wins(
        self,
        automation: Automation,
        entity_id: str,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
    ) -> bool:
        return automation_lifecycle.claim_wins(self, automation, entity_id, authority)

    def _owned_automation(
        self, automation_id: str, owner_id: str
    ) -> tuple[Automation | None, ValidationFailure | None]:
        return automation_lifecycle.owned_automation(self, automation_id, owner_id)

    def _start_path(
        self,
        entity: Entity,
        destination: Point,
        path: PathResult,
        source: str,
        state: UnitState,
    ) -> None:
        self._movement_blocked.discard(entity.entity_id)
        self._blocked_ticks.pop(entity.entity_id, None)
        self._reset_movement_liveness(entity, clear_stop=True)
        entity.path = list(
            movement_system.simplify_waypoints(
                self,
                entity.position,
                path.waypoints,
                path.cost,
            )
            if source in {"human", "combat"}
            else path.waypoints
        )
        entity.path_cost = path.cost
        entity.move_target = destination if entity.path else None
        entity.state = state if entity.path else self._state_for_assignment(entity.entity_id)
        if entity.path:
            entity.progress_target = entity.path[0]
            entity.progress_distance = entity.position.distance_to(entity.path[0])
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
        return automation_lifecycle.state_for_assignment(self, entity_id)

    def _allocate_destinations(
        self, entity_ids: tuple[str, ...], target: Point
    ) -> dict[str, Point]:
        selected = frozenset(entity_ids)
        blocked = set(self.occupancy.blocked_cells(selected))
        blocked.update(self._reserved_destination_cells(selected))
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
        center = Point(
            sum(self.entities[entity_id].position.x for entity_id in entity_ids) / len(entity_ids),
            sum(self.entities[entity_id].position.y for entity_id in entity_ids) / len(entity_ids),
        )
        direction_x = target.x - center.x
        direction_y = target.y - center.y
        length = max((direction_x * direction_x + direction_y * direction_y) ** 0.5, 1.0)
        direction_x /= length
        direction_y /= length
        ordered_entities = sorted(
            entity_ids,
            key=lambda entity_id: (
                -(
                    (self.entities[entity_id].position.x - center.x) * direction_x
                    + (self.entities[entity_id].position.y - center.y) * direction_y
                ),
                entity_id,
            ),
        )
        ordered_candidates = sorted(
            candidates,
            key=lambda cell: (
                -(
                    (cell[0] + 0.5 - target.x) * direction_x
                    + (cell[1] + 0.5 - target.y) * direction_y
                ),
                abs(
                    (cell[0] + 0.5 - target.x) * direction_y
                    - (cell[1] + 0.5 - target.y) * direction_x
                ),
                abs(cell[0] + 0.5 - target.x) + abs(cell[1] + 0.5 - target.y),
                cell[1],
                cell[0],
            ),
        )
        destinations: dict[str, Point] = {}
        for entity_id, cell in zip(ordered_entities, ordered_candidates, strict=True):
            destinations[entity_id] = (
                target if cell == target_cell else Point(cell[0] + 0.5, cell[1] + 0.5)
            )
        return destinations

    def _reserved_destination_cells(self, excluding: frozenset[str]) -> set[Cell]:
        return {
            self.game_map.cell_for(entity.move_target)
            for entity_id, entity in self.entities.items()
            if entity_id not in excluding and entity.move_target is not None
        }

    def _blocked_cells_for_mover(
        self, entity_id: str, excluding: frozenset[str]
    ) -> frozenset[Cell]:
        del entity_id, excluding
        return self._building_cells()

    def _nearest_unreserved_destination(self, entity: Entity, target: Point) -> Point | None:
        blocked = set(self.occupancy.blocked_cells(frozenset({entity.entity_id})))
        blocked.update(self._reserved_destination_cells(frozenset({entity.entity_id})))
        target_cell = self.game_map.cell_for(target)
        frontier = deque([target_cell])
        visited = {target_cell}
        while frontier:
            cell = frontier.popleft()
            if self.game_map.is_cell_passable(cell) and cell not in blocked:
                point = Point(cell[0] + 0.5, cell[1] + 0.5)
                if all(
                    other_id == entity.entity_id
                    or not other.is_movable
                    or point.distance_to(other.position) >= 0.9
                    for other_id, other in self.entities.items()
                ):
                    return point
            for neighbor in self._neighbor_cells(cell):
                if neighbor not in visited and self.game_map.contains_cell(neighbor):
                    visited.add(neighbor)
                    frontier.append(neighbor)
        return None

    def _building_cells(self) -> frozenset[Cell]:
        if self._building_cells_cache is None:
            self._building_cells_cache = frozenset(
                cell
                for entity in self.entities.values()
                if entity.category is EntityCategory.BUILDING
                for cell in entity.occupied_cells
            )
        return self._building_cells_cache

    def _gathering_slots(
        self,
        target: SpatialTarget,
        count: int,
        unit_radius: float,
    ) -> tuple[Point, ...]:
        if count <= 0:
            return ()
        if unit_radius <= 0:
            raise ValueError("gathering unit radius must be positive")
        cache_key = (target, unit_radius)
        cached = self._gathering_slot_cache.get(cache_key)
        if cached is None:
            center = target_center(target)
            blocked = self._building_cells()
            anchor = min(
                (
                    (x, y)
                    for y in range(self.game_map.height)
                    for x in range(self.game_map.width)
                    if self.game_map.is_cell_passable((x, y)) and (x, y) not in blocked
                ),
                key=lambda cell: (
                    (cell[0] + 0.5 - center.x) ** 2 + (cell[1] + 0.5 - center.y) ** 2,
                    cell[1],
                    cell[0],
                ),
                default=None,
            )
            if anchor is None:
                raise ValueError("gathering point has no passable space")
            reachable = {anchor}
            frontier = deque([anchor])
            while frontier:
                cell = frontier.popleft()
                for neighbor in self._neighbor_cells(cell):
                    if (
                        neighbor not in reachable
                        and self.game_map.contains_cell(neighbor)
                        and self.game_map.is_cell_passable(neighbor)
                        and neighbor not in blocked
                    ):
                        reachable.add(neighbor)
                        frontier.append(neighbor)
            # Keep settled formations outside the solver's 0.03 contact-pressure margin. Exact
            # diameter packing makes a stationary 1,000-unit formation pay dense collision work
            # forever even though every destination is unique.
            packing_radius = (
                max(unit_radius + 0.02, SETTLED_FORMATION_SPACING / 2)
                if unit_radius <= 0.31
                else unit_radius + 0.02
            )
            horizontal_spacing = packing_radius * 2
            vertical_spacing = sqrt(3) * packing_radius
            minimum_row = floor(-center.y / vertical_spacing) - 1
            maximum_row = ceil((self.game_map.height - center.y) / vertical_spacing) + 1
            candidates: list[Point] = []
            for row in range(minimum_row, maximum_row + 1):
                y = center.y + row * vertical_spacing
                if not 0 <= y < self.game_map.height:
                    continue
                row_offset = packing_radius if row % 2 else 0.0
                minimum_column = floor((-center.x - row_offset) / horizontal_spacing) - 1
                maximum_column = (
                    ceil((self.game_map.width - center.x - row_offset) / horizontal_spacing) + 1
                )
                for column in range(minimum_column, maximum_column + 1):
                    x = center.x + row_offset + column * horizontal_spacing
                    if not 0 <= x < self.game_map.width:
                        continue
                    point = Point(x, y)
                    if self.game_map.cell_for(point) in reachable:
                        candidates.append(point)
            cached = tuple(
                sorted(
                    candidates,
                    key=lambda point: (
                        point.distance_to(center),
                        point.y,
                        point.x,
                    ),
                )
            )
            self._gathering_slot_cache[cache_key] = cached
            self._gathering_reachable_cache[target] = frozenset(reachable)
        if count > len(cached):
            raise ValueError("gathering point has insufficient physical map space")
        return cached[:count]

    def _invalidate_navigation_cache(self) -> None:
        self._open_force_slots = None
        self._building_cells_cache = None
        self._routes.clear()
        self._gathering_slot_cache.clear()
        self._gathering_reachable_cache.clear()
        self._waypoint_corridor_cache.clear()

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


def _squared_distance(first: Point, second: Point) -> float:
    offset_x = first.x - second.x
    offset_y = first.y - second.y
    return offset_x * offset_x + offset_y * offset_y


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


def _repair_parameters(automation: Automation) -> RepairParameters:
    if not isinstance(automation.parameters, RepairParameters):
        raise TypeError("automation does not have repair parameters")
    return automation.parameters
