"""Performance contract for 4K 1,000-scout movement and collision at 100 Real FPS."""

from __future__ import annotations

from time import perf_counter

import pygame
import pytest
from tests.performance.frame_pacing import RealFpsProbe, assert_real_fps
from tests.scout_scenario import head_on_scout_simulation

from airts.contracts.commands import CommandResult, MoveCommand
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation

DISPLAY_SIZE = (3840, 2160)
UNIT_COUNT = 1_000
TARGET_FPS = 100
MEASURED_FRAMES = 100
MEASURED_TICKS = Simulation.TICKS_PER_SECOND
SIMULATION_INTERVAL_FRAMES = TARGET_FPS // Simulation.TICKS_PER_SECOND
MINIMUM_PROGRESSING_UNITS = 750


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


@pytest.mark.performance
def test_4k_static_thousand_scout_rendering_sustains_100_real_fps() -> None:
    """A static large army must not become slow merely because the window is 4K."""

    simulation, eastbound, westbound = head_on_scout_simulation()
    app, surface = _four_k_app(simulation, eastbound + westbound)

    probe = RealFpsProbe()
    for _ in range(MEASURED_FRAMES):
        app._draw(surface)
        probe.frame_completed()

    assert_real_fps(probe, TARGET_FPS, "static 4K rendering")


def test_thousand_scout_head_on_collision_cpu_work_fits_one_realtime_second(
    check_timing: bool,
) -> None:
    """Command planning and ten authoritative dense-collision ticks fit their real-time budget."""

    simulation, eastbound, westbound = head_on_scout_simulation()
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
    if check_timing:
        assert elapsed <= 1.0, f"1,000-scout command and collision ticks took {elapsed:.3f}s"


def test_4k_thousand_scout_head_on_collision_sustains_100_real_fps_end_to_end(
    check_timing: bool,
) -> None:
    """The measured second includes commands, collisions, visibility, UI, and 4K drawing."""

    simulation, eastbound, westbound = head_on_scout_simulation()
    entity_ids = eastbound + westbound
    app, surface = _four_k_app(simulation, entity_ids)
    initial_positions = {
        entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
    }

    probe = RealFpsProbe()
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
        probe.frame_completed()

    _assert_orders_active(simulation, results, entity_ids)
    progressing = sum(
        simulation.entities[entity_id].position != initial_positions[entity_id]
        for entity_id in entity_ids
    )
    assert simulation.tick == MEASURED_TICKS
    assert app.selected_entities == set(entity_ids)
    assert progressing >= MINIMUM_PROGRESSING_UNITS
    assert maximum_collision_checks > 0
    if check_timing:
        assert_real_fps(probe, TARGET_FPS, "head-on 4K collision")
