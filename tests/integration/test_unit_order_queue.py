"""Player FIFO orders cross movement, combat and builder repair boundaries."""

from unittest.mock import patch

import pygame
import pytest
from tests.integration.scenarios import builder_factory_simulation

from airts.adapters.persistence import (
    SAVE_SCHEMA,
    PersistenceError,
    capture_save,
    load_simulation_data,
)
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.commands import (
    AttackCommand,
    Command,
    HoldPositionCommand,
    MoveCommand,
    ReclaimBuildingCommand,
    RemoveEntityCommand,
    RepairBuildingCommand,
    StopCommand,
    command_from_dict,
    command_to_dict,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.world.entities import OrderKind
from airts.world.map import load_map_data


def restore(simulation: Simulation) -> Simulation:
    saved = capture_save(simulation)
    return load_simulation_data(
        {
            "schema": saved.schema,
            "map": saved.game_map,
            "state": saved.state | {"events": [event.to_dict() for event in saved.events]},
        }
    )


def damaged_buildings() -> Simulation:
    simulation = Simulation(builder_factory_simulation().game_map)
    for key in ("factory", "factory_2"):
        target = simulation.entities[key]
        health = target.health
        assert simulation.execute(ReclaimBuildingCommand(("builder_2",), key)).accepted
        for _ in range(100):
            simulation.advance()
            if target.health < health:
                break
        assert 0 < target.health < health
        assert simulation.execute(StopCommand(("builder_2",))).accepted
    return simulation


def test_builder_finishes_movement_then_repairs_each_building_then_moves_and_restores() -> None:
    simulation = damaged_buildings()
    builder = simulation.entities["builder"]
    points = (Point(12.5, 15.5), Point(16.5, 15.5), Point(18.5, 15.5))
    for point in points[:2]:
        assert simulation.execute(MoveCommand(("builder",), point, queued=True)).accepted
    for target in ("factory", "factory_2"):
        command = RepairBuildingCommand(("builder",), target, queued=True)
        assert command_from_dict(command_to_dict(command)) == command
        assert simulation.execute(command).accepted
    assert simulation.execute(MoveCommand(("builder",), points[2], queued=True)).accepted
    assert [order.kind for order in builder.order_queue] == [
        OrderKind.MOVE,
        OrderKind.REPAIR,
        OrderKind.REPAIR,
        OrderKind.MOVE,
    ]
    loaded = restore(simulation)
    restored_while_repairing = False
    for _ in range(300):
        simulation.advance()
        loaded.advance()
        if builder.repair_target_id is not None and not restored_while_repairing:
            assert simulation.export_state() == loaded.export_state()
            loaded = restore(simulation)
            restored_while_repairing = True
        if (
            simulation.entities["factory_2"].health
            == simulation.entities["factory_2"].profile.max_health
        ):
            assert (
                simulation.entities["factory"].health
                == simulation.entities["factory"].profile.max_health
            )
    completed = simulation.events.query(event_types=frozenset({EventType.REPAIR_COMPLETED}))
    assert [event.subject_id for event in reversed(completed)] == ["factory", "factory_2"]
    assert restored_while_repairing and not builder.order_queue and builder.repair_target_id is None
    assert builder.position == points[-1]
    assert simulation.export_state() == loaded.export_state()
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


def attack_world() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "attack_fifo",
                "name": "Attack FIFO",
                "width": 64,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "tank", "kind": "heavy_tank", "position": [3.5, 23.5]},
                    {
                        "id": "enemy",
                        "kind": "resource_generator",
                        "owner": "enemy",
                        "position": [32, 12],
                    },
                ],
            }
        )
    )


def test_army_finishes_waypoints_then_attack_then_remaining_move_with_save_and_replay() -> None:
    simulation = attack_world()
    tank = simulation.entities["tank"]
    waypoints = (Point(8.5, 23.5), Point(12.5, 23.5))
    final = Point(4.5, 26.5)
    for point in waypoints:
        assert simulation.execute(MoveCommand(("tank",), point, queued=True)).accepted
    attack = AttackCommand(("tank",), "enemy", queued=True)
    assert command_from_dict(command_to_dict(attack)) == attack
    assert simulation.execute(attack).accepted
    assert simulation.execute(MoveCommand(("tank",), final, queued=True)).accepted
    loaded = restore(simulation)
    restored_in_combat = False
    for _ in range(500):
        simulation.advance()
        loaded.advance()
        if tank.pursue_target and not restored_in_combat:
            reached = [
                event.details["position"]
                for event in simulation.events.query(
                    event_types=frozenset({EventType.MOVEMENT_COMPLETED}),
                    subject_id="tank",
                )
            ]
            assert list(reversed(reached))[:2] == [[point.x, point.y] for point in waypoints]
            assert simulation.export_state() == loaded.export_state()
            loaded = restore(simulation)
            restored_in_combat = True
        if "enemy" in simulation.entities:
            assert tank.move_target != final
    assert restored_in_combat and "enemy" not in simulation.entities
    assert not tank.order_queue and not tank.path and tank.position == final
    assert simulation.export_state() == loaded.export_state()
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


def test_removed_queued_target_is_diagnosed_without_losing_following_movement() -> None:
    simulation = attack_world()
    assert simulation.execute(MoveCommand(("tank",), Point(10.5, 24.5), queued=True)).accepted
    assert simulation.execute(AttackCommand(("tank",), "enemy", queued=True)).accepted
    final = Point(5.5, 26.5)
    assert simulation.execute(MoveCommand(("tank",), final, queued=True)).accepted
    before = list(simulation.entities["tank"].order_queue)
    assert not simulation.execute(AttackCommand(("tank",), "missing", queued=True)).accepted
    assert simulation.entities["tank"].order_queue == before
    assert simulation.execute(RemoveEntityCommand("enemy")).accepted
    assert simulation.events.query(event_types=frozenset({EventType.ORDER_SKIPPED}))
    simulation.advance(150)
    assert simulation.entities["tank"].position == final
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


@pytest.mark.parametrize("queued_attack", [False, True])
def test_move_appended_to_an_active_attack_waits_for_target_destruction(
    queued_attack: bool,
) -> None:
    simulation = attack_world()
    assert simulation.execute(AttackCommand(("tank",), "enemy", queued=queued_attack)).accepted
    simulation.advance()
    tank = simulation.entities["tank"]
    assert tank.pursue_target and not tank.order_queue
    final = Point(5.5, 26.5)
    assert simulation.execute(MoveCommand(("tank",), final, queued=True)).accepted
    assert tank.pursue_target and tank.attack_target_id == "enemy"
    simulation.advance(500)
    assert "enemy" not in simulation.entities and tank.position == final


def test_repair_completed_by_someone_else_is_skipped_then_movement_continues() -> None:
    simulation = damaged_buildings()
    assert simulation.execute(MoveCommand(("builder",), Point(10.5, 17.5), queued=True)).accepted
    assert simulation.execute(
        RepairBuildingCommand(("builder",), "factory_2", queued=True)
    ).accepted
    final = Point(16.5, 17.5)
    assert simulation.execute(MoveCommand(("builder",), final, queued=True)).accepted
    assert simulation.execute(RepairBuildingCommand(("builder_2",), "factory_2")).accepted
    simulation.advance(200)
    skipped = simulation.events.query(
        event_types=frozenset({EventType.ORDER_SKIPPED}), subject_id="builder"
    )
    assert len(skipped) == 1 and skipped[0].details["reason"] == "TARGET_NOT_DAMAGED"
    assert simulation.entities["builder"].position == final
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


@pytest.mark.parametrize(
    "command",
    [
        StopCommand(("tank",)),
        HoldPositionCommand(("tank",)),
        MoveCommand(("tank",), Point(4.5, 24.5)),
    ],
)
def test_ordinary_control_replaces_all_remaining_order_types(command: Command) -> None:
    simulation = attack_world()
    assert simulation.execute(MoveCommand(("tank",), Point(10.5, 24.5), queued=True)).accepted
    assert simulation.execute(AttackCommand(("tank",), "enemy", queued=True)).accepted
    assert simulation.execute(command).accepted
    assert not simulation.entities["tank"].order_queue


def test_shift_clicking_a_damaged_building_appends_repair_before_next_move() -> None:
    simulation = damaged_buildings()
    app = AirtsApp(simulation)
    app.camera.zoom = 0.5
    app.selected_entities = {"builder"}
    targets = (
        Point(12.5, 15.5),
        simulation.entities["factory"].selection_position,
        simulation.entities["factory_2"].selection_position,
        Point(18.5, 15.5),
    )
    origin_x, origin_y = app.map_origin
    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        for point in targets:
            x, y = app.screen_point(point)
            app._handle_mouse_down(3, (x - origin_x, y - origin_y))
    commands = [item["command"] for item in simulation.command_history[-4:]]
    assert [command["type"] for command in commands] == [
        "move",
        "repair_building",
        "repair_building",
        "move",
    ]
    assert all(command["queued"] for command in commands)


@pytest.mark.parametrize("first", [Point(14.5, 2.5), Point(22.5, 2.5)])
def test_queued_gathering_at_a_building_uses_a_reachable_shared_entry(first: Point) -> None:
    simulation = Simulation(builder_factory_simulation().game_map)
    assert simulation.execute(MoveCommand(("builder",), first, queued=True)).accepted
    target = simulation.entities["factory_2"].selection_position
    assert simulation.execute(MoveCommand(("builder",), target, queued=True)).accepted
    order = simulation.entities["builder"].order_queue[-1]
    assert order.group_goal is not None
    assert simulation.game_map.cell_for(order.group_goal) not in simulation._building_cells()
    simulation.advance(180)
    builder = simulation.entities["builder"]
    assert not builder.order_queue and not builder.path
    assert builder.position == order.position


def test_queued_work_admission_leaves_route_capacity_and_shares_the_repair_field() -> None:
    ids = tuple(f"builder_{index:02}" for index in range(32))
    simulation = Simulation(
        load_map_data(
            {
                "id": "queued_repair_budget",
                "name": "Queued repair budget",
                "width": 64,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {
                        "id": key,
                        "kind": "builder",
                        "position": [2.5 + index % 8, 10.5 + index // 8 * 1.2],
                    }
                    for index, key in enumerate(ids)
                ]
                + [{"id": "factory", "kind": "factory", "position": [45, 10]}],
            }
        )
    )
    simulation.entities["factory"].health -= 100
    assert simulation.execute(RepairBuildingCommand(ids, "factory", queued=True)).accepted
    simulation.advance()
    routed = sum(bool(simulation.entities[key].path) for key in ids)
    assert (
        0 < routed == simulation.automation_route_count <= Simulation.TOTAL_AUTOMATION_ROUTE_BUDGET
    )
    assert simulation.navigation_field_build_count == 1


@pytest.mark.parametrize(
    "order",
    [
        {"kind": "unknown", "position": [12, 12]},
        {"kind": "attack", "position": [12, 12]},
        {"kind": "move", "position": [12, 12], "target_entity_id": "factory"},
        {"kind": "repair", "position": [12, 12], "target_entity_id": "missing"},
        {"kind": "repair", "position": [12, 12], "target_entity_id": "scout"},
        {"kind": "attack", "position": [12, 12], "target_entity_id": "factory"},
        {
            "kind": "repair",
            "position": [12, 12],
            "target_entity_id": "factory",
            "group_goal": [12, 12],
        },
    ],
)
def test_corrupt_order_kind_target_and_capability_are_rejected(order: dict[str, object]) -> None:
    simulation = damaged_buildings()
    state = simulation.export_state()
    state["entities"]["builder"]["order_queue"] = [order]
    with pytest.raises(PersistenceError):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )
