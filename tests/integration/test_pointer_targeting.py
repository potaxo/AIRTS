"""Zoom-aware intent: precise ground clicks nearby and helpful targeting at distance."""

from unittest.mock import patch

import pygame
import pytest

from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.world.map import load_map_data


def targeting_app(tile: float) -> AirtsApp:
    pygame.font.init()
    app = AirtsApp(
        Simulation(
            load_map_data(
                {
                    "id": "pointer",
                    "name": "Pointer",
                    "width": 80,
                    "height": 80,
                    "terrain": {"default": "grass", "rectangles": []},
                    "entities": [
                        {"id": "builder", "kind": "builder", "position": [8.5, 10.5]},
                        {"id": "tank", "kind": "light_tank", "position": [10.5, 10.5]},
                        {"id": "unit", "kind": "scout", "position": [20.6, 17.5]},
                        {"id": "factory", "kind": "factory", "position": [16, 16]},
                        {"id": "enemy", "kind": "factory", "owner": "enemy", "position": [28, 16]},
                        {
                            "id": "enemy_unit",
                            "kind": "scout",
                            "owner": "enemy",
                            "position": [30.5, 24.5],
                        },
                    ],
                }
            )
        )
    )
    app.camera.zoom = tile / app.tile_size
    app.camera.center_on(Point(24, 18), app.canvas_rect, (80, 80))
    app.ui.font = pygame.font.Font(None, 24)
    app.ui.small_font = pygame.font.Font(None, 19)
    app.simulation.entities["factory"].health -= 50
    return app


@pytest.mark.parametrize("target_id", ["factory", "enemy"])
@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize(
    "tile,assisted", [(8, True), (16, True), (20, False), (24, False), (32, False)]
)
def test_ground_next_to_buildings_respects_zoom_for_context_orders_and_selection(
    target_id: str,
    tile: int,
    assisted: bool,
    queued: bool,
) -> None:
    app = targeting_app(tile)
    assisted = assisted or (tile == 20 and target_id == "enemy")
    building = app.simulation.entities[target_id]
    point = Point(building.position.x + building.profile.footprint[0] + 1, building.position.y + 3)
    app.selected_entities = {"builder" if target_id == "factory" else "tank"}
    with patch.object(app, "_input_modifiers", return_value=pygame.KMOD_SHIFT if queued else 0):
        app._handle_mouse_down(3, app.screen_point(point))
    command = app.simulation.command_history[-1]["command"]
    assert isinstance(command, dict)
    expected = "repair_building" if target_id == "factory" else "attack"
    assert command["type"] == (expected if assisted else "move")
    assert command.get("queued", False) == queued
    app.selected_entities.clear()
    app._select_entities(point, point)
    assert app.selected_entities == ({target_id} if assisted else set())


@pytest.mark.parametrize("target_id", ["factory", "enemy"])
def test_direct_building_hits_still_select_and_issue_context_orders_when_zoomed_in(
    target_id: str,
) -> None:
    app = targeting_app(32)
    building = app.simulation.entities[target_id]
    point = Point(building.position.x + 0.2, building.position.y + 1.5)
    app.selected_entities = {"builder" if target_id == "factory" else "tank"}
    app._handle_mouse_down(3, app.screen_point(point))
    command = app.simulation.command_history[-1]["command"]
    assert isinstance(command, dict)
    assert command["type"] == ("repair_building" if target_id == "factory" else "attack")
    app._select_entities(point, point)
    assert app.selected_entities == {target_id}


def test_direct_unit_hit_wins_over_assisted_building_hit() -> None:
    app = targeting_app(8)
    point = Point(20.1, 17.5)
    app.selected_entities = {"builder"}
    app._handle_mouse_down(3, app.screen_point(point))
    command = app.simulation.command_history[-1]["command"]
    assert isinstance(command, dict) and command["type"] == "move"
    app._select_entities(point, point)
    assert app.selected_entities == {"unit"}


@pytest.mark.parametrize("tile,assisted", [(8, True), (32, False)])
def test_unit_hit_assistance_also_fades_when_zoomed_in(tile: int, assisted: bool) -> None:
    app = targeting_app(tile)
    point = Point(31.8, 24.5)
    app.selected_entities = {"tank"}
    app._handle_mouse_down(3, app.screen_point(point))
    command = app.simulation.command_history[-1]["command"]
    assert isinstance(command, dict) and command["type"] == ("attack" if assisted else "move")
    app.selected_entities.clear()
    app._select_entities(point, point)
    assert app.selected_entities == ({"enemy_unit"} if assisted else set())


@pytest.mark.parametrize("tile,assisted", [(8, True), (32, False)])
def test_reclaim_uses_the_same_zoom_aware_building_targeting(tile: int, assisted: bool) -> None:
    app = targeting_app(tile)
    app.selected_entities = {"builder"}
    app.reclaim_mode = True
    before = app.simulation.command_count
    app._handle_mouse_down(1, app.screen_point(Point(21, 19)))
    assert app.simulation.command_count == before + int(assisted)
    assert app.simulation.entities["builder"].reclaim_target_id == ("factory" if assisted else None)
