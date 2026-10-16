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
from airts.contracts.automations import AutomationStatus, ConstructionParameters
from airts.contracts.commands import (
    AttackCommand,
    CreateConstructionCommand,
    CreatePatrolCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
    ReclaimBuildingCommand,
    RepairBuildingCommand,
    StopCommand,
)
from airts.contracts.spatial import Point, PolylineTarget
from airts.presentation.app import AirtsApp
from airts.presentation.opengl_renderer import OpenGLFrameBuilder
from airts.presentation.palette import REPAIR_COLOR, WORK_BEAM_WIDTH
from airts.presentation.rendering import SoftwareRenderer, order_feedback, work_links
from airts.presentation.ui import selection
from airts.simulation import Simulation
from airts.systems.repair import building_distance
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


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
    simulation.entities["hub"].health = 20
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
    assert simulation.entities["hub"].health == 20  # A hub cannot service itself.


def hub_building_world() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "hub_buildings",
                "name": "Hub buildings",
                "width": 48,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "hub", "kind": "repair_hub", "position": [22, 12]},
                    {"id": "building", "kind": "factory", "position": [26, 12]},
                    {"id": "outside", "kind": "factory", "position": [24, 18]},
                    {"id": "hostile", "kind": "factory", "owner": "enemy", "position": [16, 12]},
                    {"id": "scaffold", "kind": "factory", "position": [22, 7]},
                    {"id": "builder", "kind": "builder", "position": [25.5, 13.5]},
                ],
            }
        )
    )


def test_hub_repairs_building_retains_target_and_restores_service(tmp_path: Path) -> None:
    simulation = hub_building_world()
    for key in ("building", "outside", "hostile", "scaffold"):
        simulation.entities[key].health = 200
    simulation.entities["scaffold"].construction_progress = 0.5
    simulation.advance()
    hub, building = simulation.entities["hub"], simulation.entities["building"]
    assert building.health == 205 and hub.repair_target_id == "building"
    assert tuple(work_links(simulation)) == ((hub.selection_position, building.selection_position),)
    # A nearer wounded unit does not steal active service.
    simulation.entities["builder"].health = 20
    path = tmp_path / "hub-building.json"
    save_simulation(simulation, path)
    loaded = load_simulation(path)
    simulation.advance(59)
    loaded.advance(59)
    assert simulation.export_state() == loaded.export_state()
    assert building.health == building.kind.profile.max_health
    assert simulation.entities["builder"].health == 20
    assert all(simulation.entities[key].health == 200 for key in ("outside", "hostile", "scaffold"))
    simulation.advance()
    assert simulation.entities["builder"].health == 25 and hub.repair_target_id == "builder"


def test_hub_avoids_reclamation_and_resumes_healing_when_stopped() -> None:
    simulation = hub_building_world()
    target = simulation.entities["building"]
    target.health = 200
    simulation.advance()
    assert simulation.entities["hub"].repair_target_id == "building"
    before = simulation.resources["player"]
    assert simulation.execute(ReclaimBuildingCommand(("builder",), "building")).accepted
    assert simulation.entities["hub"].repair_target_id is None
    assert not tuple(work_links(simulation))
    simulation.advance(10)
    assert target.health == 155
    assert simulation.entities["hub"].repair_target_id is None
    assert not tuple(work_links(simulation))
    assert simulation.resources["player"] == before
    assert simulation.execute(StopCommand(("builder",))).accepted
    simulation.advance()
    assert target.health == 160 and simulation.entities["hub"].repair_target_id == "building"


def test_repair_beam_tracks_hub_service_without_selection_in_both_renderers() -> None:
    simulation = repair_world()
    simulation.entities["a"].health = 90
    simulation.entities["b"].health = 20
    app = AirtsApp(simulation)
    software = SoftwareRenderer()

    def check_beam(target_id: str | None) -> None:
        before = simulation.export_state()
        paths = software._representative_path_points(app)
        packed, vertices, selected_paths = OpenGLFrameBuilder._build_paths(app, (0, 0), 1.0)
        assert selected_paths == 0
        assert not packed and vertices == 0  # GPU work beams use the thick feedback segment batch.
        if target_id is None:
            assert not paths and not packed and vertices == 0
            assert not tuple(work_links(simulation))
        else:
            source = simulation.entities["hub"].selection_position
            target = simulation.entities[target_id].selection_position
            assert paths == ((REPAIR_COLOR, (app.screen_point(source), app.screen_point(target))),)
            assert tuple(work_links(simulation)) == ((source, target),)
            with patch("airts.presentation.rendering.pygame.draw.lines") as lines:
                software.draw_entities(app, pygame.Surface(app.window_size))
            lines.assert_called_once_with(
                lines.call_args.args[0], REPAIR_COLOR, False, paths[0][1], WORK_BEAM_WIDTH
            )
        assert simulation.export_state() == before

    check_beam(None)
    simulation.advance()
    check_beam("a")
    simulation.advance()
    check_beam(None)  # Full health clears the service link in the same tick.
    simulation.advance()
    check_beam("b")
    simulation.remove_entity("b")
    check_beam(None)


def test_builder_repairs_from_a_clear_position_in_range_without_touching_building() -> None:
    simulation = repair_world()
    builder, building = simulation.entities["builder"], simulation.entities["factory"]
    building.health = 200
    assert simulation.execute(RepairBuildingCommand(("builder",), "factory")).accepted
    simulation.advance(35)
    assert building.health > 200
    assert 2.5 < building_distance(builder.position, building) <= builder.kind.profile.repair_range
    assert not builder.path and builder.move_target is None
    assert not builder.occupied_cells & building.occupied_cells
    assert (builder.position, building.selection_position) in tuple(work_links(simulation))
    assert simulation.execute(StopCommand(("builder",))).accepted
    assert not tuple(work_links(simulation))


def test_factory_unit_healing_has_a_beam_only_during_service() -> None:
    simulation = repair_world()
    simulation.remove_entity("hub")
    simulation.remove_entity("hostile")
    unit = simulation.entities["a"]
    unit.health = 20
    assert simulation.execute(CreateRepairAndReturnCommand(("a",))).accepted
    source = simulation.entities["factory"].selection_position
    for _ in range(100):
        simulation.advance()
        if (source, unit.position) in tuple(work_links(simulation)):
            break
    else:
        pytest.fail("Unit never entered factory repair service")
    health = unit.health
    simulation.advance()
    assert unit.health > health
    simulation.advance(100)
    assert unit.health == unit.kind.profile.max_health
    assert not tuple(work_links(simulation))


def test_builder_construction_beam_appears_from_working_range_and_stops_on_completion() -> None:
    simulation = repair_world()
    builder = simulation.entities["builder"]
    initial_position = builder.position
    created = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.TURRET, Point(6, 4))
    )
    assert created.accepted
    simulation.advance()
    parameters = simulation.automations[created.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters)
    building = simulation.entities[parameters.site_entity_id or ""]
    assert builder.position == initial_position
    assert building_distance(builder.position, building) == 3.5
    assert tuple(work_links(simulation)) == ((builder.position, building.selection_position),)
    simulation.advance(30)
    assert building.is_complete
    assert not tuple(work_links(simulation))


def test_direct_building_repair_replaces_queued_construction() -> None:
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
    assert simulation.automations[queued.automation_id or ""].reason_code == "QUEUE_REPLACED"
    assert simulation.automations[queued.automation_id or ""].status is AutomationStatus.CANCELED
    simulation.advance(160)
    assert simulation.entities["factory"].health == 500
    assert "builder" not in simulation.assignments


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


@pytest.mark.parametrize("patrol", [False, True], ids=["direct-repair", "patrol-repair"])
def test_builder_repair_replays_combat_damage_and_restoration(patrol: bool) -> None:
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
    command = (
        CreatePatrolCommand(("builder",), PolylineTarget((Point(2.5, 8.5), Point(20.5, 8.5))))
        if patrol
        else RepairBuildingCommand(("builder",), "factory")
    )
    assert simulation.execute(command).accepted
    simulation.advance(150)
    assert simulation.entities["factory"].health == 500
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


def test_hub_radius_includes_boundary_and_excludes_unfinished_source() -> None:
    simulation = repair_world()
    # This case isolates repair capacity from the separately covered combat support response.
    assert simulation.remove_entity("hostile").accepted
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
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 18)
    app.selected_entities = {"factory"}
    with patch.object(app.ui, "small_text", wraps=app.ui.small_text) as text:
        selection.draw_selection(app, pygame.Surface(app.window_size))
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


@pytest.mark.parametrize("worker,target", [("builder", "a"), ("hub", "hub")])
def test_save_rejects_wrong_repair_target_kind(worker: str, target: str) -> None:
    simulation = repair_world()
    simulation.entities[worker].repair_target_id = target
    with pytest.raises(PersistenceError, match="invalid repair target"):
        load_simulation_data(
            {
                "schema": SAVE_SCHEMA,
                "map": simulation.game_map.to_dict(),
                "state": simulation.export_state(),
            }
        )
