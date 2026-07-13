"""Expected 4K behavior for 1,000-scout movement and collision at 100 FPS."""

from __future__ import annotations

from time import perf_counter

import pygame

from airts.app import AirtsApp
from airts.commands import CommandResult, MoveCommand
from airts.geometry import Point
from airts.map_model import load_map_data
from airts.simulation import Simulation

DISPLAY_SIZE = (3840, 2160)
UNIT_COUNT = 1_000
GROUP_SIZE = UNIT_COUNT // 2
TARGET_FPS = 100
MEASURED_FRAMES = 100
MEASURED_TICKS = Simulation.TICKS_PER_SECOND
SIMULATION_INTERVAL_FRAMES = TARGET_FPS // Simulation.TICKS_PER_SECOND
MINIMUM_PROGRESSING_UNITS = 750


def _head_on_scout_simulation() -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    eastbound = tuple(f"east_{index:04d}" for index in range(GROUP_SIZE))
    westbound = tuple(f"west_{index:04d}" for index in range(GROUP_SIZE))
    simulation = Simulation(
        load_map_data(
            {
                "id": "four_k_thousand_scout_head_on",
                "name": "4K Thousand Scout Head-On Collision",
                "width": 80,
                "height": 60,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [0, 14, 80, 4, "road"],
                        [0, 42, 80, 4, "road"],
                        [31, 20, 3, 20, "forest"],
                    ],
                },
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": [5.5 + index % 25, 20.5 + index // 25],
                    }
                    for index, entity_id in enumerate(eastbound)
                ]
                + [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": [35.5 + index % 25, 20.5 + index // 25],
                    }
                    for index, entity_id in enumerate(westbound)
                ]
                + [
                    {
                        "id": "factory",
                        "kind": "factory",
                        "owner": "player",
                        "position": [68, 4],
                    },
                    {
                        "id": "repair_hub",
                        "kind": "repair_hub",
                        "owner": "player",
                        "position": [68, 12],
                    },
                    {
                        "id": "command_center",
                        "kind": "command_center",
                        "owner": "player",
                        "position": [68, 48],
                    },
                    {
                        "id": "resource_generator",
                        "kind": "resource_generator",
                        "owner": "player",
                        "position": [4, 48],
                    },
                ],
            }
        ),
        random_seed=97,
    )
    return simulation, eastbound, westbound


def _four_k_app(
    simulation: Simulation, entity_ids: tuple[str, ...]
) -> tuple[AirtsApp, pygame.Surface]:
    pygame.font.init()
    app = AirtsApp(simulation)
    app.resize_layout(DISPLAY_SIZE)
    app._font = pygame.font.Font(None, round(24 * app.ui_scale))
    app._small_font = pygame.font.Font(None, round(19 * app.ui_scale))
    app.selected_entities = set(entity_ids)
    surface = pygame.Surface(DISPLAY_SIZE)
    app._draw(surface)
    return app, surface


def _issue_head_on_orders(
    simulation: Simulation,
    eastbound: tuple[str, ...],
    westbound: tuple[str, ...],
) -> tuple[CommandResult, CommandResult]:
    return (
        simulation.execute(MoveCommand(eastbound, Point(70.5, 30.5))),
        simulation.execute(MoveCommand(westbound, Point(9.5, 30.5))),
    )


def _assert_orders_active(
    simulation: Simulation,
    results: tuple[CommandResult, CommandResult],
    entity_ids: tuple[str, ...],
) -> None:
    assert all(result.accepted for result in results)
    assert all(simulation.entities[entity_id].move_target is not None for entity_id in entity_ids)


def test_4k_static_thousand_scout_rendering_sustains_100fps() -> None:
    """A static large army must not become slow merely because the window is 4K."""

    simulation, eastbound, westbound = _head_on_scout_simulation()
    app, surface = _four_k_app(simulation, eastbound + westbound)

    started = perf_counter()
    for _ in range(MEASURED_FRAMES):
        app._draw(surface)
    elapsed = perf_counter() - started

    achieved_fps = MEASURED_FRAMES / elapsed
    assert achieved_fps >= TARGET_FPS, (
        f"static 4K rendering achieved {achieved_fps:.1f} FPS "
        f"({elapsed:.3f}s for {MEASURED_FRAMES} frames)"
    )


def test_thousand_scout_head_on_collision_cpu_work_fits_one_realtime_second() -> None:
    """Command planning and ten authoritative dense-collision ticks fit their real-time budget."""

    simulation, eastbound, westbound = _head_on_scout_simulation()
    entity_ids = eastbound + westbound
    initial_positions = {
        entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
    }

    started = perf_counter()
    results = _issue_head_on_orders(simulation, eastbound, westbound)
    maximum_collision_checks = 0
    for _ in range(MEASURED_TICKS):
        simulation.advance()
        maximum_collision_checks = max(
            maximum_collision_checks,
            simulation.collision_pair_check_count,
        )
    elapsed = perf_counter() - started

    _assert_orders_active(simulation, results, entity_ids)
    progressing = sum(
        simulation.entities[entity_id].position != initial_positions[entity_id]
        for entity_id in entity_ids
    )
    assert simulation.tick == MEASURED_TICKS
    assert progressing >= MINIMUM_PROGRESSING_UNITS
    assert maximum_collision_checks > 0
    assert elapsed <= 1.0, f"1,000-scout command and collision ticks took {elapsed:.3f}s"


def test_4k_thousand_scout_head_on_collision_sustains_100fps_end_to_end() -> None:
    """The measured second includes commands, collisions, visibility, UI, and 4K drawing."""

    simulation, eastbound, westbound = _head_on_scout_simulation()
    entity_ids = eastbound + westbound
    app, surface = _four_k_app(simulation, entity_ids)
    initial_positions = {
        entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
    }

    started = perf_counter()
    results = _issue_head_on_orders(simulation, eastbound, westbound)
    maximum_collision_checks = 0
    for frame in range(MEASURED_FRAMES):
        if frame % SIMULATION_INTERVAL_FRAMES == 0:
            simulation.advance()
            maximum_collision_checks = max(
                maximum_collision_checks,
                simulation.collision_pair_check_count,
            )
        app._draw(surface)
    elapsed = perf_counter() - started

    _assert_orders_active(simulation, results, entity_ids)
    progressing = sum(
        simulation.entities[entity_id].position != initial_positions[entity_id]
        for entity_id in entity_ids
    )
    assert simulation.tick == MEASURED_TICKS
    assert app.selected_entities == set(entity_ids)
    assert progressing >= MINIMUM_PROGRESSING_UNITS
    assert maximum_collision_checks > 0
    achieved_fps = MEASURED_FRAMES / elapsed
    assert achieved_fps >= TARGET_FPS, (
        f"head-on 4K collision achieved {achieved_fps:.1f} FPS "
        f"({elapsed:.3f}s for {MEASURED_FRAMES} frames)"
    )


def test_explicit_software_runtime_scales_a_bounded_framebuffer_for_4k_windows() -> None:
    """The compatibility backend must not expand its software buffer to physical 4K."""

    assert AirtsApp.DISPLAY_FLAGS & pygame.SCALED
    assert AirtsApp.DISPLAY_FLAGS & pygame.RESIZABLE
    assert AirtsApp.WINDOW_SIZE[0] < DISPLAY_SIZE[0]
    assert AirtsApp.WINDOW_SIZE[1] < DISPLAY_SIZE[1]
