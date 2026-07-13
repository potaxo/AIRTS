"""Sustained native-4K contract for a genuine 1,000-unit battle."""

from __future__ import annotations

from time import perf_counter

import pygame

from airts.app import AirtsApp
from airts.commands import CreateDefendCommand, MoveCommand
from airts.geometry import Point, PolylineTarget
from airts.map_model import EntityKind, load_map_data
from airts.opengl_renderer import OpenGLRenderer
from airts.simulation import Simulation

DISPLAY_SIZE = (3840, 2160)
TARGET_FPS = 100
MEASURED_SECONDS = 3
MEASURED_FRAMES = TARGET_FPS * MEASURED_SECONDS
SIMULATION_INTERVAL_FRAMES = TARGET_FPS // Simulation.TICKS_PER_SECOND
UNITS_PER_OWNER = 500


def _unit_kind(index: int) -> str:
    """Return a stable 70/20/10 scout, light-tank, and heavy-tank mix."""

    remainder = index % 10
    if remainder < 7:
        return EntityKind.SCOUT.value
    if remainder < 9:
        return EntityKind.LIGHT_TANK.value
    return EntityKind.HEAVY_TANK.value


def _battle_simulation() -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    player_ids = tuple(f"player_{index:04d}" for index in range(UNITS_PER_OWNER))
    enemy_ids = tuple(f"enemy_{index:04d}" for index in range(UNITS_PER_OWNER))
    units = [
        {
            "id": entity_id,
            "kind": _unit_kind(index),
            "owner": "player",
            "position": [8.5 + index % 25, 20.5 + index // 25],
        }
        for index, entity_id in enumerate(player_ids)
    ] + [
        {
            "id": entity_id,
            "kind": _unit_kind(index),
            "owner": "enemy",
            "position": [47.5 + index % 25, 20.5 + index // 25],
        }
        for index, entity_id in enumerate(enemy_ids)
    ]
    simulation = Simulation(
        load_map_data(
            {
                "id": "sustained_complex_thousand_unit_battle",
                "name": "Sustained Complex Thousand Unit Battle",
                "width": 80,
                "height": 60,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [0, 14, 80, 4, "road"],
                        [0, 42, 80, 4, "road"],
                        [37, 0, 6, 18, "forest"],
                        [37, 42, 6, 18, "forest"],
                    ],
                },
                "entities": units
                + [
                    {
                        "id": "player_factory",
                        "kind": "factory",
                        "owner": "player",
                        "position": [2, 4],
                    },
                    {
                        "id": "player_repair_hub",
                        "kind": "repair_hub",
                        "owner": "player",
                        "position": [2, 50],
                    },
                    {
                        "id": "enemy_factory",
                        "kind": "factory",
                        "owner": "enemy",
                        "position": [74, 4],
                    },
                    {
                        "id": "enemy_repair_hub",
                        "kind": "repair_hub",
                        "owner": "enemy",
                        "position": [74, 50],
                    },
                ],
            }
        ),
        random_seed=211,
    )
    return simulation, player_ids, enemy_ids


def _battle_app(simulation: Simulation, player_ids: tuple[str, ...]) -> AirtsApp:
    pygame.font.init()
    app = AirtsApp(simulation)
    app.resize_layout(DISPLAY_SIZE)
    app._font = pygame.font.Font(None, round(24 * app.ui_scale))
    app._small_font = pygame.font.Font(None, round(19 * app.ui_scale))
    app.selected_entities = set(player_ids)
    app.inspected_entity_id = "enemy_0000"
    app.active_target = PolylineTarget((Point(36, 18), Point(44, 42)))
    app.notice = "1,000-unit mixed battle performance verification"
    return app


def test_sustained_complex_battle_renders_above_100fps() -> None:
    """Real enemies, combat, UI churn, and GPU completion must remain above 100 FPS."""

    simulation, player_ids, enemy_ids = _battle_simulation()
    assert {simulation.entities[item].owner_id for item in player_ids + enemy_ids} == {
        "player",
        "enemy",
    }
    assert {simulation.entities[item].kind for item in player_ids + enemy_ids} == {
        EntityKind.SCOUT,
        EntityKind.LIGHT_TANK,
        EntityKind.HEAVY_TANK,
    }
    app = _battle_app(simulation, player_ids)
    renderer = OpenGLRenderer.from_standalone_context()
    target = renderer.create_offscreen_target(DISPLAY_SIZE)
    starting_health = sum(entity.health for entity in simulation.entities.values())
    maximum_collision_checks = 0
    maximum_projectiles = 0
    try:
        renderer_name = str(renderer.info["renderer"])
        software_renderers = (
            "llvmpipe",
            "softpipe",
            "software rasterizer",
            "swiftshader",
            "gdi generic",
            "microsoft basic render driver",
        )
        assert not any(name in renderer_name.lower() for name in software_renderers), (
            f"expected a hardware OpenGL renderer, got {renderer_name!r}"
        )
        warm_frame = renderer.render(app, DISPLAY_SIZE, framebuffer=target.framebuffer)
        assert warm_frame.unit_count == UNITS_PER_OWNER * 2
        assert warm_frame.selected_unit_count == UNITS_PER_OWNER
        renderer.finish()
        started = perf_counter()
        assert app.active_target is not None
        defend_result = simulation.execute(
            CreateDefendCommand(
                player_ids,
                app.active_target,
                title="Hold the center against the enemy army",
                original_instruction="Defend this line with the selected army.",
            )
        )
        assert defend_result.accepted
        app.selected_automation_id = defend_result.automation_id
        assert simulation.execute(MoveCommand(enemy_ids, Point(17.5, 30.5), "enemy")).accepted
        interpolation_samples: set[float] = set()
        for frame_index in range(MEASURED_FRAMES):
            # A real Clock reading fluctuates. It must not force a full 4K UI upload every frame.
            app.fps = 96.0 + frame_index % 9
            if frame_index % 50 == 0:
                inspected_index = (frame_index // 50) % UNITS_PER_OWNER
                app.inspected_entity_id = f"enemy_{inspected_index:04d}"
            if frame_index % SIMULATION_INTERVAL_FRAMES == 0:
                app._advance_presentation_tick()
                maximum_collision_checks = max(
                    maximum_collision_checks,
                    simulation.collision_pair_check_count,
                )
                maximum_projectiles = max(maximum_projectiles, len(simulation.projectiles))
            app.render_alpha = (frame_index % SIMULATION_INTERVAL_FRAMES) / (
                SIMULATION_INTERVAL_FRAMES - 1
            )
            interpolation_samples.add(app.render_alpha)
            renderer.render(app, DISPLAY_SIZE, framebuffer=target.framebuffer)
        renderer.finish()
        elapsed = perf_counter() - started
        rendered_pixel = target.read_pixel(
            (app.canvas_rect.centerx, DISPLAY_SIZE[1] - app.canvas_rect.centery)
        )
    finally:
        target.release()
        renderer.release()

    ending_health = sum(entity.health for entity in simulation.entities.values())
    assert simulation.tick == Simulation.TICKS_PER_SECOND * MEASURED_SECONDS
    assert maximum_collision_checks > 0
    assert maximum_projectiles > 0
    assert len(interpolation_samples) == SIMULATION_INTERVAL_FRAMES
    assert ending_health < starting_health
    assert rendered_pixel != bytes((*AirtsApp.BACKGROUND, 255))
    achieved_fps = MEASURED_FRAMES / elapsed
    assert achieved_fps >= TARGET_FPS, (
        f"{renderer_name} achieved {achieved_fps:.1f} sustained complex-battle FPS "
        f"({elapsed:.3f}s for {MEASURED_FRAMES} frames and {simulation.tick} ticks)"
    )
