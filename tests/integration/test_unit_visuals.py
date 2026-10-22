"""Visual facing and GPU staging never feed back into authoritative gameplay."""

from array import array
from math import cos, hypot, pi, sin, tau
from unittest.mock import patch

import pygame
import pytest

from airts.contracts.commands import (
    MoveCommand,
    RemoveEntityCommand,
    RepairBuildingCommand,
    StopCommand,
)
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.opengl_renderer import OpenGLFrameBuilder, OpenGLRenderer
from airts.presentation.palette import REPAIR_COLOR
from airts.presentation.rendering import SoftwareRenderer
from airts.presentation.visuals.assets import SOFTWARE_DIRECTIONS
from airts.presentation.visuals.facing import heading
from airts.presentation.visuals.gpu import ENTITY_FLOATS
from airts.simulation import Simulation
from airts.world.entities import Entity
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


def visual_app(*, enemy: bool = False) -> AirtsApp:
    entities: list[dict[str, object]] = [
        {"id": "tank", "kind": "light_tank", "position": [5.5, 5.5]},
        {"id": "turret", "kind": "turret", "position": [20, 5]},
    ]
    if enemy:
        entities.extend(
            [
                {"id": "enemy", "kind": "builder", "owner": "enemy", "position": [10.5, 5.5]},
                {
                    "id": "turret_enemy",
                    "kind": "builder",
                    "owner": "enemy",
                    "position": [27.5, 5.5],
                },
            ]
        )
    return AirtsApp(
        Simulation(
            load_map_data(
                {
                    "id": "visual-facing",
                    "name": "Visual facing",
                    "width": 36,
                    "height": 24,
                    "terrain": {"default": "grass"},
                    "entities": entities,
                }
            )
        )
    )


def test_motion_facing_stays_after_stop_and_passive_displacement() -> None:
    app = visual_app()
    assert app.simulation.execute(MoveCommand(("tank",), Point(15.5, 5.5))).accepted
    for _ in range(4):
        app._advance_presentation_tick()
        app.facing.sync(app.simulation)
    assert app.facing.pose("tank").body == pytest.approx(pi / 2)
    app.simulation.execute(StopCommand(("tank",)))
    app.facing.sync(app.simulation)
    unit = app.simulation.entities["tank"]
    unit.position = Point(unit.position.x, unit.position.y + 0.2)
    app.simulation.occupancy.move("tank", unit.occupied_cells, frozenset(app.simulation.entities))
    app._advance_presentation_tick()
    before = app.simulation.export_state()
    app.facing.sync(app.simulation)
    assert app.facing.pose("tank").body == pytest.approx(pi / 2)
    assert app.simulation.export_state() == before
    app._reset_presentation_history()
    assert app.facing.pose("tank").body == 0  # Reconstructed presentation state, not a save field.


def test_vehicle_body_and_fixed_turret_weapons_aim_independently() -> None:
    app = visual_app(enemy=True)
    app.simulation.execute(MoveCommand(("tank",), Point(5.5, 12.5)))
    for _ in range(6):
        app._advance_presentation_tick()
        app.facing.sync(app.simulation)
    tank = app.simulation.entities["tank"]
    pose = app.facing.pose("tank")
    assert abs(pose.body) == pytest.approx(pi)
    assert pose.weapon == pytest.approx(
        heading(tank.position, app.simulation.entities["enemy"].position)
    )
    assert abs(pose.body - pose.weapon) > 1
    fixed = app.facing.pose("turret")
    assert fixed.body == 0 and fixed.weapon > 0
    assert app.simulation.entities["turret"].position == Point(20, 5)
    frame = OpenGLFrameBuilder().build(app, app.window_size)
    values = array("f")
    values.frombytes(frame.entity_buffer)
    records = [values[i : i + ENTITY_FLOATS] for i in range(0, len(values), ENTITY_FLOATS)]
    assert len(records) == 4
    assert records[0][15] == pytest.approx(pose.body)
    assert records[0][17] == pytest.approx(pose.weapon)
    assert records[1][15] == 0 and records[1][17] > 0
    assert all(record[13] >= 0 for record in records[:2])  # GPU expands both aimed weapons.


def working_builder_app(position: Point | None = None) -> AirtsApp:
    position = position if position is not None else Point(8.5, 14)
    app = AirtsApp(
        Simulation(
            load_map_data(
                {
                    "id": "working-facing",
                    "name": "Working facing",
                    "width": 36,
                    "height": 28,
                    "terrain": {"default": "grass"},
                    "entities": [
                        {"id": "builder", "kind": "builder", "position": [position.x, position.y]},
                        {"id": "factory", "kind": "factory", "position": [12, 12]},
                    ],
                }
            )
        )
    )
    app.simulation.entities["factory"].health = 100
    assert app.simulation.execute(RepairBuildingCommand(("builder",), "factory")).accepted
    app._advance_presentation_tick()
    app.facing.sync(app.simulation)
    return app


@pytest.mark.parametrize(
    "position", [Point(8.5, 14), Point(19.5, 14), Point(14, 8.5), Point(14, 19.5)]
)
def test_working_builder_turns_toward_building_and_stop_retains_facing(position: Point) -> None:
    app = working_builder_app(position)
    simulation = app.simulation
    worker, target = simulation.entities["builder"], simulation.entities["factory"]
    for _ in range(5):
        previous = app.facing.pose("builder").body
        app._advance_presentation_tick()
        before = simulation.export_state()
        app.facing.sync(simulation)
        angle = app.facing.pose("builder").body
        assert abs((angle - previous + pi) % tau - pi) <= tau * simulation.TICK_SECONDS + 1e-9
        assert simulation.export_state() == before
    desired = heading(worker.selection_position, target.selection_position)
    assert sin(angle) == pytest.approx(sin(desired))
    assert cos(angle) == pytest.approx(cos(desired))
    assert worker.position == position  # A presentation turn never moves the repairer.
    app._reset_presentation_history()
    assert app.facing.pose("builder").body == pytest.approx(desired)
    assert simulation.execute(StopCommand(("builder",))).accepted
    for _ in range(3):
        app._advance_presentation_tick()
        app.facing.sync(simulation)
    assert not app.facing.work_links
    assert app.facing.pose("builder").body == pytest.approx(desired)


@pytest.mark.parametrize("end", ["stop", "complete", "removed"])
def test_repair_ending_mid_turn_clears_beam_and_retains_current_heading(end: str) -> None:
    app = working_builder_app()
    angle = app.facing.pose("builder").body
    assert 0 < angle < pi / 2
    if end == "stop":
        app.simulation.execute(StopCommand(("builder",)))
    elif end == "complete":
        target = app.simulation.entities["factory"]
        target.health = target.profile.max_health
    else:
        app.simulation.remove_entity("factory")
    app._advance_presentation_tick()
    app.facing.sync(app.simulation)
    assert not app.facing.work_links
    assert app.facing.pose("builder").body == angle


@pytest.mark.parametrize("tile", [2.0, 24.0])
@pytest.mark.parametrize("angle", [0, pi / 2, pi, -pi / 2, 0.41])
def test_software_work_beam_starts_at_rotated_claw_including_minimum_sprite_size(
    tile: float, angle: float
) -> None:
    app = working_builder_app()
    app.camera.zoom = tile / app.tile_size
    app.facing.pose("builder").body = angle
    before = app.simulation.export_state()
    paths = SoftwareRenderer()._representative_path_points(app)
    assert len(paths) == 1
    color, (start, end) = paths[0]
    center = app.screen_point(app.simulation.entities["builder"].position)
    size = max(14, round(1.35 * tile))
    displayed = round(angle / tau * SOFTWARE_DIRECTIONS) * tau / SOFTWARE_DIRECTIONS
    x, y = size / 8, -size * 3 / 8
    assert start == (
        center[0] + round(cos(displayed) * x - sin(displayed) * y),
        center[1] + round(sin(displayed) * x + cos(displayed) * y),
    )
    assert start != center
    assert end == app.screen_point(app.simulation.entities["factory"].selection_position)
    assert color == REPAIR_COLOR
    assert app.simulation.export_state() == before


@pytest.mark.performance
def test_native_work_beam_follows_claw_during_interpolation_and_zoom_without_uploads() -> None:
    app = working_builder_app()
    app.resize_layout((1280, 720))
    pose = app.facing.pose("builder")
    pose.previous_body, pose.body = 0, pi / 2
    worker = app.simulation.entities["builder"]
    app._previous_entity_positions["builder"] = Point(worker.position.x - 0.5, worker.position.y)
    renderer = OpenGLRenderer.from_standalone_context()
    target = renderer.create_offscreen_target(app.window_size)
    feedback = renderer._feedback
    assert feedback is not None
    before = app.simulation.export_state()
    try:
        with patch.object(feedback, "_build", wraps=feedback._build) as build:
            for tile in (4.0, 32.0):
                app.camera.zoom *= tile / app.tile_size
                for alpha in (0, 0.5, 1):
                    app.render_alpha = alpha
                    target.framebuffer.use()
                    renderer._context.viewport = (0, 0, *app.window_size)
                    renderer._context.clear(0, 0, 0, 1)
                    feedback.draw(app, app.window_size)
                    renderer.finish()
                    angle = alpha * pi / 2
                    size = max(14, 1.35 * tile)
                    x, y = size / 8, -size * 3 / 8
                    ox, oy = app.map_origin
                    cx = ox + (worker.position.x - 0.5 * (1 - alpha)) * tile
                    cy = oy + worker.position.y * tile
                    port = (
                        cx + cos(angle) * x - sin(angle) * y,
                        cy + sin(angle) * x + cos(angle) * y,
                    )
                    end = app.screen_point(app.simulation.entities["factory"].selection_position)
                    dx, dy = end[0] - port[0], end[1] - port[1]
                    length = hypot(dx, dy)
                    # Sample inside each side of the cap, avoiding boundary pixel rounding.
                    for distance, expected in ((2, bytes(REPAIR_COLOR)), (-2, bytes(3))):
                        px, py = port[0] + distance * dx / length, port[1] + distance * dy / length
                        pixel = target.read_pixel((int(px), app.window_size[1] - 1 - int(py)))
                        assert pixel[:3] == expected, (tile, alpha, distance, port)
                    assert target.read_pixel((int(cx), app.window_size[1] - 1 - int(cy)))[
                        :3
                    ] == bytes(3)
            assert build.call_count == 1
        assert app.simulation.export_state() == before
    finally:
        target.release()
        renderer.release()


@pytest.mark.parametrize("alpha", [0, 0.4, 1])
def test_tick_boundary_reuse_is_single_call_and_interaction_is_immediate(alpha: float) -> None:
    app = visual_app()
    app.simulation.execute(MoveCommand(("tank",), Point(15.5, 5.5)))
    builder = OpenGLFrameBuilder()
    old = builder.build(app, app.window_size)
    app._advance_presentation_tick()
    app.render_alpha = alpha
    assert builder.staged_frame(app, app.window_size) is old
    assert builder.staged_frame(app, app.window_size) is None
    new = builder.build(app, app.window_size)
    assert new.tick == app.simulation.tick and new.entity_buffer is not old.entity_buffer
    app._advance_presentation_tick()
    app.selected_entities = {"tank"}
    assert builder.staged_frame(app, app.window_size) is None
    builder.build(app, app.window_size)
    app._advance_presentation_tick()
    app.simulation.remove_entity("turret")
    # Explicit commands never defer, even when they remove a building.
    assert builder.staged_frame(app, app.window_size) is None


def test_scene_staging_keeps_all_survivors_and_publishes_births_immediately() -> None:
    app = visual_app()
    app.selected_entities = {"tank", "turret"}
    builder = OpenGLFrameBuilder()
    old = builder.build(app, app.window_size)
    app._advance_presentation_tick()
    app.simulation._remove_entity(RemoveEntityCommand("turret", "COMBAT_DESTROYED"))
    app._prune_removed_entities()
    assert app.selected_entities == {"tank"}
    assert builder.staged_frame(app, app.window_size) is old
    latest = builder.build(app, app.window_size)
    assert latest.unit_count == 1 and latest.building_count == 0
    app._advance_presentation_tick()
    newborn = Entity("newborn", EntityKind.BUILDER, "player", Point(10.5, 10.5), 80)
    app.simulation.entities[newborn.entity_id] = newborn
    app.simulation.occupancy.place(newborn.entity_id, newborn.occupied_cells)
    app.inspected_entity_id = "tank"
    assert builder.staged_frame(app, app.window_size) is None
    current = builder.build(app, app.window_size)
    assert current.unit_count == 2


@pytest.mark.performance
def test_native_sprite_turn_interpolation_changes_pixels_without_repacking() -> None:
    """Hardware rasterization checks the texture atlas and continuous rotation."""
    pygame.font.init()
    app = visual_app()
    app.resize_layout((1280, 720))
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 16)
    pose = app.facing.pose("tank")
    pose.previous_body = pose.previous_weapon = 0
    pose.body = pose.weapon = pi / 2
    renderer = OpenGLRenderer.from_standalone_context()
    target = renderer.create_offscreen_target(app.window_size)
    before = app.simulation.export_state()
    try:
        images = []
        frames = []
        for alpha in (0, 0.5, 1):
            app.render_alpha = alpha
            frames.append(renderer.render(app, app.window_size, framebuffer=target.framebuffer))
            renderer.finish()
            images.append(target.framebuffer.read(components=4))
        assert frames[0] is frames[1] is frames[2]
        assert len(set(images)) == 3
        assert app.simulation.export_state() == before
        assert renderer._sprite_texture is not None
    finally:
        target.release()
        renderer.release()
    assert renderer._sprite_texture is None
