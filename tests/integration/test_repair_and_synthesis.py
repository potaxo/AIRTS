"""Builder repair, automatic repair capacity, synthesis and moving-target pursuit."""

from pathlib import Path
from unittest.mock import patch

import pygame
import pytest

from airts.adapters.persistence import (
    SAVE_SCHEMA,
    PersistenceError,
    load_simulation,
    load_simulation_data,
    save_simulation,
)
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.commands import (
    AttackCommand,
    CreateConstructionCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
    RepairBuildingCommand,
    StopCommand,
)
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.opengl_renderer import OpenGLFrameBuilder
from airts.presentation.rendering import SoftwareRenderer, order_feedback
from airts.simulation import Simulation
from airts.world.map import EntityKind, load_map_data


def repair_world() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "repair_synthesis",
                "name": "Repair and synthesis",
                "width": 48,
                "height": 32,
                "terrain": {"default": "grass"},
                "resource_sites": [[30, 25]],
                "entities": [
                    {"id": "builder", "kind": "builder", "position": [2.5, 4.5]},
                    {"id": "factory", "kind": "factory", "position": [12, 10]},
                    {"id": "hub", "kind": "repair_hub", "position": [22, 12]},
                    {"id": "a", "kind": "light_tank", "position": [20.5, 13.5]},
                    {"id": "b", "kind": "light_tank", "position": [26.5, 13.5]},
                    {"id": "outside", "kind": "scout", "position": [35.5, 13.5]},
                    {
                        "id": "hostile",
                        "kind": "builder",
                        "owner": "enemy",
                        "position": [23.5, 17.5],
                    },
                ],
            }
        )
    )


def test_builder_approaches_repairs_and_save_continues(tmp_path: Path) -> None:
    simulation = repair_world()
    factory = simulation.entities["factory"]
    factory.health = 200
    assert simulation.execute(RepairBuildingCommand(("builder",), "factory")).accepted
    simulation.advance(35)
    assert factory.health < factory.kind.profile.max_health
    path = tmp_path / "builder.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    simulation.advance(110)
    restored.advance(110)
    assert simulation.export_state() == restored.export_state()
    assert factory.health == factory.kind.profile.max_health
    assert simulation.entities["builder"].repair_target_id is None


@pytest.mark.parametrize(
    "replacement", [StopCommand(("builder",)), MoveCommand(("builder",), Point(4.5, 6.5))]
)
def test_builder_repair_is_overridden_by_direct_control(
    replacement: StopCommand | MoveCommand,
) -> None:
    simulation = repair_world()
    factory = simulation.entities["factory"]
    factory.health = 200
    assert simulation.execute(RepairBuildingCommand(("builder",), "factory")).accepted
    assert simulation.execute(replacement).accepted
    simulation.advance(50)
    assert factory.health == 200
    assert simulation.entities["builder"].repair_target_id is None


@pytest.mark.parametrize(
    "ids,target",
    [
        (("a",), "factory"),
        (("builder",), "hostile"),
        (("builder",), "a"),
        (("builder",), "missing"),
    ],
)
def test_invalid_builder_repair_preserves_current_order(ids: tuple[str, ...], target: str) -> None:
    simulation = repair_world()
    simulation.entities["factory"].health = 200
    assert simulation.execute(RepairBuildingCommand(("builder",), "factory")).accepted
    assert not simulation.execute(RepairBuildingCommand(ids, target)).accepted
    assert simulation.entities["builder"].repair_target_id == "factory"


def test_hub_repairs_one_in_range_unit_until_full_then_switches(tmp_path: Path) -> None:
    simulation = repair_world()
    for key in ("a", "b", "outside", "hostile"):
        simulation.entities[key].health = 20
    simulation.advance(1)
    assert simulation.entities["a"].health == 25
    assert simulation.entities["b"].health == 20
    assert simulation.entities["outside"].health == 20
    assert simulation.entities["hostile"].health == 20
    save_simulation(simulation, tmp_path / "hub.json")
    restored = load_simulation(tmp_path / "hub.json")
    simulation.advance(16)
    restored.advance(16)
    assert simulation.export_state() == restored.export_state()
    assert simulation.entities["a"].health == 100
    assert simulation.entities["b"].health == 25


def test_queued_construction_waits_for_direct_building_repair() -> None:
    simulation = repair_world()
    simulation.entities["factory"].health = 200
    active = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(4, 6))
    )
    queued = simulation.execute(
        CreateConstructionCommand(
            "builder", EntityKind.RESOURCE_GENERATOR, Point(30, 25), queued=True
        )
    )
    assert active.accepted and queued.accepted
    assert simulation.execute(RepairBuildingCommand(("builder",), "factory")).accepted
    simulation.advance(1)
    assert simulation.entities["builder"].repair_target_id == "factory"
    assert simulation.automations[queued.automation_id or ""].reason_code == "BUILDERS_QUEUED"
    simulation.advance(160)
    assert simulation.entities["factory"].health == 500
    assert simulation.assignments["builder"] == queued.automation_id


def test_explicit_repair_and_return_shares_hub_capacity_and_returns() -> None:
    simulation = repair_world()
    simulation.entities["a"].health = simulation.entities["b"].health = 20
    result = simulation.execute(CreateRepairAndReturnCommand(("a", "b")))
    assert result.accepted
    simulation.advance(1)
    assert sorted(simulation.entities[key].health for key in ("a", "b")) == [20, 25]
    simulation.advance(100)
    assert all(simulation.entities[key].health == 100 for key in ("a", "b"))
    assert simulation.automations[result.automation_id or ""].status.terminal


def test_hub_loses_target_when_it_leaves_range() -> None:
    simulation = repair_world()
    simulation.entities["a"].health = simulation.entities["b"].health = 20
    simulation.advance(1)
    assert simulation.execute(MoveCommand(("a",), Point(10.5, 22.5))).accepted
    simulation.advance(45)
    assert simulation.entities["a"].health < 100
    assert simulation.entities["b"].health > 20


def test_synthesizer_builds_off_site_and_earns_only_after_completion(tmp_path: Path) -> None:
    simulation = repair_world()
    simulation.resources["player"] = 2000
    before = simulation.resources["player"]
    assert simulation.execute(
        CreateConstructionCommand("builder", EntityKind.MATTER_SYNTHESIZER, Point(4, 6))
    ).accepted
    simulation.advance(10)
    site = next(
        entity
        for entity in simulation.entities.values()
        if entity.kind is EntityKind.MATTER_SYNTHESIZER
    )
    assert not site.is_complete
    assert simulation.resources["player"] == before - 1200
    save_simulation(simulation, tmp_path / "synthesis.json")
    restored = load_simulation(tmp_path / "synthesis.json")
    simulation.advance(40)
    restored.advance(40)
    assert simulation.export_state() == restored.export_state()
    assert site.is_complete
    balance = simulation.resources["player"]
    simulation.advance(10)
    assert simulation.resources["player"] == balance + 5


def test_synthesis_commands_replay() -> None:
    simulation = repair_world()
    # Default opening balance suffices after an extractor produces sandbox income.
    simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(30, 25))
    )
    simulation.advance(250)
    # A second builder order waits for funds if necessary.
    simulation.execute(
        CreateConstructionCommand("builder", EntityKind.MATTER_SYNTHESIZER, Point(4, 6))
    )
    simulation.advance(220)
    assert any(
        entity.kind is EntityKind.MATTER_SYNTHESIZER and entity.is_complete
        for entity in simulation.entities.values()
    )
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


def test_builder_repair_command_replays_combat_damage_and_restoration() -> None:
    data = repair_world().game_map.to_dict()
    data["entities"] = [
        {"id": "builder", "kind": "builder", "position": [2.5, 4.5]},
        {"id": "factory", "kind": "factory", "position": [12, 10]},
        {"id": "enemy", "kind": "scout", "owner": "enemy", "position": [18.5, 12.5]},
    ]
    simulation = Simulation(load_map_data(data))
    simulation.advance(40)
    assert simulation.entities["factory"].health < 500
    simulation.remove_entity("enemy")
    assert simulation.execute(RepairBuildingCommand(("builder",), "factory")).accepted
    simulation.advance(150)
    assert simulation.entities["factory"].health == 500
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


def test_hub_radius_includes_boundary_and_excludes_unfinished_source() -> None:
    simulation = repair_world()
    unit = simulation.entities["outside"]
    unit.health = 20
    assert simulation.execute(MoveCommand(("outside",), Point(29.5, 13.5))).accepted
    simulation.advance(20)
    assert unit.position == Point(29.5, 13.5)
    assert unit.health > 20
    simulation.entities["hub"].construction_progress = 0.5
    health = unit.health
    simulation.advance(1)
    assert unit.health == health


def test_moving_target_refreshes_chase_before_old_route_finishes_and_dies() -> None:
    simulation = repair_world()
    attacker = simulation.entities["outside"]
    target = simulation.entities["hostile"]
    simulation.remove_entity("a")
    simulation.remove_entity("b")
    assert simulation.execute(AttackCommand(("outside",), "hostile")).accepted
    simulation.advance(1)
    old_goal = attacker.move_target
    assert old_goal is not None
    assert simulation.execute(
        MoveCommand(("hostile",), Point(4.5, 26.5), owner_id="enemy")
    ).accepted
    refreshed = False
    for _ in range(60):
        simulation.advance(1)
        assert attacker.attack_target_id == "hostile" or "hostile" not in simulation.entities
        if attacker.move_target is not None and attacker.move_target != old_goal:
            refreshed = True
            break
    assert refreshed
    assert target.health > 0
    simulation.advance(350)
    assert "hostile" not in simulation.entities
    assert not attacker.pursue_target
    assert not attacker.path


def test_right_click_dispatch_price_and_live_attack_feedback() -> None:
    simulation = repair_world()
    app = AirtsApp(simulation)
    simulation.entities["factory"].health = 200
    app.selected_entities = {"builder"}
    point = app.screen_point(simulation.entities["factory"].selection_position)
    app._handle_mouse_down(3, (point[0] - app.map_origin[0], point[1] - app.map_origin[1]))
    assert simulation.entities["builder"].repair_target_id == "factory"
    app.selected_entities = {"builder", "outside"}
    point = app.screen_point(simulation.entities["hostile"].selection_position)
    app._handle_mouse_down(3, (point[0] - app.map_origin[0], point[1] - app.map_origin[1]))
    attacker = simulation.entities["outside"]
    assert attacker.pursue_target
    assert simulation.entities["builder"].repair_target_id == "factory"
    points, color = order_feedback(app, attacker)
    assert points[-1] == simulation.entities["hostile"].selection_position
    assert color[0] > color[1]
    assert "outside" in app.representative_path_entity_ids()
    software = SoftwareRenderer()
    assert software._representative_path_points(app)
    packed, count, paths = OpenGLFrameBuilder._build_paths(app, (0, 0), 1.0)
    assert packed and count == 2 and paths == 1
    old_endpoint = points[-1]
    assert simulation.execute(
        MoveCommand(("hostile",), Point(23.5, 22.5), owner_id="enemy")
    ).accepted
    simulation.advance(1)
    points, _ = order_feedback(app, attacker)
    assert points[-1] != old_endpoint
    assert points[-1] == simulation.entities["hostile"].selection_position
    pygame.font.init()
    app._font = pygame.font.Font(None, 22)
    app._small_font = pygame.font.Font(None, 18)
    app.selected_entities = {"factory"}
    with patch.object(app, "_small_text", wraps=app._small_text) as text:
        app._draw_context_panel(pygame.Surface(app.window_size))
    assert any("Cost 400" in call.args[1] for call in text.call_args_list)


def test_large_attack_selection_keeps_feedback_bounded() -> None:
    data = repair_world().game_map.to_dict()
    data["entities"] = [
        {
            "id": f"unit_{index:03d}",
            "kind": "scout",
            "position": [2.5 + index % 16, 2.5 + index // 16],
        }
        for index in range(64)
    ] + [{"id": "enemy", "kind": "builder", "owner": "enemy", "position": [40.5, 26.5]}]
    simulation = Simulation(load_map_data(data))
    app = AirtsApp(simulation)
    app.selected_entities = {key for key in simulation.entities if key != "enemy"}
    assert simulation.execute(AttackCommand(tuple(sorted(app.selected_entities)), "enemy")).accepted
    assert len(app.representative_path_entity_ids()) == app.MAX_SELECTED_PATHS
    _, vertices, paths = OpenGLFrameBuilder._build_paths(app, (0, 0), 1.0)
    assert paths == app.MAX_SELECTED_PATHS and vertices == 2 * app.MAX_SELECTED_PATHS


def test_save_rejects_wrong_repair_target_kind() -> None:
    simulation = repair_world()
    simulation.entities["builder"].repair_target_id = "a"
    with pytest.raises(PersistenceError, match="invalid repair target"):
        load_simulation_data(
            {
                "schema": SAVE_SCHEMA,
                "map": simulation.game_map.to_dict(),
                "state": simulation.export_state(),
            }
        )
