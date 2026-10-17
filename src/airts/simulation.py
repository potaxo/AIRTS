"""Deterministic command, automation, spatial-grounding, and simulation runtime."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from gc import collect as collect_garbage
from math import ceil, floor, sqrt

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationParameters,
    AutomationStatus,
    ConstructionParameters,
    area_coverage_order,
    assign_formation_slots,
    target_center,
    target_contains,
)
from airts.contracts.automations import (
    production_parameters as _production_parameters,
)
from airts.contracts.commands import (
    AttackCommand,
    CancelUpgradeCommand,
    Command,
    CommandResult,
    ControlAuthority,
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
    ReclaimBuildingCommand,
    ReduceProductionCommand,
    RemoveEntityCommand,
    RenameRegionCommand,
    RepairBuildingCommand,
    ResumeAutomationCommand,
    SetDeveloperModeCommand,
    SetSelectionCommand,
    StopCommand,
    UpgradeEntityCommand,
    ValidationFailure,
    ValidationPhase,
    command_to_dict,
)
from airts.contracts.events import EventLog, EventType
from airts.contracts.spatial import (
    GroundingSelection,
    Point,
    PointTarget,
    SpatialStore,
    SpatialTarget,
)
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.collision.solver import warm_collision_kernel
from airts.navigation.pathfinding import PathfindingError, PathResult, RoutingService
from airts.navigation.spatial_index import SpatialIndex, warm_spatial_index
from airts.systems import (
    automation_control,
    automation_execution,
    command_handlers,
    opponent_ai,
    spatial_commands,
)
from airts.systems import combat as combat_system
from airts.systems import construction as construction_system
from airts.systems import economy as economy_system
from airts.systems import movement as movement_system
from airts.systems import production as production_system
from airts.systems import reclamation as reclamation_system
from airts.systems import repair as repair_system
from airts.systems import skirmish as skirmish_system
from airts.systems import upgrades as upgrade_system
from airts.systems.repair_positions import service_route
from airts.world._numeric import BoolArray
from airts.world.entities import Entity, Projectile, ProjectileTrace, UnitState
from airts.world.map import Cell, GameMap, OccupancyGrid
from airts.world.opponents import OpponentState
from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory
from airts.world.visibility import VisibilitySystem


@dataclass(frozen=True, slots=True)
class _GatheringSlots:
    radius: float
    points: tuple[Point, ...]


class Simulation:
    LARGE_SCENE_GC_ENTITY_THRESHOLD = 512
    LARGE_SCENE_GC_CELL_THRESHOLD = 256 * 256
    TICKS_PER_SECOND = 10
    INCOME_INTERVAL_TICKS = 2 * TICKS_PER_SECOND
    TICK_SECONDS = 1.0 / TICKS_PER_SECOND
    NO_PROGRESS_YIELD_TICKS = 30
    DESTINATION_REPATH_TICKS = 50
    MIN_PROGRESS_DISTANCE = 0.02
    DEFEND_RESPONSE_RADIUS = 4.0
    DEFEND_PURSUIT_RADIUS = 7.0
    PATROL_PURSUIT_RADIUS = 7.0
    COMBAT_SUPPORT_RADIUS = 8.0
    COMBAT_SUPPORT_MANUAL_COOLDOWN_TICKS = 5 * TICKS_PER_SECOND
    DEFEND_ATTACK_MEMORY_TICKS = 30
    DEFEND_STATION_TOLERANCE = 0.05
    DEFEND_FORMATION_TOLERANCE = 1.0
    DEFAULT_ENEMY_SPAWN_INTERVAL_TICKS = 10
    DEFAULT_ENEMY_SPAWN_CAP = 100
    AUTOMATION_ROUTE_BUDGET = 16
    TOTAL_AUTOMATION_ROUTE_BUDGET = 32
    GATHERING_PATH_BUDGET = TOTAL_AUTOMATION_ROUTE_BUDGET
    COMBAT_PATH_BUDGET = 16
    GATHERING_CACHE_TARGET_LIMIT = 16
    GATHERING_CACHE_SLOT_LIMIT = 16_384
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
        self.game_map = game_map
        self._owner_teams = {slot.owner_id: slot.team for slot in game_map.match_slots}
        if game_map.match_slots and ambient_enemy_spawns:
            raise ValueError("ambient enemy spawning is unavailable in team matches")
        self.match_result: int | None = None
        self.opponents = {
            slot.owner_id: OpponentState()
            for slot in game_map.match_slots
            if slot.controller == "ai"
        }
        self._opponent_command_count = 0
        self._opponent_candidate_count = 0
        self._opponent_forecast_count = 0
        self._terrain_obstacles = frozenset(
            (x, y)
            for y, row in enumerate(game_map.terrain)
            for x, terrain in enumerate(row)
            if not terrain.passable
        )
        self.random_seed = random_seed
        self.ambient_enemy_spawns = ambient_enemy_spawns
        self.enemy_spawn_interval_ticks = enemy_spawn_interval_ticks
        self.enemy_spawn_cap = enemy_spawn_cap
        self.tick = 0
        self.developer_mode = False
        self.developer_income_ticks = 0
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
        self._live_automation_candidates: tuple[Automation, ...] = ()
        self.assignments: dict[str, str] = {}
        self.suspended_assignments: dict[str, str] = {}
        self.events = EventLog()
        self.visibility = VisibilitySystem(game_map)
        self.resources = {
            owner_id: (1000 if game_map.match_slots else 500)
            for owner_id in {entity.owner_id for entity in self.entities.values()}
        }
        self.spatial = SpatialStore()
        self.selection = GroundingSelection()
        self._next_automation_number = 1
        self._next_entity_number = 1
        self._next_projectile_number = 1
        self.projectiles: dict[str, Projectile] = {}
        self.projectile_traces: list[ProjectileTrace] = []
        self._command_history: list[dict[str, object]] = []
        self._removal_batch: set[str] | None = None
        self._removal_formations: dict[tuple[str, SpatialTarget], None] = {}
        self._removal_restart_construction = False
        self._movement_workspace = movement_system.MovementWorkspace(self.entities)
        self._movement_blocked: set[str] = set()
        self._movement_step_attempt_count = 0
        self._collision_pair_check_count = 0
        self._contact_candidate_count = 0
        self._contact_component_count = 0
        self._contact_coordination_work = 0
        self._contact_blocked_component_count = 0
        self._movement_replan_count = 0
        self._building_cells_cache: frozenset[Cell] | None = None
        self._routes = RoutingService(
            game_map,
            automation_budget=self.TOTAL_AUTOMATION_ROUTE_BUDGET,
            combat_budget=self.COMBAT_PATH_BUDGET,
        )
        self._gathering_slot_cache: OrderedDict[
            tuple[SpatialTarget, float, bool], _GatheringSlots
        ] = OrderedDict()
        self._gathering_reachable_cache: OrderedDict[SpatialTarget, BoolArray] = OrderedDict()
        warm_collision_kernel()
        warm_spatial_index()
        self._update_visibility()
        if (
            len(game_map.entities) >= self.LARGE_SCENE_GC_ENTITY_THRESHOLD
            or game_map.width * game_map.height >= self.LARGE_SCENE_GC_CELL_THRESHOLD
        ):
            # Drain previous-scene and JIT initialization cycles at the non-frame
            # startup boundary, including large maps with a small opening army.
            # Normal automatic collection remains enabled throughout the match.
            collect_garbage()

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
            automation
            for automation in self._live_automation_candidates
            if not automation.status.terminal
            and (automation.entity_ids or automation.has_future_source)
        )

    def _rebuild_live_automations(self, automations: Iterable[Automation]) -> None:
        """Rebuild the disposable query index on activation, retirement or load."""
        self._live_automation_candidates = tuple(
            sorted(
                (automation for automation in automations if not automation.status.terminal),
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
        """Local contact/goal constraints evaluated, including repeated contact sweeps."""

        return self._collision_pair_check_count

    @property
    def contact_candidate_count(self) -> int:
        """Local broadphase candidates, including repeated visits this tick."""
        return self._contact_candidate_count

    @property
    def contact_component_count(self) -> int:
        """Compressed contact components admitted for coordinated release."""
        return self._contact_component_count

    @property
    def contact_coordination_work(self) -> int:
        """Prediction pairs, fringe queue visits and affine member evaluations."""
        return self._contact_coordination_work

    @property
    def contact_blocked_component_count(self) -> int:
        """Compressed components with no certified release seed this tick."""
        return self._contact_blocked_component_count

    @property
    def movement_replan_count(self) -> int:
        """Admitted stalled-path and internal anchor route attempts this tick."""
        return self._movement_replan_count

    def execute(self, command: Command) -> CommandResult:
        controlled_ids = command_handlers.player_controlled_entity_ids(self, command)
        result = self._execute_command(command, record=True)
        if result.accepted:
            for key in controlled_ids:
                self.entities[key].last_player_control_tick = self.tick
        return result

    def are_hostile(self, first_owner: str, second_owner: str) -> bool:
        if first_owner == second_owner:
            return False
        if not self.game_map.match_slots:
            return True
        teams = self._owner_teams
        return not (
            first_owner in teams
            and second_owner in teams
            and teams[first_owner] == teams[second_owner]
        )

    def _execute_command(self, command: Command, *, record: bool) -> CommandResult:
        if record:
            self._command_history.append({"tick": self.tick, "command": command_to_dict(command)})
        if isinstance(command, UpgradeEntityCommand):
            return upgrade_system.start_upgrade(self, command)
        if isinstance(command, CancelUpgradeCommand):
            return upgrade_system.cancel_upgrade(self, command)
        if isinstance(command, SetDeveloperModeCommand):
            return command_handlers.set_developer_mode(self, command)
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
        if isinstance(command, ReclaimBuildingCommand):
            return reclamation_system.reclaim_building(self, command)
        if isinstance(command, RepairBuildingCommand):
            return repair_system.repair_building(self, command)
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
        if isinstance(command, ReduceProductionCommand):
            return production_system.reduce_production(self, command)
        if isinstance(command, CreateConstructionCommand):
            return construction_system.create_construction(self, command)
        if isinstance(command, CreateReinforcementCommand):
            return command_handlers.create_reinforcement(self, command)
        if isinstance(command, CreateRepairAndReturnCommand):
            return command_handlers.create_repair(self, command)
        if isinstance(command, CreateEconomyCommand):
            return command_handlers.create_economy(self, command)
        if isinstance(command, PauseAutomationCommand):
            return automation_control.pause(self, command.automation_id, command.owner_id)
        if isinstance(command, ResumeAutomationCommand):
            return automation_control.resume(self, command.automation_id, command.owner_id)
        return self._cancel(command.automation_id, command.owner_id)

    def advance(self, ticks: int = 1) -> None:
        if ticks < 0:
            raise ValueError("tick count cannot be negative")
        for _ in range(ticks):
            if self.game_map.match_slots and self.match_result is not None:
                break
            self.tick += 1
            self._routes.begin_tick()
            self._movement_step_attempt_count = 0
            self._collision_pair_check_count = 0
            self._contact_candidate_count = 0
            self._contact_component_count = 0
            self._contact_coordination_work = 0
            self._contact_blocked_component_count = 0
            self._movement_replan_count = 0
            self._opponent_command_count = 0
            self._opponent_candidate_count = 0
            self._opponent_forecast_count = 0
            upgrade_system.advance_upgrades(self)
            economy_system.generate_income(self)
            economy_system.generate_developer_income(self)
            economy_system.spawn_ambient_enemy(self)
            if self.game_map.match_slots:
                opponent_ai.drive_opponents(self)
            movement_system.drive_move_queues(self)
            self._drive_automations()
            repair_system.drive_builders(self)
            reclamation_system.drive_reclamation(self)
            movement_system.move_entities(self)
            combat_system.drive_projectiles(self)
            repair_system.drive_hubs(self)
            combat_system.drive_combat(self)
            self._update_visibility()
            if self.game_map.match_slots:
                self.match_result = skirmish_system.evaluate_result(self)

    def _drive_automations(self) -> None:
        construction_system.start_next_construction(self)
        automation_ids = sorted(
            automation.automation_id
            for automation in self._live_automation_candidates
            if automation.status in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}
        )
        if automation_ids:
            offset = self.tick % len(automation_ids)
            automation_ids = automation_ids[offset:] + automation_ids[:offset]
        # Positions and owners are fixed during this pass. Production/construction
        # can add entities between patrols/defenses, so discard snapshots on membership growth.
        queries = automation_execution.AutomationQueries()
        entity_count = len(self.entities)
        for automation_id in automation_ids:
            automation = self.automations[automation_id]
            if automation.status not in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}:
                continue
            if automation.kind in {AutomationKind.PATROL, AutomationKind.DEFEND}:
                automation_execution.drive_auto_repair(self, automation)
                if len(self.entities) != entity_count:
                    queries = automation_execution.AutomationQueries()
                    entity_count = len(self.entities)
            if automation.kind is AutomationKind.PATROL:
                automation_execution.drive_patrol(self, automation, queries)
            elif automation.kind is AutomationKind.DEFEND:
                automation_execution.drive_defend(self, automation, queries)
            elif automation.kind is AutomationKind.PRODUCTION:
                production_system.drive_production(self, automation)
            elif automation.kind is AutomationKind.REINFORCEMENT:
                automation_execution.drive_reinforcement(self, automation)
            elif automation.kind is AutomationKind.ECONOMY:
                economy_system.drive_economy(self, automation)
            elif automation.kind is AutomationKind.CONSTRUCTION:
                construction_system.drive_construction(self, automation)
                queries = automation_execution.AutomationQueries()
            else:
                automation_execution.drive_repair(self, automation)

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
            automation_execution.refresh_gathering_formation(self, current)
            if current.kind is AutomationKind.PRODUCTION and not current.status.terminal:
                self._transition(current, AutomationStatus.FAILED, "SOURCE_ENTITY_REMOVED")
            else:
                automation_control.handle_automation_without_entities(self, current)
        if suspended_id is not None:
            suspended = self.automations[suspended_id]
            suspended.remove_entity(entity_id)
            automation_execution.refresh_gathering_formation(self, suspended)
            automation_control.handle_automation_without_entities(self, suspended)
        for automation in self._live_automation_candidates:
            if (
                automation.kind is AutomationKind.CONSTRUCTION
                and not automation.status.terminal
                and isinstance(automation.parameters, ConstructionParameters)
                and automation.parameters.site_entity_id == entity_id
            ):
                self._transition(automation, AutomationStatus.FAILED, "CONSTRUCTION_DESTROYED")
                self._release_automation(automation)
            elif (
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
        for opponent in self.opponents.values():
            opponent.remove_entity(entity_id)
        if removed_entity.category is EntityCategory.BUILDING:
            self._invalidate_navigation_cache()
        if entity_id in self.selection.entity_ids:
            self.selection = GroundingSelection(
                tuple(item for item in self.selection.entity_ids if item != entity_id),
                self.selection.point_ids,
                self.selection.route_ids,
                self.selection.region_ids,
            )
        if self._removal_batch is None:
            self._clear_removed_references({entity_id})
        else:
            self._removal_batch.add(entity_id)
        self._movement_blocked.discard(entity_id)
        self.events.record(
            self.tick,
            EventType.ENTITY_REMOVED,
            entity_id,
            previous_automation_id=current_id,
            automation_id=None,
            reason=command.reason,
        )
        if removed_entity.kind is EntityKind.BUILDER or not removed_entity.is_complete:
            if self._removal_batch is None:
                construction_system.start_next_construction(self)
            else:
                self._removal_restart_construction = True
        return self._accept("remove_entity")

    @contextmanager
    def _batch_entity_removals(self) -> Iterator[None]:
        """Resolve deaths immediately; coordinate survivors once at the phase boundary."""
        assert self._removal_batch is None
        removed_ids: set[str] = set()
        self._removal_batch = removed_ids
        try:
            yield
        finally:
            self._removal_batch = None
            formations = tuple(self._removal_formations)
            self._removal_formations.clear()
            restart = self._removal_restart_construction
            self._removal_restart_construction = False
            if removed_ids:
                self._clear_removed_references(removed_ids)
            for owner, target in formations:
                automation_execution.coordinate_shared_defend_stations(self, target, owner)
            if restart:
                construction_system.start_next_construction(self)

    def _clear_removed_references(self, removed_ids: set[str]) -> None:
        gatherings: dict[tuple[str, Point], list[str]] = {}
        for key in sorted(self.entities):
            entity = self.entities[key]
            if entity.attack_target_id in removed_ids:
                if entity.attack_gather_point is not None:
                    gatherings.setdefault((entity.owner_id, entity.attack_gather_point), []).append(
                        entity.entity_id
                    )
                    entity.attack_gather_point = None
                if entity.pursue_target:
                    entity.path.clear()
                    entity.move_target = None
                    entity.state = self._state_for_assignment(entity.entity_id)
                    movement_system.reset_movement_liveness(entity)
                entity.attack_target_id = None
                entity.pursue_target = False
            if entity.repair_target_id in removed_ids:
                entity.repair_target_id = None
                if entity.kind is EntityKind.BUILDER:
                    entity.path.clear()
                    entity.move_target = None
                    entity.state = UnitState.IDLE
                    movement_system.reset_movement_liveness(entity)
            if entity.reclaim_target_id in removed_ids:
                entity.reclaim_target_id = None
                entity.path.clear()
                entity.move_target = None
                entity.state = UnitState.IDLE
                movement_system.reset_movement_liveness(entity)
            if entity.last_attacker_id in removed_ids:
                entity.last_attacker_id = None
                entity.last_attacked_tick = None
        for (_, point), ids in gatherings.items():
            try:
                destinations = self._allocate_destinations(tuple(ids), point, gathering=True)
            except PathfindingError as error:
                for key in ids:
                    self.entities[key].state = UnitState.WAITING
                    self.entities[key].move_queue[:] = [point]
                    self.events.record(
                        self.tick,
                        EventType.PATHFINDING_FAILED,
                        key,
                        operation="attack_gather",
                        reason=str(error),
                    )
                continue
            for key in ids:
                self.entities[key].move_queue[:] = [destinations[key]]
                self.entities[key].state = UnitState.MOVING

    def snapshot(self) -> dict[str, object]:
        return self._domain_state() | {
            "map": {"id": self.game_map.map_id, "version": self.game_map.map_version},
            "occupancy": self.occupancy.snapshot(),
        }

    def _domain_state(self) -> dict[str, object]:
        """Serialize the authoritative fields shared by snapshots and complete saves."""

        return (
            {
                "match_result": self.match_result,
                "opponents": {
                    owner: opponent.to_dict() for owner, opponent in sorted(self.opponents.items())
                },
            }
            if self.game_map.match_slots
            else {}
        ) | {
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
            "developer_mode": self.developer_mode,
            "developer_income_ticks": self.developer_income_ticks,
            "spatial": self.spatial.to_dict(),
            "selection": self.selection.to_dict(),
            "projectiles": {
                projectile_id: projectile.to_dict()
                for projectile_id, projectile in sorted(self.projectiles.items())
            },
            "projectile_traces": [trace.to_dict() for trace in self.projectile_traces],
        }

    def export_state(self, *, include_events: bool = True) -> dict[str, object]:
        """Export state; adapters may capture immutable events separately for deferred encoding."""
        return self._domain_state() | {
            **(
                {"events": [event.to_dict() for event in self.events.events]}
                if include_events
                else {}
            ),
            "command_history": list(self._command_history),
            "next_automation_number": self._next_automation_number,
            "next_entity_number": self._next_entity_number,
            "next_projectile_number": self._next_projectile_number,
            "movement_blocked": sorted(self._movement_blocked),
            "blocked_ticks": {},  # Retired save-v11 field; no runtime congestion counter.
        }

    def _invalidate_navigation_cache(self) -> None:
        self._building_cells_cache = None
        self._routes.clear()
        self._gathering_slot_cache.clear()
        self._gathering_reachable_cache.clear()

    def _allocate_destinations(
        self, entity_ids: tuple[str, ...], target: Point, *, gathering: bool = False
    ) -> dict[str, Point]:
        blocked = self._building_cells()
        static_obstacles = self._terrain_obstacles | blocked
        members = [self.entities[key] for key in entity_ids]
        maximum_radius = max(collision_radius(entity.kind) for entity in members)
        if not gathering:
            center = Point(
                sum(self.entities[key].position.x for key in entity_ids) / len(entity_ids),
                sum(self.entities[key].position.y for key in entity_ids) / len(entity_ids),
            )
            # Fit the existing shape at a map edge instead of reshuffling every unit.
            dx = max(
                max(collision_radius(e.kind) - e.position.x for e in members),
                min(
                    target.x - center.x,
                    min(
                        self.game_map.width - collision_radius(e.kind) - e.position.x
                        for e in members
                    ),
                ),
            )
            dy = max(
                max(collision_radius(e.kind) - e.position.y for e in members),
                min(
                    target.y - center.y,
                    min(
                        self.game_map.height - collision_radius(e.kind) - e.position.y
                        for e in members
                    ),
                ),
            )
            translated = {e.entity_id: Point(e.position.x + dx, e.position.y + dy) for e in members}
            index = SpatialIndex(translated)
            separated = True
            for entity in members:
                point = translated[entity.entity_id]
                radius = collision_radius(entity.kind)
                for neighbor_id in index.nearby(point, radius + maximum_radius):
                    if neighbor_id <= entity.entity_id:
                        continue
                    self._collision_pair_check_count += 1
                    contact = radius + collision_radius(self.entities[neighbor_id].kind)
                    if point.distance_to(translated[neighbor_id]) < contact - 1e-8:
                        separated = False
                        break
                if not separated:
                    break
            # Translation preserves physical compression as well as shape. Reuse the
            # existing reachable slot allocator when an imported/recovering selection
            # cannot all settle at those translated centers.
            if separated and all(
                self.game_map.is_passable(point)
                and self.game_map.cell_for(point) not in blocked
                and movement_system.position_clear(
                    point,
                    collision_radius(self.entities[key].kind),
                    self.game_map.width,
                    self.game_map.height,
                    static_obstacles,
                )
                for key, point in translated.items()
            ):
                return translated
        slots = self._gathering_slots(
            PointTarget(target),
            len(entity_ids),
            maximum_radius,
            require_count=False,
        )
        available = slots
        if len(available) != len(entity_ids):
            raise PathfindingError("INSUFFICIENT_DESTINATIONS")
        if len(entity_ids) == 1 and movement_system.position_clear(
            target, maximum_radius, self.game_map.width, self.game_map.height, static_obstacles
        ):
            available = (target,)
        return assign_formation_slots(
            {key: self.entities[key].position for key in entity_ids}, available, target
        )

    def _gathering_slots(
        self,
        target: SpatialTarget,
        count: int,
        unit_radius: float,
        *,
        require_count: bool = True,
        spread: bool = False,
    ) -> tuple[Point, ...]:
        if count <= 0:
            return ()
        key = (target, unit_radius, spread)
        entry = self._gathering_slot_cache.get(key)
        if entry is not None:
            self._gathering_slot_cache.move_to_end(key)
        cached = entry.points if entry is not None else ()
        if len(cached) < count:
            center = target_center(target)
            blocked = self._building_cells()
            static_obstacles = self._terrain_obstacles | blocked
            reachable = self._gathering_reachable(target)
            # Leave a unit-width aisle between settled neighbors.
            spacing = unit_radius * 4 + 0.12
            row_height = spacing * sqrt(3) / 2
            map_radius = max(
                center.distance_to(Point(x, y))
                for x in (0, self.game_map.width)
                for y in (0, self.game_map.height)
            )
            previous_radius = entry.radius if entry is not None else -1.0
            search_radius = min(map_radius, max(spacing * sqrt(count), previous_radius * 2))
            if spread:
                extent = (
                    target.radius
                    if isinstance(target, PointTarget)
                    else max(p.distance_to(center) for p in target.points)
                )
                search_radius = min(map_radius, max(search_radius, extent))
            # Each disk is complete within its radius. Expanding only the missing
            # annulus preserves the full-map nearest prefix and established slots.
            while True:
                points = list(cached)
                for row in range(
                    floor(max(-center.y, -search_radius) / row_height),
                    min(
                        ceil((self.game_map.height - center.y) / row_height),
                        floor(search_radius / row_height) + 1,
                    ),
                ):
                    station_y = center.y + row * row_height
                    offset = (row % 2) * spacing / 2
                    for column in range(
                        floor((max(-center.x, -search_radius) - offset) / spacing),
                        min(
                            ceil((self.game_map.width - center.x - offset) / spacing),
                            floor((search_radius - offset) / spacing) + 1,
                        ),
                    ):
                        point = Point(center.x + column * spacing + offset, station_y)
                        if (
                            self.game_map.contains(point)
                            and previous_radius < point.distance_to(center) <= search_radius
                            and reachable[floor(point.y), floor(point.x)]
                            and movement_system.position_clear(
                                point,
                                unit_radius,
                                self.game_map.width,
                                self.game_map.height,
                                static_obstacles,
                            )
                        ):
                            points.append(point)
                cached = tuple(sorted(points, key=lambda p: (p.distance_to(center), p.y, p.x)))
                if len(cached) >= count or search_radius == map_radius:
                    break
                previous_radius = search_radius
                search_radius = min(map_radius, search_radius * 2)
            if spread:
                interior = tuple(p for p in cached if target_contains(target, p))
                outside = tuple(p for p in cached if not target_contains(target, p))
                cached = area_coverage_order(interior) + outside
            if len(cached) <= self.GATHERING_CACHE_SLOT_LIMIT:
                self._gathering_slot_cache[key] = _GatheringSlots(search_radius, cached)
                self._gathering_slot_cache.move_to_end(key)
                while (
                    len(self._gathering_slot_cache) > self.GATHERING_CACHE_TARGET_LIMIT
                    or sum(len(item.points) for item in self._gathering_slot_cache.values())
                    > self.GATHERING_CACHE_SLOT_LIMIT
                ):
                    self._gathering_slot_cache.popitem(last=False)
            else:
                self._gathering_slot_cache.pop(key, None)
        if require_count and count > len(cached):
            raise ValueError("gathering point has insufficient map space")
        return cached[:count]

    def _gathering_reachable(self, target: SpatialTarget) -> BoolArray:
        reachable = self._gathering_reachable_cache.get(target)
        if reachable is None:
            blocked = self._building_cells()
            anchor = self._routes.nearest_cell(target_center(target), blocked)
            reachable = next(
                (
                    region
                    for region in self._gathering_reachable_cache.values()
                    if region[anchor[1], anchor[0]]
                ),
                None,
            )
            if reachable is None:
                reachable = self._routes.reachable_cells(anchor, blocked)
            self._gathering_reachable_cache[target] = reachable
            if len(self._gathering_reachable_cache) > self.GATHERING_CACHE_TARGET_LIMIT:
                self._gathering_reachable_cache.popitem(last=False)
        self._gathering_reachable_cache.move_to_end(target)
        return reachable

    def _cancel(self, automation_id: str, owner_id: str) -> CommandResult:
        return automation_control.cancel(self, automation_id, owner_id)

    def _activate(
        self,
        automation: Automation,
        entity_ids: tuple[str, ...],
        *,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
        suspend: bool = False,
        assign_entities: bool = True,
    ) -> None:
        automation_control.activate(
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
        return automation_control.new_automation(
            self, kind, title, owner_id, priority, original_instruction, entity_ids, parameters
        )

    def _transition(self, automation: Automation, status: AutomationStatus, reason: str) -> None:
        automation_control.transition(self, automation, status, reason)

    def _assign(
        self,
        entity_id: str,
        automation: Automation,
        *,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
        suspend: bool = False,
    ) -> None:
        automation_control.assign(self, entity_id, automation, authority=authority, suspend=suspend)

    def _release_automation(self, automation: Automation, *, clear_suspended: bool = False) -> None:
        automation_control.release_automation(self, automation, clear_suspended=clear_suspended)

    def _resume_suspended_assignment(self, repair_automation: Automation, entity_id: str) -> None:
        automation_control.resume_suspended_assignment(self, repair_automation, entity_id)

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
                or not building.is_complete
                or building.kind not in order
                or (required_id is not None and building.entity_id != required_id)
            ):
                continue
            try:
                point, path = service_route(self, entity, building)
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
        return automation_control.validate_claims(
            self, automation, entity_ids, authority=authority, replace_existing=replace_existing
        )

    def _claim_wins(
        self,
        automation: Automation,
        entity_id: str,
        authority: ControlAuthority = ControlAuthority.AUTOMATION,
    ) -> bool:
        return automation_control.claim_wins(self, automation, entity_id, authority)

    def _state_for_assignment(self, entity_id: str) -> UnitState:
        return automation_control.state_for_assignment(self, entity_id)

    def _building_cells(self) -> frozenset[Cell]:
        if self._building_cells_cache is None:
            self._building_cells_cache = frozenset(
                cell
                for entity in self.entities.values()
                if entity.category is EntityCategory.BUILDING
                for cell in entity.occupied_cells
            )
        return self._building_cells_cache

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
