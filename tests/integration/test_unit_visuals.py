"""Visual facing and GPU staging never feed back into authoritative gameplay."""

from array import array
from math import pi

import pygame
import pytest

from airts.contracts.commands import MoveCommand, RemoveEntityCommand, StopCommand
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.gpu_shapes import SHAPE_FLOATS
from airts.presentation.opengl_renderer import OpenGLFrameBuilder, OpenGLRenderer
from airts.presentation.visuals.facing import heading
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
    values.frombytes(frame.shape_buffer)
    images = [
        values[i : i + SHAPE_FLOATS]
        for i in range(0, len(values), SHAPE_FLOATS)
        if values[i + 18] == 3
    ]
    assert len(images) == 6  # Two separately aimed weapons, plus two builder bodies.


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
    assert new.tick == app.simulation.tick and new.shape_buffer is not old.shape_buffer
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
