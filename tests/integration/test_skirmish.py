"""Match behavior on the original 64-cell map, without changing existing fixtures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pygame
import pytest

from airts.adapters.persistence import (
    MATCH_SAVE_SCHEMA,
    PersistenceError,
    load_simulation,
    load_simulation_data,
    save_simulation,
)
from airts.adapters.replay import capture_replay, load_replay_data, run_replay
from airts.contracts.automations import ConstructionParameters
from airts.contracts.commands import (
    AttackCommand,
    CreateConstructionCommand,
    CreateProductionCommand,
    MoveCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.ui import automations
from airts.simulation import Simulation
from airts.systems.skirmish import owner_status
from airts.world.map import load_example_map, load_map_data
from airts.world.units.catalog import EntityKind


def match_simulation() -> Simulation:
    data = load_example_map().to_dict()
    entities = [
        {
            "id": spec.entity_id,
            "kind": spec.kind.value,
            "owner": "ai_b" if spec.owner_id == "enemy" else spec.owner_id,
            "position": [spec.position.x, spec.position.y],
        }
        for spec in load_example_map().entities
    ]
    entities.extend(
        [
            {"id": "builder", "kind": "builder", "owner": "player", "position": [24.5, 46.5]},
            {"id": "enemy_factory", "kind": "factory", "owner": "ai_b", "position": [48, 48]},
            {"id": "enemy_builder", "kind": "builder", "owner": "ai_b", "position": [45.5, 48.5]},
            {"id": "ally_factory", "kind": "factory", "owner": "ai_c", "position": [42, 50]},
            {"id": "ally_scout", "kind": "scout", "owner": "ai_c", "position": [26.5, 28.5]},
        ]
    )
    data["entities"] = entities
    data["resource_sites"] = [[20, 49], [24, 49], [44, 8]]
    data["match_slots"] = [
        {"owner": "player", "controller": "player", "team": 1},
        {"owner": "ai_b", "controller": "ai", "team": 2},
        {"owner": "ai_c", "controller": "ai", "team": 1},
    ]
    return Simulation(load_map_data(data))


def test_extractor_requires_free_site_and_survives_save_continuation(tmp_path: Path) -> None:
    simulation = match_simulation()
    rejected = simulation.execute(
        CreateConstructionCommand(
            "builder",
            EntityKind.RESOURCE_EXTRACTOR,
            Point(25, 46),
        )
    )
    assert not rejected.accepted
    assert rejected.reason == "RESOURCE_SITE_REQUIRED"
    command = CreateConstructionCommand("builder", EntityKind.RESOURCE_EXTRACTOR, Point(24, 49))
    assert simulation.execute(command).accepted
    assert simulation.execute(command).accepted  # An unstarted blueprint does not reserve a site.
    simulation.advance(5)
    path = tmp_path / "match.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    simulation.advance(60)
    restored.advance(60)
    assert simulation.export_state() == restored.export_state()
    extractor = next(
        entity
        for entity in simulation.entities.values()
        if entity.kind is EntityKind.RESOURCE_EXTRACTOR and entity.position == Point(24, 49)
    )
    simulation.remove_entity(extractor.entity_id)
    assert simulation.execute(command).accepted


def test_eliminated_player_keeps_army_until_team_result_and_out_requires_all_assets() -> None:
    simulation = match_simulation()
    simulation.remove_entity("factory_01")
    simulation.remove_entity("builder")
    simulation.remove_entity("command_01")
    simulation.advance()
    assert owner_status(simulation, "player") == "eliminated"
    assert simulation.match_result is None
    assert simulation.execute(MoveCommand(("tank_01",), Point(18, 28))).accepted
    simulation.remove_entity("enemy_factory")
    simulation.remove_entity("enemy_builder")
    simulation.remove_entity("enemy_command_01")
    simulation.advance()
    assert simulation.match_result is None
    assert "enemy_tank_01" in simulation.entities
    assert owner_status(simulation, "ai_b") == "eliminated"
    for entity in tuple(simulation.entities.values()):
        if entity.owner_id == "ai_b":
            simulation.remove_entity(entity.entity_id)
    assert owner_status(simulation, "ai_b") == "out"
    simulation.advance()
    assert simulation.match_result == 1
    tick = simulation.tick
    simulation.advance(100)
    assert simulation.tick == tick


def test_surviving_armies_continue_after_all_production_is_destroyed() -> None:
    simulation = match_simulation()
    for entity in tuple(simulation.entities.values()):
        if entity.kind in {EntityKind.FACTORY, EntityKind.BUILDER, EntityKind.COMMAND_CENTER}:
            simulation.remove_entity(entity.entity_id)
    simulation.advance()
    assert simulation.match_result is None
    for entity in tuple(simulation.entities.values()):
        simulation.remove_entity(entity.entity_id)
    simulation.advance()
    assert simulation.match_result == 0


def test_unfinished_factory_remains_an_asset_without_preventing_elimination() -> None:
    simulation = match_simulation()
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.FACTORY, Point(24, 49))
    )
    assert result.accepted
    simulation.advance()
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters)
    assert parameters.site_entity_id is not None
    simulation.remove_entity("factory_01")
    simulation.remove_entity("builder")
    simulation.remove_entity("command_01")
    assert owner_status(simulation, "player") == "eliminated"
    assert parameters.site_entity_id in simulation.entities


def test_teams_prohibit_manual_and_automatic_friendly_fire() -> None:
    simulation = match_simulation()
    assert not simulation.execute(AttackCommand(("scout_02",), "ally_scout")).accepted
    # The ally is close enough to be shot if automatic targeting ignores the team.
    assert simulation.entities["tank_01"].position.distance_to(Point(26.5, 28.5)) > 6
    simulation.entities["ally_scout"].position = Point(13.5, 29.5)
    simulation.occupancy.move("ally_scout", simulation.entities["ally_scout"].occupied_cells)
    simulation.advance(15)
    assert simulation.entities["ally_scout"].health == EntityKind.SCOUT.profile.max_health
    assert all(
        projectile.target_entity_id != "ally_scout"
        for projectile in simulation.projectiles.values()
        if projectile.owner_id == "player"
    )


def test_ai_produces_expands_attacks_and_replays_without_double_commands(tmp_path: Path) -> None:
    simulation = match_simulation()
    simulation.advance(80)
    initial_ids = {spec.entity_id for spec in simulation.game_map.entities}
    assert any(
        entity.entity_id not in initial_ids and entity.owner_id == "ai_b"
        for entity in simulation.entities.values()
    )
    assert any(
        automation.owner_id == "ai_b" and automation.title == "AI expansion"
        for automation in simulation.automations.values()
    )
    # This contested opening may have retreated by tick 80; verify that an
    # attack was actually issued rather than requiring suicidal ongoing pursuit.
    assert any(
        event.event_type is EventType.COMMAND_ACCEPTED and event.details.get("command") == "attack"
        for event in simulation.events.events
    )
    assert simulation.command_history == ()
    data = load_replay_data(capture_replay(simulation).to_dict())
    assert run_replay(data).snapshot() == simulation.snapshot()
    path = tmp_path / "ai.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    simulation.advance(40)
    restored.advance(40)
    assert simulation.export_state() == restored.export_state()


def test_zoom_clamps_and_preserves_cursor_world_point_and_panel_wheel() -> None:
    app = AirtsApp(match_simulation())
    pointer = (app.canvas_rect.centerx + 70, app.canvas_rect.centery - 40)
    world = app._map_point(pointer)
    app.zoom_camera(3, pointer)
    assert app._map_point(pointer).distance_to(world) <= 1 / app.tile_size
    app.zoom_camera(100, pointer)
    assert app.tile_size == pytest.approx(32)
    app.resize_layout((3840, 2160))
    assert app.tile_size == pytest.approx(32)
    app.resize_layout(app.WINDOW_SIZE)
    app.zoom_camera(100, app.canvas_rect.center)
    app.pan_camera(1e9, -1e9)
    assert app.map_origin[0] <= app.canvas_rect.left
    assert app.map_origin[1] + app.map_pixel_size[1] >= app.canvas_rect.bottom
    app.zoom_camera(-100, pointer)
    assert app.camera.zoom == 1
    assert app.camera.offset == Point(0, 0)
    with patch("pygame.mouse.get_pos", return_value=app.left_panel_rect.center):
        app._handle_event(pygame.event.Event(pygame.MOUSEWHEEL, {"y": 3}))
    assert app.camera.zoom == 1


def test_opening_cover_configures_then_locks_match_until_restart() -> None:
    from airts.presentation.lobby import Lobby

    app = AirtsApp(match_simulation(), show_lobby=True)
    assert app.lobby is not None
    pygame.font.init()
    try:
        app.ui.font = pygame.font.Font(None, 24)
        app.ui.small_font = pygame.font.Font(None, 19)
        screen = pygame.Surface(app.WINDOW_SIZE)
        app._draw(screen)
        cover = app.lobby
        app._handle_event(
            pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": cover.buttons[0][0].center}
            )
        )
        assert cover.page == "setup"
        app._draw(screen)
        rectangle = next(
            rect for rect, action, index in cover.buttons if action == "controller" and index == 1
        )
        cover.click(app, rectangle.center)
        assert sum(role == "player" for role, _ in cover.setup) == 1
        assert cover.setup[1][0] == "player"
        app._draw(screen)
        start = next(rect for rect, action, _ in cover.buttons if action == "start")
        # Keep the test simulation on the original map, even when starting through the lobby.
        with patch(
            "airts.presentation.app.create_skirmish_map", return_value=match_simulation().game_map
        ):
            app._handle_event(
                pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": start.center})
            )
        assert app.lobby is None
        assert app.simulation.game_map.width == 64
        app._handle_key(pygame.K_F2)
        assert isinstance(app.lobby, Lobby)
        assert app.lobby.page == "cover"
    finally:
        pygame.font.quit()


def test_ai_replaces_builder_even_when_factory_has_continuous_army_loop() -> None:
    simulation = match_simulation()
    simulation.advance(25)
    assert simulation.continuous_production("enemy_factory") is not None
    simulation.remove_entity("enemy_builder")
    simulation.remove_entity("enemy_command_01")
    # Allow the existing finite scout order, the AI decision interval and the replacement.
    simulation.advance(
        EntityKind.SCOUT.profile.production_ticks
        + EntityKind.BUILDER.profile.production_ticks
        + Simulation.TICKS_PER_SECOND
    )
    assert any(
        entity.owner_id == "ai_b" and entity.kind is EntityKind.BUILDER
        for entity in simulation.entities.values()
    )


def test_automation_panel_excludes_enemy_and_allied_ai_and_clears_ai_inspection() -> None:
    simulation = match_simulation()
    result = simulation.execute(
        CreateProductionCommand(
            "factory_01",
            EntityKind.LIGHT_TANK,
            1,
            continuous=True,
        )
    )
    simulation.advance(45)
    assert any(automation.owner_id == "ai_b" for automation in simulation.live_automations)
    assert any(automation.owner_id == "ai_c" for automation in simulation.live_automations)
    app = AirtsApp(simulation)
    rows = automations.panel_rows(app)
    assert [automation.automation_id for automation in rows] == [result.automation_id]
    app.automation_panel.selected_id = next(
        automation.automation_id
        for automation in simulation.live_automations
        if automation.owner_id == "ai_b"
    )
    app._prune_removed_entities()
    assert app.automation_panel.selected_id is None
    automations.scroll(app, 100, visible_rows=1, total_rows=len(rows))
    assert app.automation_panel.scroll == 0


def test_wasd_moves_camera_continuously_without_unit_commands_and_respects_edges() -> None:
    app = AirtsApp(match_simulation())
    app.zoom_camera(6, app.canvas_rect.center)
    history = app.simulation.command_history
    app._handle_key(pygame.K_w)
    app._handle_key(pygame.K_a)
    app._move_camera_from_keys(0.1)
    first = app.camera.offset
    assert first.x > 0 and first.x == pytest.approx(first.y)
    app._move_camera_from_keys(0.1)
    assert app.camera.offset.x == pytest.approx(2 * first.x)
    app._handle_event(pygame.event.Event(pygame.KEYUP, {"key": pygame.K_w}))
    app._handle_event(pygame.event.Event(pygame.KEYUP, {"key": pygame.K_a}))
    released = app.camera.offset
    app._move_camera_from_keys(1)
    assert app.camera.offset == released
    app._handle_key(pygame.K_d)
    app._handle_key(pygame.K_s)
    app._move_camera_from_keys(100)
    origin = app.map_origin
    assert origin[0] + app.map_pixel_size[0] >= app.canvas_rect.right
    assert origin[1] + app.map_pixel_size[1] >= app.canvas_rect.bottom
    app._handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST))
    released = app.camera.offset
    app._move_camera_from_keys(1)
    assert app.camera.offset == released
    assert app.simulation.command_history == history
    with (
        patch.object(app, "_create_patrol") as patrol,
        patch.object(app, "_create_defend") as defend,
        patch.object(app, "_stop_selected") as stop,
    ):
        app._handle_key(pygame.K_t)
        app._handle_key(pygame.K_y)
        app._handle_key(pygame.K_x)
        patrol.assert_called_once_with()
        defend.assert_called_once_with()
        stop.assert_called_once_with()


def test_software_zoom_reuses_cell_terrain_and_scales_only_visible_crop() -> None:
    app = AirtsApp(match_simulation())
    screen = pygame.Surface(app.WINDOW_SIZE)
    screen.set_clip(app.canvas_rect)
    renderer = app._software_renderer
    renderer.draw_map(app, screen)
    terrain = renderer._map_surface
    assert terrain is not None and terrain.get_size() == (64, 64)
    for steps in (8, -1, 3):
        app.zoom_camera(steps, app.canvas_rect.center)
        app.pan_camera(40, -30)
        renderer.draw_map(app, screen)
        assert renderer._map_surface is terrain
        scaled = renderer._scaled_map_surface
        assert scaled is not None
        assert scaled.get_width() <= app.canvas_rect.width + 2 * app.tile_size + 1
        assert scaled.get_height() <= app.canvas_rect.height + 2 * app.tile_size + 1


def test_save_rejects_wrong_match_result(tmp_path: Path) -> None:
    import json

    simulation = match_simulation()
    simulation.advance()
    path = tmp_path / "invalid.json"
    save_simulation(simulation, path)
    document = json.loads(path.read_text())
    document["state"]["match_result"] = True
    path.write_text(json.dumps(document))
    with pytest.raises(PersistenceError, match="match_result"):
        load_simulation(path)


def test_command_center_production_survives_current_save() -> None:
    simulation = match_simulation()
    for entity in tuple(simulation.entities.values()):
        if entity.owner_id in {"player", "ai_c"} and entity.kind in {
            EntityKind.FACTORY,
            EntityKind.BUILDER,
        }:
            simulation.remove_entity(entity.entity_id)
    simulation.advance()
    assert owner_status(simulation, "player") == "active"
    assert simulation.match_result is None
    state = simulation.export_state()
    document = {
        "schema": MATCH_SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": state,
    }
    restored = load_simulation_data(document)
    assert restored.match_result is None
    assert restored.opponents == simulation.opponents
    assert restored.execute(CreateProductionCommand("command_01", EntityKind.BUILDER, 1)).accepted
    restored.advance(EntityKind.BUILDER.profile.production_ticks)
    assert any(
        entity.kind is EntityKind.BUILDER and entity.owner_id == "player"
        for entity in restored.entities.values()
    )
