"""GPU world feedback visibility, geometry and camera cache regressions."""

from array import array
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pygame
import pytest

from airts.contracts.commands import (
    CreateConstructionCommand,
    ReclaimBuildingCommand,
    RepairBuildingCommand,
)
from airts.contracts.spatial import Point, PolygonRegion
from airts.presentation.app import AirtsApp
from airts.presentation.build_placement import BuildingLineDraft
from airts.presentation.gpu_feedback import GPUFeedback, _triangles
from airts.presentation.palette import RECLAIM_COLOR, REPAIR_COLOR, WORK_BEAM_WIDTH
from airts.presentation.rendering import reclaim_links, work_links
from airts.simulation import Simulation
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


def feedback_scene() -> AirtsApp:
    game_map = load_map_data(
        {
            "id": "feedback",
            "name": "Feedback",
            "width": 64,
            "height": 64,
            "terrain": {"default": "grass"},
            "entities": [
                {"id": "builder", "kind": "builder", "position": [2.5, 2.5]},
                {
                    "id": "enemy_builder",
                    "kind": "builder",
                    "owner": "enemy",
                    "position": [20.5, 2.5],
                },
                {"id": "enemy_factory", "kind": "factory", "owner": "enemy", "position": [16, 16]},
            ],
        }
    )
    simulation = Simulation(replace(game_map, resource_sites=(Point(2, 18),)))
    for owner, builder, point in [
        ("player", "builder", Point(6, 10)),
        ("enemy", "enemy_builder", Point(18, 10)),
    ]:
        result = simulation.execute(
            CreateConstructionCommand(builder, EntityKind.REPAIR_HUB, point, owner_id=owner)
        )
        assert result.accepted
    simulation.entities["enemy_factory"].construction_progress = 0.25
    return AirtsApp(simulation)


def mocked_feedback() -> GPUFeedback:
    context = MagicMock()

    def buffer(*args: object, **kwargs: object) -> MagicMock:
        result = MagicMock()
        result.size = 4096
        return result

    context.buffer.side_effect = buffer

    def program(**kwargs: object) -> MagicMock:
        result = MagicMock()
        uniforms: dict[str, SimpleNamespace] = {}
        result.__getitem__.side_effect = lambda name: uniforms.setdefault(
            name, SimpleNamespace(value=None)
        )
        return result

    context.program.side_effect = program
    module = SimpleNamespace(LINEAR=1, TRIANGLES=2, TRIANGLE_STRIP=3)
    return GPUFeedback(module, context)


def test_gpu_selection_preview_remains_visible_when_pointer_enters_command_bar() -> None:
    app = feedback_scene()
    app.drag_start = Point(1, 1)
    end = (app.command_bar_rect.right - 1, app.command_bar_rect.centery)
    feedback = mocked_feedback()
    try:
        with (
            patch("airts.presentation.gpu_feedback.pygame.display.get_init", return_value=True),
            patch("airts.presentation.gpu_feedback.pygame.mouse.get_pos", return_value=end),
        ):
            feedback.draw(app, app.window_size)
        shapes = array("f")
        shapes.frombytes(feedback.shapes.write.call_args.args[0])
        selection = shapes[-18:]
        pointer = app._map_point(end)
        assert tuple(selection[:4]) == pytest.approx(
            ((1 + pointer.x) / 2, (1 + pointer.y) / 2, (pointer.x - 1) / 2, (pointer.y - 1) / 2)
        )
        assert tuple(selection[14:18]) == pytest.approx((245 / 255, 245 / 255, 245 / 255, 1))
    finally:
        feedback.release()


def test_gpu_packs_thick_builder_and_hub_work_beams_without_changing_state() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "work_beams",
                "name": "Work beams",
                "width": 64,
                "height": 64,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "builder", "kind": "builder", "position": [2.5, 4.5]},
                    {"id": "repairer", "kind": "builder", "position": [16.5, 5.5]},
                    {"id": "factory", "kind": "factory", "position": [10, 4]},
                    {"id": "hub", "kind": "repair_hub", "position": [20, 4]},
                    {"id": "tank", "kind": "light_tank", "position": [18.5, 5.5]},
                ],
            }
        )
    )
    simulation.entities["tank"].health = 20
    simulation.entities["factory"].health = 200
    assert simulation.execute(
        CreateConstructionCommand("builder", EntityKind.TURRET, Point(6, 4))
    ).accepted
    assert simulation.execute(RepairBuildingCommand(("repairer",), "factory")).accepted
    simulation.advance()
    app = AirtsApp(simulation)
    feedback = mocked_feedback()
    before = simulation.export_state()
    try:
        feedback._build(app, None)
        values = array("f")
        values.frombytes(feedback.lines.write.call_args.args[0])
        beams = [
            tuple(values[i : i + 9])
            for i in range(0, len(values), 9)
            if tuple(values[i + 4 : i + 7]) == pytest.approx(tuple(c / 255 for c in REPAIR_COLOR))
        ]
        assert len(beams) == 3
        expected = tuple(work_links(simulation))
        for beam, (source, target) in zip(beams, expected, strict=True):
            assert beam[:4] == (source.x, source.y, target.x, target.y)
            assert beam[8] == WORK_BEAM_WIDTH
        assert simulation.export_state() == before
        simulation.entities["tank"].health = EntityKind.LIGHT_TANK.profile.max_health
        assert simulation.execute(ReclaimBuildingCommand(("repairer",), "factory")).accepted
        simulation.advance()
        before = simulation.export_state()
        feedback._build(app, None)
        values = array("f")
        values.frombytes(feedback.lines.write.call_args.args[0])
        beams = [
            tuple(values[i : i + 9])
            for i in range(0, len(values), 9)
            if tuple(values[i + 4 : i + 7]) == pytest.approx(tuple(c / 255 for c in RECLAIM_COLOR))
        ]
        assert len(beams) == 1
        source, target = next(reclaim_links(simulation))
        assert beams[0][:4] == (source.x, source.y, target.x, target.y)
        assert beams[0][8] == WORK_BEAM_WIDTH
        assert simulation.export_state() == before
    finally:
        feedback.release()


def test_gpu_building_line_preview_tracks_draft_without_dirtying_ui_texture() -> None:
    app = feedback_scene()
    app.placement_kind = EntityKind.TURRET
    app.building_line_draft = BuildingLineDraft(
        EntityKind.TURRET, ("builder",), Point(6, 24), Point(14, 24)
    )
    feedback = mocked_feedback()
    try:
        feedback.draw(app, app.window_size)
        key = app._opengl_overlay_key()
        values = array("f")
        values.frombytes(feedback.shapes.write.call_args.args[0])
        preview = [
            tuple(values[i : i + 18]) for i in range(0, len(values), 18) if values[i + 13] == 3
        ]
        assert [v[:4] for v in preview] == [(x + 1, 25, 1, 1) for x in (6, 8, 10, 12, 14)]
        app.building_line_draft.end = Point(10, 24)
        feedback.draw(app, app.window_size)
        assert app._opengl_overlay_key() == key
        values = array("f")
        values.frombytes(feedback.shapes.write.call_args.args[0])
        assert sum(values[i + 13] == 3 for i in range(0, len(values), 18)) == 3
    finally:
        feedback.release()


def test_camera_uses_gpu_uniforms_without_redrawing_or_uploading_world_feedback() -> None:
    pygame.font.init()
    app = feedback_scene()
    app.ui.small_font = pygame.font.Font(None, 19)
    app.ui.font = pygame.font.Font(None, 24)
    feedback = mocked_feedback()
    try:
        feedback.draw(app, app.WINDOW_SIZE)
        key = app._opengl_overlay_key()
        initial_transform = (app.map_origin, app.tile_size)
        uploads = feedback.shapes.write.call_count
        texture = feedback.texture
        with patch.object(feedback, "_build", wraps=feedback._build) as build:
            app.zoom_camera(4, app.canvas_rect.center)
            app.pan_camera(30, -20)
            feedback.draw(app, app.WINDOW_SIZE)
            build.assert_not_called()
        assert (app.map_origin, app.tile_size) != initial_transform
        regions = app._opengl_partial_overlay_regions(key, app._opengl_overlay_key())
        assert regions == (app.minimap.panel,)
        assert feedback.shapes.write.call_count == uploads
        assert feedback.texture is texture
        assert feedback.shape_program["origin"].value == app.map_origin
        assert feedback.shape_program["tile"].value == app.tile_size
        assert feedback.context.scissor is None
        shapes = array("f")
        shapes.frombytes(feedback.shapes.write.call_args.args[0])
        centers = [tuple(shapes[i : i + 2]) for i in range(0, len(shapes), 18)]
        assert (7.5, 11.5) in centers  # Own repair-hub blueprint.
        assert (19.5, 11.5) not in centers  # Enemy blueprint is private.
        assert (18, 18) in centers  # Enemy actual scaffold remains visible.
        assert (3, 19) in centers  # Resource sites are visible from the opening.
        assert feedback.counts[0] == len(shapes) // 18
        assert feedback.counts[3] == 1  # Scaffold percentage is retained.
        # Construction/progress must never invoke a full-canvas Pygame surface.
        with (
            patch.object(app, "_draw_construction", side_effect=AssertionError("CPU construction")),
            patch.object(
                app, "_draw_resource_sites", side_effect=AssertionError("CPU resource sites")
            ),
        ):
            app._draw_opengl_overlay(pygame.Surface(app.WINDOW_SIZE, pygame.SRCALPHA))
    finally:
        feedback.release()


@pytest.mark.parametrize("reverse", [False, True])
def test_gpu_region_fill_triangulates_concave_and_collinear_vertices(reverse: bool) -> None:
    points = (Point(0, 0), Point(2, 0), Point(4, 0), Point(4, 4), Point(2, 2), Point(0, 4))
    region = PolygonRegion(points[::-1] if reverse else points)
    vertices = _triangles(region)
    area = 0.0
    for i in range(0, len(vertices), 3):
        a, b, c = vertices[i : i + 3]
        area += abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)) / 2
    assert area == abs(region.signed_area)


def test_gpu_feedback_releases_partial_shader_initialization() -> None:
    context = MagicMock()
    program = MagicMock()
    context.program.side_effect = [program, RuntimeError("shader rejected")]
    with pytest.raises(RuntimeError, match="shader rejected"):
        GPUFeedback(SimpleNamespace(), context)
    program.release.assert_called_once()
