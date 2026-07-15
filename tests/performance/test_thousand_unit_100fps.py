"""Performance contract for 1,000-unit interactive rendering at 100 Real FPS."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pygame
import pytest
from tests.performance.frame_pacing import RealFpsProbe, assert_real_fps

from airts.automations import AutomationKind, AutomationStatus
from airts.commands import CommandResult, CreateDefendCommand, CreatePatrolCommand, MoveCommand
from airts.geometry import Point, rectangle_region
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.world.map_model import load_map_data

UNIT_COUNT = 1_000
TARGET_FPS = 100
MEASURED_FRAMES = 100
SIMULATION_INTERVAL_FRAMES = TARGET_FPS // Simulation.TICKS_PER_SECOND
MINIMUM_PROGRESSING_UNITS = 100
MAXIMUM_REPRESENTATIVE_PATHS = 32

OrderFactory = Callable[[tuple[str, ...]], MoveCommand | CreatePatrolCommand | CreateDefendCommand]


def _simulation() -> tuple[Simulation, tuple[str, ...]]:
    entity_ids = tuple(f"unit_{index:04d}" for index in range(UNIT_COUNT))
    simulation = Simulation(
        load_map_data(
            {
                "id": "thousand_unit_100fps",
                "name": "Thousand Unit 100 Real FPS",
                "width": 80,
                "height": 60,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [0, 23, 80, 4, "road"],
                        [24, 30, 14, 14, "forest"],
                        [48, 42, 20, 4, "road"],
                    ],
                },
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [index % 50 + 0.5, index // 50 + 0.5],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=73,
    )
    return simulation, entity_ids


def _move_order(entity_ids: tuple[str, ...]) -> MoveCommand:
    return MoveCommand(entity_ids, Point(70.5, 50.5))


def _patrol_order(entity_ids: tuple[str, ...]) -> CreatePatrolCommand:
    return CreatePatrolCommand(
        entity_ids,
        rectangle_region(Point(56, 5), Point(76, 55)),
    )


def _defend_order(entity_ids: tuple[str, ...]) -> CreateDefendCommand:
    return CreateDefendCommand(
        entity_ids,
        rectangle_region(Point(56, 5), Point(76, 55)),
    )


def _rendering_app(
    simulation: Simulation, entity_ids: tuple[str, ...]
) -> tuple[AirtsApp, pygame.Surface]:
    pygame.font.init()
    app = AirtsApp(simulation)
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    app.selected_entities = set(entity_ids)
    surface = pygame.Surface(app.WINDOW_SIZE)
    app._draw(surface)
    return app, surface


def _assert_order_owns_every_unit(
    simulation: Simulation,
    result: CommandResult,
    expected_kind: AutomationKind | None,
    entity_ids: tuple[str, ...],
) -> None:
    if expected_kind is None:
        assert result.automation_id is None
        assert all(
            simulation.entities[entity_id].move_target is not None for entity_id in entity_ids
        )
        return
    automation = simulation.automations[result.automation_id or ""]
    assert automation.kind is expected_kind
    assert automation.status is AutomationStatus.ACTIVE
    assert tuple(automation.entity_ids) == entity_ids
    assert all(
        simulation.assignments.get(entity_id) == automation.automation_id
        for entity_id in entity_ids
    )


@pytest.mark.parametrize(
    ("name", "order_factory", "expected_kind"),
    (
        ("move", _move_order, None),
        ("patrol", _patrol_order, AutomationKind.PATROL),
        ("defend", _defend_order, AutomationKind.DEFEND),
    ),
)
def test_thousand_selected_units_execute_and_render_at_100_real_fps(
    name: str,
    order_factory: OrderFactory,
    expected_kind: AutomationKind | None,
) -> None:
    """Measure one interactive second without excluding command or simulation work."""

    simulation, entity_ids = _simulation()
    app, surface = _rendering_app(simulation, entity_ids)
    initial_positions = {
        entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
    }

    probe = RealFpsProbe()
    result = simulation.execute(order_factory(entity_ids))
    for frame in range(MEASURED_FRAMES):
        if frame % SIMULATION_INTERVAL_FRAMES == 0:
            simulation.advance()
        app._draw(surface)
        probe.frame_completed()

    assert result.accepted
    assert simulation.tick == Simulation.TICKS_PER_SECOND
    assert app.selected_entities == set(entity_ids)
    _assert_order_owns_every_unit(simulation, result, expected_kind, entity_ids)
    progressing = sum(
        simulation.entities[entity_id].position != initial_positions[entity_id]
        for entity_id in entity_ids
    )
    assert progressing >= MINIMUM_PROGRESSING_UNITS
    assert_real_fps(probe, TARGET_FPS, f"{name} with {UNIT_COUNT} selected units")


def test_thousand_selected_unit_paths_are_visible_but_bounded() -> None:
    """Large selections retain route feedback without drawing one full path per unit."""

    simulation, entity_ids = _simulation()
    result = simulation.execute(_move_order(entity_ids))
    app, surface = _rendering_app(simulation, entity_ids)

    assert result.accepted
    with patch("airts.presentation.app.pygame.draw.lines", wraps=pygame.draw.lines) as draw_lines:
        app._draw(surface)

    route_calls = [
        call
        for call in draw_lines.call_args_list
        if len(call.args) >= 2 and call.args[1] == (225, 225, 225)
    ]
    assert 1 <= len(route_calls) <= MAXIMUM_REPRESENTATIVE_PATHS


def test_application_render_loop_uses_the_requested_thousand_fps_ceiling() -> None:
    assert AirtsApp.FRAME_RATE_LIMIT == 1_000
