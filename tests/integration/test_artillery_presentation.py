"""Software and GPU artillery feedback agree on arc, scale, blast radius and expiry."""

from array import array
from math import ceil
from unittest.mock import patch

import pygame
import pytest

from airts.contracts.commands import AttackCommand
from airts.presentation.app import AirtsApp
from airts.presentation.gpu_shapes import SHAPE_FLOATS
from airts.presentation.opengl_renderer import OpenGLFrameBuilder
from airts.simulation import Simulation
from airts.world.entities import SHELL_BLAST_TICKS
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


@pytest.mark.parametrize("kind", [EntityKind.ARTILLERY, EntityKind.LIGHT_ARTILLERY])
def test_artillery_arc_and_blast_use_both_backends_without_mutating_simulation(
    kind: EntityKind,
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "shell_visuals",
                "name": "Shell visuals",
                "width": 24,
                "height": 24,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "gun", "kind": kind.value, "position": [2.5, 12.5]},
                    {
                        "id": "target",
                        "kind": "builder",
                        "owner": "enemy",
                        "position": [2.5 + kind.profile.attack_range, 12.5],
                    },
                ],
            }
        )
    )
    assert simulation.execute(AttackCommand(("gun",), "target")).accepted
    travel_ticks = ceil(
        kind.profile.attack_range / kind.profile.projectile_speed / Simulation.TICK_SECONDS
    )
    midflight_ticks = max(1, travel_ticks // 2)
    simulation.advance(midflight_ticks)
    app = AirtsApp(simulation)
    builder = OpenGLFrameBuilder()
    shell = next(iter(simulation.projectiles.values()))
    surface = pygame.Surface(app.WINDOW_SIZE)

    def draw_feedback() -> tuple[list[tuple[object, ...]], list[array[float]]]:
        before = simulation.export_state()
        with patch(
            "airts.presentation.rendering.pygame.draw.circle", wraps=pygame.draw.circle
        ) as draw:
            app._software_renderer.draw_projectiles(app, surface)
        packed, _, _, _ = builder._build_projectiles(app, (0, 0), 1)
        values = array("f")
        values.frombytes(packed)
        shapes = [values[i : i + SHAPE_FLOATS] for i in range(0, len(values), SHAPE_FLOATS)]
        assert simulation.export_state() == before
        return [call.args for call in draw.call_args_list], shapes

    circles, shapes = draw_feedback()
    assert [circle[3] for circle in circles] == [3, 6, 4]
    assert shapes[1][6:8] == array("f", [6, 6])
    assert shapes[2][6:8] == array("f", [4, 4])
    assert shapes[1][0] == pytest.approx(shell.position.x)
    assert shapes[1][1] < shell.position.y  # Display arc above ground shadow.
    assert shapes[0][1] == pytest.approx(shell.position.y)

    simulation.advance(travel_ticks + 1 - midflight_ticks)
    circles, shapes = draw_feedback()
    destination = simulation.projectile_traces[-1].points[-1]
    assert circles[0][2] == app.screen_point(destination)
    assert circles[0][3] == round(kind.profile.splash_radius * app.tile_size)
    assert circles[0][4] == 1  # Full radius outline.
    assert shapes[0][0:2] == array("f", [destination.x, destination.y])
    assert shapes[0][4:6] == array("f", [kind.profile.splash_radius] * 2)
    assert shapes[0][19] == 1  # GPU outline, with a transparent interior.
    assert shapes[0][17] == 0
    assert simulation.projectile_traces[-1].expires_tick - simulation.tick == SHELL_BLAST_TICKS
    simulation.remove_entity("gun")
    initial_wave_radius = shapes[1][4]
    initial_flash_radius = shapes[2][4]
    simulation.advance()
    _, shapes = draw_feedback()
    assert shapes[1][4] > initial_wave_radius
    assert shapes[2][4] < initial_flash_radius
    simulation.advance(SHELL_BLAST_TICKS - 1)
    circles, shapes = draw_feedback()
    assert not circles and not shapes
