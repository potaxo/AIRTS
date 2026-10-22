"""Current production and upgrade feedback in world space and selection controls."""

from array import array
from unittest.mock import patch

import pygame
import pytest

from airts.contracts.commands import (
    CancelAutomationCommand,
    CancelUpgradeCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    PauseAutomationCommand,
    UpgradeEntityCommand,
)
from airts.presentation.app import AirtsApp
from airts.presentation.opengl_renderer import OpenGLRenderer
from airts.presentation.palette import PROGRESS_COLOR, PROGRESS_TRACK
from airts.presentation.progress import building_progress, current_production
from airts.presentation.rendering import SoftwareRenderer
from airts.presentation.ui.selection import draw_selection
from airts.presentation.visuals.catalog import APPEARANCES
from airts.presentation.visuals.gpu import ENTITY_FLOATS, pack_entities
from airts.simulation import Simulation
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


@pytest.fixture
def progress_app() -> AirtsApp:
    simulation = Simulation(
        load_map_data(
            {
                "id": "progress",
                "name": "Progress feedback",
                "width": 40,
                "height": 24,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "factory", "kind": "factory", "position": [5, 5]},
                    {"id": "center", "kind": "command_center", "position": [14, 5]},
                    {"id": "income", "kind": "matter_synthesizer", "position": [24, 5]},
                ],
            }
        )
    )
    simulation.resources["player"] = 10_000
    return AirtsApp(simulation)


def bar_rect(app: AirtsApp, entity_id: str) -> pygame.Rect:
    entity = app.simulation.entities[entity_id]
    center = app.screen_point(entity.selection_position)
    width = max(12, round(app.tile_size * 1.4))
    return pygame.Rect(
        center[0] - width // 2,
        center[1] + max(7, round(APPEARANCES[entity.kind].size[1] * app.tile_size / 2)) + 4,
        width,
        3,
    )


@pytest.mark.parametrize("entity_id", ["factory", "center", "income"])
@pytest.mark.parametrize("finish", [False, True], ids=["cancel", "complete"])
def test_unselected_building_progress_pixels_and_gpu_batch_lifecycle(
    progress_app: AirtsApp, entity_id: str, finish: bool
) -> None:
    app = progress_app
    world = app.simulation
    upgrading = entity_id == "income"
    result = world.execute(
        UpgradeEntityCommand(entity_id)
        if upgrading
        else CreateProductionCommand(entity_id, EntityKind.BUILDER, 1)
    )
    assert result.accepted
    entity = world.entities[entity_id]
    duration = (
        entity.upgrade.remaining_ticks if upgrading else EntityKind.BUILDER.profile.production_ticks
    )
    world.advance(duration // 2)
    fraction = (duration // 2) / duration
    before = world.export_state()
    assert not app.selected_entities
    surface = pygame.Surface(app.window_size)
    renderer = SoftwareRenderer()
    renderer.draw_entities(app, surface)
    bar = bar_rect(app, entity_id)
    assert tuple(surface.get_at((bar.left + 1, bar.centery))[:3]) == PROGRESS_COLOR
    assert tuple(surface.get_at((bar.right - 1, bar.centery))[:3]) == PROGRESS_TRACK
    assert bar.top > app.screen_point(entity.selection_position)[1]
    packed, *_ = pack_entities(app)
    values = array("f")
    values.frombytes(packed)
    offset = list(world.entities).index(entity_id) * ENTITY_FLOATS
    assert values[offset + 24] == pytest.approx(fraction)
    assert values[offset + 26] == 1  # GPU anchors progress below this building.
    assert world.export_state() == before
    if finish:
        world.advance(duration - duration // 2)
    else:
        assert world.execute(
            CancelUpgradeCommand(entity_id)
            if upgrading
            else CancelAutomationCommand(result.automation_id or "")
        ).accepted
    assert building_progress(world, entity) == ()
    surface.fill((0, 0, 0))
    renderer.draw_entities(app, surface)
    assert tuple(surface.get_at((bar.left + 1, bar.centery))[:3]) != PROGRESS_COLOR


def test_production_feedback_follows_assigned_item_through_preemption_pause_and_spawn_wait(
    progress_app: AirtsApp,
) -> None:
    world = progress_app.simulation
    factory = world.entities["factory"]
    loop = world.execute(CreateProductionCommand("factory", EntityKind.BUILDER, 1, continuous=True))
    assert loop.accepted
    world.advance(10)
    assert building_progress(world, factory) == (0.4,)
    batch = world.execute(
        CreateProductionBatchCommand("factory", ((EntityKind.SCOUT, 1), (EntityKind.LIGHT_TANK, 1)))
    )
    assert batch.accepted
    assert building_progress(world, factory) == (0.0,)
    world.advance(EntityKind.SCOUT.profile.production_ticks)
    current = current_production(world, "factory")
    assert current is not None and current.current_unit_kind is EntityKind.LIGHT_TANK
    world.advance(10)
    assert building_progress(world, factory) == (0.25,)
    assert world.execute(PauseAutomationCommand(batch.automation_id or "")).accepted
    world.advance(3)
    assert building_progress(world, factory) == (0.25,)
    assert world.execute(CancelAutomationCommand(batch.automation_id or "")).accepted
    assert building_progress(world, factory) == (0.4,)
    with patch("airts.systems.production.find_spawn_point", return_value=None):
        world.advance(EntityKind.BUILDER.profile.production_ticks)
    assert world.automations[loop.automation_id or ""].reason_code == "SPAWN_BLOCKED"
    assert building_progress(world, factory) == (1.0,)


def test_production_panel_blue_track_only_follows_current_kind(progress_app: AirtsApp) -> None:
    app = progress_app
    world = app.simulation
    assert world.execute(
        CreateProductionBatchCommand("factory", ((EntityKind.SCOUT, 1), (EntityKind.LIGHT_TANK, 1)))
    ).accepted
    world.advance(EntityKind.SCOUT.profile.production_ticks + 10)
    app.selected_entities = {"factory"}
    app._selection_changed()
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 16)
    surface = pygame.Surface(app.window_size)
    draw_selection(app, surface)
    for kind in (EntityKind.SCOUT, EntityKind.LIGHT_TANK):
        button = next(
            rect for rect, action in app._command_buttons if action == f"queue:{kind.value}"
        )
        point = (app.right_panel_rect.x + 18, button.bottom + 1)
        assert (tuple(surface.get_at(point)[:3]) == PROGRESS_COLOR) == (
            kind is EntityKind.LIGHT_TANK
        )


@pytest.mark.performance
def test_native_progress_bar_stays_below_building_with_fixed_thickness_through_zoom(
    progress_app: AirtsApp,
) -> None:
    app = progress_app
    assert app.simulation.execute(UpgradeEntityCommand("income")).accepted
    app.simulation.advance(215)
    for source in ("factory", "center"):
        assert app.simulation.execute(
            CreateProductionCommand(source, EntityKind.BUILDER, 1)
        ).accepted
    app.simulation.advance(10)
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 16)
    renderer = OpenGLRenderer.from_standalone_context()
    target = renderer.create_offscreen_target(app.window_size)
    try:
        original = None
        for zoom in (1.0, 0.2, 1.0):
            app.camera.zoom = zoom
            frame = renderer.render(app, app.window_size, framebuffer=target.framebuffer)
            renderer.finish()
            if original is None:
                original = frame.shape_buffer
            assert frame.shape_buffer is original
            image = pygame.image.frombytes(
                target.framebuffer.read(components=3), app.window_size, "RGB", True
            )
            for entity_id in ("factory", "center", "income"):
                entity = app.simulation.entities[entity_id]
                center = entity.selection_position
                cx = app.map_origin[0] + center.x * app.tile_size
                bottom = (
                    app.map_origin[1]
                    + center.y * app.tile_size
                    + max(7, APPEARANCES[entity.kind].size[1] * app.tile_size / 2)
                )
                half_width = max(6, 0.7 * app.tile_size)
                x = int(cx - half_width / 2)
                blue_rows = [
                    y
                    for y in range(round(bottom), round(bottom) + 12)
                    if (pixel := image.get_at((x, y))).b > pixel.g + 20 and pixel.b > pixel.r + 50
                ]
                assert len(blue_rows) == 3, (entity_id, zoom, blue_rows)
                assert blue_rows[0] >= bottom + 3
                # The outer rows are antialiased; the center has the exact blue/track colors.
                y = blue_rows[1]
                assert tuple(image.get_at((x, y))[:3]) == PROGRESS_COLOR
                assert tuple(image.get_at((int(cx + half_width / 2), y))[:3]) == PROGRESS_TRACK
    finally:
        target.release()
        renderer.release()
