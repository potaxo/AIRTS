"""Weapons, reclamation, drag construction and free repair-area arrival."""

from math import isfinite
from unittest.mock import patch

import pygame
import pytest
from tests.integration.opponent_scenarios import opening_army, opponent_scenario

from airts.adapters.persistence import PersistenceError, capture_save, load_simulation_data
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import (
    AutomationStatus,
    ConstructionParameters,
    RepairParameters,
    RepairPhase,
)
from airts.contracts.commands import (
    AttackCommand,
    CreateConstructionCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
    ReclaimBuildingCommand,
    RepairBuildingCommand,
    StopCommand,
    command_from_dict,
    command_to_dict,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PolylineTarget
from airts.presentation.app import AirtsApp
from airts.presentation.palette import RECLAIM_COLOR, WORK_BEAM_WIDTH
from airts.presentation.rendering import RendererBackend, SoftwareRenderer, reclaim_links
from airts.presentation.ui import selection
from airts.simulation import Simulation
from airts.systems.opponent_ai import engagement_margin, force_strength
from airts.systems.repair_positions import service_points
from airts.world.entities import Entity, Projectile, UnitState
from airts.world.map import footprint_cells, load_map_data
from airts.world.units import catalog
from airts.world.units.catalog import EntityKind


def world() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "building_orders",
                "name": "Building orders",
                "width": 48,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "builder", "kind": "builder", "position": [2.5, 4.5]},
                    {"id": "builder2", "kind": "builder", "position": [3.5, 4.5]},
                    {"id": "factory", "kind": "factory", "position": [12, 10]},
                    {"id": "hub", "kind": "repair_hub", "position": [22, 12]},
                    {"id": "a", "kind": "light_tank", "position": [32.5, 12.5]},
                    {"id": "b", "kind": "heavy_tank", "position": [32.5, 14.5]},
                ],
            }
        )
    )


def restored(simulation: Simulation) -> Simulation:
    save = capture_save(simulation)
    return load_simulation_data(
        {
            "schema": save.schema,
            "map": save.game_map,
            "state": save.state | {"events": [e.to_dict() for e in save.events]},
        }
    )


def test_non_builder_kind_can_build_repair_and_reclaim_from_its_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(catalog._ENTITY_PROFILES, EntityKind.SCOUT, EntityKind.BUILDER.profile)
    simulation = world()
    simulation.entities["builder"].kind = EntityKind.SCOUT
    created = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(4, 5))
    )
    assert created.accepted
    simulation.advance()
    simulation = restored(simulation)
    simulation.advance(Simulation.CONSTRUCTION_BUILD_TICKS)
    job = simulation.automations[created.automation_id or ""]
    assert job.status is AutomationStatus.COMPLETED
    assert isinstance(job.parameters, ConstructionParameters)
    building_id = job.parameters.constructed_entity_id
    assert building_id is not None

    building = simulation.entities[building_id]
    building.health -= 10
    assert simulation.execute(RepairBuildingCommand(("builder",), building_id)).accepted
    simulation = restored(simulation)
    simulation.advance(2)
    assert simulation.entities[building_id].health == building.profile.max_health

    assert simulation.execute(ReclaimBuildingCommand(("builder",), building_id)).accepted
    simulation = restored(simulation)
    simulation.advance(60)
    assert building_id not in simulation.entities
    refunds = [
        event
        for event in simulation.events.events
        if event.event_type is EventType.RESOURCE_CHANGED
        and event.details.get("reason") == "BUILDING_RECLAIMED"
    ]
    assert len(refunds) == 1
    assert refunds[0].details["amount"] == building.profile.construction_cost * 70 // 100


@pytest.mark.parametrize("order", ["move", "patrol", "stop"])
def test_new_order_detaches_only_selected_builder_from_active_and_queued_construction(
    order: str,
) -> None:
    simulation = world()
    simulation.resources["player"] = 2_000
    jobs = []
    for point in (Point(40, 20), Point(40, 24)):
        created = simulation.execute(
            CreateConstructionCommand(
                "builder",
                EntityKind.FACTORY,
                point,
                builder_ids=("builder", "builder2"),
                queued=bool(jobs),
            )
        )
        assert created.accepted
        jobs.append(created.automation_id)
    command = (
        MoveCommand(("builder",), Point(6.5, 4.5))
        if order == "move"
        else CreatePatrolCommand(("builder",), PolylineTarget((Point(4.5, 4.5), Point(8.5, 4.5))))
        if order == "patrol"
        else StopCommand(("builder",))
    )
    assert simulation.execute(command).accepted
    for job_id in jobs:
        job = simulation.automations[job_id or ""]
        assert job.entity_ids == ["builder2"]
        assert job.status is not AutomationStatus.CANCELED
    simulation = restored(simulation)
    simulation.advance(350)
    assert all("builder" not in simulation.automations[job_id or ""].entity_ids for job_id in jobs)
    assert all(
        simulation.automations[job_id or ""].status is AutomationStatus.COMPLETED for job_id in jobs
    )


@pytest.mark.parametrize("work", ["repair", "reclaim"])
def test_queued_move_waits_for_an_approaching_builders_direct_work(work: str) -> None:
    simulation = world()
    simulation.entities["factory"].health = 200
    command = (
        RepairBuildingCommand(("builder",), "factory")
        if work == "repair"
        else ReclaimBuildingCommand(("builder",), "factory")
    )
    assert simulation.execute(command).accepted
    simulation.advance()
    builder = simulation.entities["builder"]
    assert builder.path
    assert simulation.execute(MoveCommand(("builder",), Point(6.5, 4.5), queued=True)).accepted
    assert (
        builder.repair_target_id if work == "repair" else builder.reclaim_target_id
    ) == "factory"
    assert builder.move_target != Point(6.5, 4.5)
    simulation.advance(160)
    assert builder.position == Point(6.5, 4.5)
    if work == "repair":
        assert simulation.entities["factory"].health == EntityKind.FACTORY.profile.max_health
    else:
        assert "factory" not in simulation.entities


def test_queued_movement_runs_fifo_survives_save_and_replays_and_can_be_replaced() -> None:
    simulation = world()
    waypoints = (Point(8.5, 4.5), Point(8.5, 18.5), Point(4.5, 18.5))
    assert simulation.execute(MoveCommand(("builder",), waypoints[0])).accepted
    simulation.advance(2)
    for point in waypoints[1:]:
        command = MoveCommand(("builder",), point, queued=True)
        assert command_from_dict(command_to_dict(command)) == command
        assert simulation.execute(command).accepted
    loaded = restored(simulation)
    for _ in range(200):
        simulation.advance()
        loaded.advance()
    assert simulation.entities["builder"].position == waypoints[-1]
    completed = [
        event.details["position"]
        for event in simulation.events.events
        if event.subject_id == "builder" and event.event_type.value == "movement_completed"
    ]
    assert completed == [[p.x, p.y] for p in waypoints]
    assert simulation.export_state() == loaded.export_state()
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()
    assert simulation.execute(MoveCommand(("builder",), Point(20.5, 18.5), queued=True)).accepted
    assert simulation.execute(MoveCommand(("builder",), Point(24.5, 18.5), queued=True)).accepted
    pending = list(simulation.entities["builder"].order_queue)
    assert not simulation.execute(MoveCommand(("builder", "missing"), Point(10, 10))).accepted
    assert simulation.entities["builder"].order_queue == pending
    assert simulation.execute(StopCommand(("builder",))).accepted
    assert not simulation.entities["builder"].order_queue
    assert not simulation.entities["builder"].path


def test_remote_reclaim_save_override_single_refund_and_replay() -> None:
    simulation = world()
    command = ReclaimBuildingCommand(("builder", "builder2"), "factory")
    assert command_from_dict(command_to_dict(command)) == command
    before = simulation.resources["player"]
    assert simulation.execute(command).accepted
    simulation.advance(5)
    assert "factory" in simulation.entities
    loaded = restored(simulation)
    stopped = restored(simulation)
    assert stopped.execute(StopCommand(command.entity_ids)).accepted
    stopped.advance(100)
    assert "factory" in stopped.entities
    assert stopped.resources["player"] == before
    simulation.advance(100)
    loaded.advance(100)
    assert simulation.export_state() == loaded.export_state()
    assert "factory" not in simulation.entities
    assert simulation.resources["player"] == before + 280
    assert all(simulation.entities[key].reclaim_target_id is None for key in command.entity_ids)
    assert (
        sum(
            event.event_type is EventType.RESOURCE_CHANGED
            and event.details.get("reason") == "BUILDING_RECLAIMED"
            for event in simulation.events.events
        )
        == 1
    )
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


@pytest.mark.parametrize(
    ("ids", "target", "reason"),
    [
        (("a",), "factory", "ENTITY_NOT_BUILDER"),
        (("builder",), "unknown", "UNKNOWN_TARGET"),
        (("builder",), "a", "TARGET_NOT_BUILDING"),
    ],
)
def test_reclaim_validation_preserves_current_order(
    ids: tuple[str, ...], target: str, reason: str
) -> None:
    simulation = world()
    assert simulation.execute(MoveCommand(("builder",), Point(5.5, 7.5))).accepted
    path = list(simulation.entities["builder"].path)
    result = simulation.execute(ReclaimBuildingCommand(ids, target))
    assert not result.accepted and result.reason == reason
    assert simulation.entities["builder"].path == path


def test_single_cell_turrets_allow_adjacent_construction_and_restore_occupancy() -> None:
    simulation = world()
    for builder, x in (("builder", 4), ("builder2", 5)):
        assert simulation.execute(
            CreateConstructionCommand(builder, EntityKind.TURRET, Point(x, 5))
        ).accepted
    simulation.advance()
    loaded = restored(simulation)
    for current in (simulation, loaded):
        turrets = [
            entity for entity in current.entities.values() if entity.kind is EntityKind.TURRET
        ]
        assert {entity.occupied_cells for entity in turrets} == {
            frozenset({(4, 5)}),
            frozenset({(5, 5)}),
        }
        assert {entity.selection_position for entity in turrets} == {
            Point(4.5, 5.5),
            Point(5.5, 5.5),
        }


def test_reclaim_paid_scaffold_frees_occupancy_and_fails_its_job() -> None:
    simulation = world()
    before = simulation.resources["player"]
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.TURRET, Point(4, 5))
    )
    assert result.accepted
    simulation.advance()
    job = simulation.automations[result.automation_id or ""]
    assert isinstance(job.parameters, ConstructionParameters)
    site = job.parameters.site_entity_id
    assert site is not None and not simulation.entities[site].is_complete
    assert simulation.resources["player"] == before - 250
    assert simulation.execute(ReclaimBuildingCommand(("builder", "builder2"), site)).accepted
    simulation.advance(10)
    assert site not in simulation.entities
    assert job.status.terminal
    assert simulation.resources["player"] == before - 75
    assert not simulation._building_cells().intersection(
        footprint_cells(EntityKind.TURRET, Point(4, 5))
    )
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


@pytest.mark.parametrize("removal", ["external", "combat"])
def test_reclaim_removed_target_refunds_nothing_and_invalid_save_is_rejected(removal: str) -> None:
    simulation = world()
    builder = simulation.entities["builder"]
    builder.position = Point(8.5, 10.5)
    simulation.occupancy.move(builder.entity_id, builder.occupied_cells)
    assert simulation.execute(ReclaimBuildingCommand(("builder",), "factory")).accepted
    save = capture_save(simulation)
    save.state["entities"]["builder"]["reclaim_target_id"] = "a"
    with pytest.raises(PersistenceError, match="invalid reclaim target"):
        load_simulation_data({"schema": save.schema, "map": save.game_map, "state": save.state})
    before = simulation.resources["player"]
    simulation.advance(2)
    assert simulation.entities["factory"].health == 490
    if removal == "combat":
        source = simulation.entities["b"]
        source.owner_id = "enemy"
        target = simulation.entities["factory"]
        projectile = Projectile(
            "incoming",
            source.entity_id,
            target.entity_id,
            source.owner_id,
            source.kind,
            target.selection_position,
            target.selection_position,
            target.health,
            source.kind.profile.projectile_speed,
        )
        simulation.projectiles[projectile.projectile_id] = projectile
    else:
        simulation.remove_entity("factory")
    simulation.advance(80)
    assert simulation.resources["player"] == before
    assert simulation.entities["builder"].reclaim_target_id is None


def test_reclaim_respects_ownership_and_starting_building_has_zero_refund() -> None:
    simulation = world()
    building = simulation.entities["factory"]
    building.owner_id = "enemy"
    assert (
        simulation.execute(ReclaimBuildingCommand(("builder",), "factory")).reason
        == "TARGET_NOT_OWNED"
    )
    center = Entity("center", EntityKind.COMMAND_CENTER, "player", Point(4, 5), 700)
    simulation.entities[center.entity_id] = center
    simulation.occupancy.place(center.entity_id, center.occupied_cells)
    simulation._invalidate_navigation_cache()
    before = simulation.resources["player"]
    assert simulation.execute(ReclaimBuildingCommand(("builder",), "center")).accepted
    simulation.advance()
    assert center.health == 700 - EntityKind.BUILDER.profile.reclaim_rate
    assert simulation.resources["player"] == before
    simulation.advance(140)
    assert "center" not in simulation.entities and simulation.resources["player"] == before


def test_reclaim_drains_health_without_early_refund_and_restores_work_in_progress() -> None:
    simulation = world()
    target = simulation.entities["factory"]
    target.health = 160
    builder = simulation.entities["builder"]
    builder.position = Point(8.5, 10.5)
    simulation.occupancy.move(builder.entity_id, builder.occupied_cells)
    before = simulation.resources["player"]
    assert simulation.execute(ReclaimBuildingCommand(("builder",), "factory")).accepted
    simulation.advance(simulation.TICKS_PER_SECOND)
    assert target.health == 160 - simulation.TICKS_PER_SECOND * builder.kind.profile.reclaim_rate
    assert simulation.resources["player"] == before
    assert not builder.path and builder.move_target is None
    assert builder.position == Point(8.5, 10.5)
    app = AirtsApp(simulation)
    renderer = SoftwareRenderer()
    endpoints = (builder.selection_position, target.selection_position)
    assert tuple(reclaim_links(simulation)) == (endpoints,)
    paths = renderer._representative_path_points(app)
    assert paths == ((RECLAIM_COLOR, tuple(app.screen_point(p) for p in endpoints)),)
    with patch("airts.presentation.rendering.pygame.draw.lines") as lines:
        renderer.draw_entities(app, pygame.Surface(app.window_size))
    lines.assert_called_once_with(
        lines.call_args.args[0], RECLAIM_COLOR, False, paths[0][1], WORK_BEAM_WIDTH
    )
    loaded = restored(simulation)
    assert tuple(reclaim_links(loaded)) == (endpoints,)
    assert simulation.execute(StopCommand(("builder",))).accepted
    simulation.advance(30)
    assert target.health == 110 and simulation.resources["player"] == before
    assert not tuple(reclaim_links(simulation))
    loaded.advance(21)
    assert loaded.entities["factory"].health == 5
    assert loaded.resources["player"] == before
    loaded.advance()
    assert "factory" not in loaded.entities
    assert loaded.resources["player"] == before + 280
    assert not tuple(reclaim_links(loaded))
    refunds = [
        event
        for event in loaded.events.events
        if event.event_type is EventType.RESOURCE_CHANGED
        and event.details.get("reason") == "BUILDING_RECLAIMED"
    ]
    assert len(refunds) == 1 and refunds[0].details["builder_id"] == "builder"


def test_factory_produces_artillery_and_turret_is_stationary_armed_only_when_complete() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "weapons",
                "name": "Weapons",
                "width": 48,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "factory", "kind": "factory", "position": [2, 2]},
                    {"id": "gun", "kind": "turret", "position": [20, 10]},
                    {"id": "artillery", "kind": "artillery", "position": [18.5, 15.5]},
                    {
                        "id": "enemy",
                        "kind": "command_center",
                        "owner": "enemy",
                        "position": [26, 10],
                    },
                ],
            }
        )
    )
    gun = simulation.entities["gun"]
    gun.construction_progress = 0.5
    assert simulation.execute(CreateProductionCommand("factory", EntityKind.ARTILLERY, 1)).accepted
    simulation.advance(EntityKind.ARTILLERY.profile.production_ticks)
    assert not any(
        e.subject_id == "gun" and e.event_type.value == "projectile_launched"
        for e in simulation.events.events
    )
    assert len([e for e in simulation.entities.values() if e.kind is EntityKind.ARTILLERY]) == 2
    gun.construction_progress = None
    assert simulation.execute(AttackCommand(("gun",), "enemy")).accepted
    simulation.advance(60)
    assert gun.position == Point(20, 10) and not gun.pursue_target
    shots = [
        e.tick
        for e in simulation.events.events
        if e.subject_id == "artillery" and e.event_type.value == "projectile_launched"
    ]
    assert len(shots) >= 3 and all(b - a == 25 for a, b in zip(shots, shots[1:], strict=False))
    turret_shots = [
        e.tick
        for e in simulation.events.events
        if e.subject_id == "gun" and e.event_type.value == "projectile_launched"
    ]
    assert len(turret_shots) >= 8
    assert all(b - a == 6 for a, b in zip(turret_shots, turret_shots[1:], strict=False))
    assert simulation.entities["enemy"].health < EntityKind.COMMAND_CENTER.profile.max_health
    assert restored(simulation).snapshot() == simulation.snapshot()


def test_opponent_forecast_handles_fixed_turrets_and_slow_artillery() -> None:
    heavy = Entity("heavy", EntityKind.HEAVY_TANK, "ai", Point(20.5, 20.5), 160)
    turret = Entity("turret", EntityKind.TURRET, "player", Point(26, 20), 400)
    artillery = Entity("artillery", EntityKind.ARTILLERY, "ai", Point(20.5, 22.5), 90)
    assert force_strength([artillery]) < force_strength([heavy])
    assert isfinite(engagement_margin([heavy], [turret]))
    assert isfinite(engagement_margin([turret], [heavy]))
    turret.construction_progress = 0.5
    assert force_strength([turret]) == 0
    simulation = opponent_scenario(opening_army(), ((EntityKind.TURRET, 52, 55),))
    simulation.advance(40)
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()
    assert restored(simulation).snapshot() == simulation.snapshot()


def app_for_building_line() -> AirtsApp:
    app = AirtsApp(world(), RendererBackend.SOFTWARE)
    app.ui.font = pygame.font.Font(
        None, 18
    )  # Screen coordinates, rather than legacy local test pixels.
    app.selected_entities = {"builder", "builder2"}
    app.placement_kind = EntityKind.TURRET
    return app


def test_builder_reclaim_button_target_selection_and_right_cancel() -> None:
    pygame.font.init()
    app = app_for_building_line()
    app.ui.small_font = pygame.font.Font(None, 14)
    selection.draw_selection(app, pygame.Surface(app.window_size))
    button = next(rect for rect, action in app._command_buttons if action == "reclaim")
    app._handle_command_click(button.center)
    assert app.reclaim_mode and app.placement_kind is None
    before = len(app.simulation.command_history)
    app._handle_mouse_down(3, app.screen_point(Point(10, 20)))
    assert not app.reclaim_mode and len(app.simulation.command_history) == before
    app._handle_command_click(button.center)
    app._handle_mouse_down(1, app.screen_point(Point(14, 12)))
    assert not app.reclaim_mode
    assert all(
        app.simulation.entities[key].reclaim_target_id == "factory" for key in app.selected_entities
    )
    app.simulation.advance(100)
    assert "factory" not in app.simulation.entities


def test_shift_drag_previews_then_enqueues_spaced_buildings_and_skips_invalid_sites() -> None:
    pygame.font.init()
    app = app_for_building_line()
    start, end = app.screen_point(Point(8, 10)), app.screen_point(Point(20, 10))
    before = len(app.simulation.command_history)
    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        app._handle_mouse_down(1, start)
        app._handle_mouse_motion(end, (True, False, False))
    assert len(app.simulation.command_history) == before
    assert app.building_line_draft is not None
    before_preview = app.simulation.export_state()
    previews = app._construction_previews(None)
    valid_x = (8, 9, 10, 11, 16, 17, 18, 19, 20)
    assert [point for point, valid in previews if valid] == [Point(x, 10) for x in valid_x]
    assert [point for point, valid in previews if not valid] == [
        Point(x, 10) for x in (12, 13, 14, 15)
    ]
    app._draw_construction(pygame.Surface(app.window_size))
    assert app.simulation.export_state() == before_preview
    app._handle_mouse_up(1, end)
    jobs = [
        a
        for a in app.simulation.live_automations
        if isinstance(a.parameters, ConstructionParameters)
    ]
    assert [
        a.parameters.position
        for a in sorted(jobs, key=lambda a: int(a.automation_id.removeprefix("automation_")))
    ] == [Point(x, 10) for x in valid_x]
    assert "Skipped 4" in app.notice
    assert app.placement_kind is EntityKind.TURRET
    assert sum(a.reason_code == "BUILDERS_QUEUED" for a in jobs) == 8


@pytest.mark.parametrize("motion_cancel", [False, True])
def test_right_button_cancels_building_drag_without_commands(motion_cancel: bool) -> None:
    pygame.font.init()
    app = app_for_building_line()
    start, end = app.screen_point(Point(8, 20)), app.screen_point(Point(20, 20))
    before = app.simulation.export_state()
    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        app._handle_mouse_down(1, start)
    if motion_cancel:
        app._handle_mouse_motion(end, (True, False, True))
    else:
        app._handle_mouse_down(3, end)
    app._handle_mouse_up(1, end)
    assert app.simulation.export_state() == before
    assert app.building_line_draft is None and app.placement_kind is None


def test_repair_stops_on_range_entry_and_allocates_separate_destinations() -> None:
    simulation = world()
    for key in ("a", "b"):
        simulation.entities[key].health = 10
    result = simulation.execute(CreateRepairAndReturnCommand(("a", "b"), health_threshold=1))
    assert result.accepted
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, RepairParameters)
    simulation.advance()
    a, b = simulation.entities["a"], simulation.entities["b"]
    assert a.move_target is not None and b.move_target is not None
    assert (
        a.move_target.distance_to(b.move_target)
        >= a.kind.profile.collision_radius + b.kind.profile.collision_radius
    )
    # A moving unit already in range does not need to finish its perimeter path.
    old_goal = a.move_target
    point = Point(28, 13)
    a.position = point
    simulation.occupancy.move(a.entity_id, a.occupied_cells, frozenset(simulation.entities))
    simulation.advance()
    assert parameters.phases["a"] is RepairPhase.REPAIRING
    assert a.move_target is None and not a.path and a.position == point
    assert a.position != old_goal and a.state is UnitState.REPAIRING
    assert all(
        p.distance_to(b.position)
        >= a.kind.profile.collision_radius + b.kind.profile.collision_radius
        for p in service_points(simulation, a, simulation.entities["hub"])
    )
    loaded = restored(simulation)
    simulation.advance(180)
    loaded.advance(180)
    assert loaded.snapshot() == simulation.snapshot()
    assert a.health == a.kind.profile.max_health and b.health == b.kind.profile.max_health


def test_overlapping_repair_arrivals_seek_other_clear_slots() -> None:
    simulation = world()
    for key in ("a", "b"):
        entity = simulation.entities[key]
        entity.position = Point(28.5, 13.5)
        entity.health = 10
        simulation.occupancy.move(key, entity.occupied_cells, frozenset(simulation.entities))
    result = simulation.execute(CreateRepairAndReturnCommand(("a", "b"), health_threshold=1))
    assert result.accepted
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, RepairParameters)
    simulation.advance()
    assert parameters.phases == {"a": RepairPhase.TRAVELING, "b": RepairPhase.TRAVELING}
    assert simulation.entities["a"].move_target != simulation.entities["b"].move_target
    assert simulation.entities["a"].move_target != Point(28.5, 13.5)
    simulation.advance(160)
    assert all(
        simulation.entities[key].health == simulation.entities[key].kind.profile.max_health
        for key in ("a", "b")
    )


def test_repair_waits_for_capacity_then_recovers_when_slot_is_freed() -> None:
    simulation = world()
    simulation.remove_entity("factory")
    unit = simulation.entities["a"]
    unit.health = 10
    result = simulation.execute(CreateRepairAndReturnCommand(("a",), health_threshold=1))
    assert result.accepted
    # Fill every in-range cell with parked scouts after the order was accepted.
    for index, point in enumerate(service_points(simulation, unit, simulation.entities["hub"])):
        parked = Entity(f"parked_{index}", EntityKind.SCOUT, "player", point, 60)
        simulation.entities[parked.entity_id] = parked
        simulation.occupancy.place(
            parked.entity_id, parked.occupied_cells, frozenset(simulation.entities)
        )
    simulation.advance()
    job = simulation.automations[result.automation_id or ""]
    assert job.reason_code == "NO_REPAIR_SPACE" and not unit.path
    with simulation._batch_entity_removals():
        for key in tuple(simulation.entities):
            if key.startswith("parked_"):
                simulation.remove_entity(key)
    simulation.advance(120)
    assert unit.health == unit.kind.profile.max_health
