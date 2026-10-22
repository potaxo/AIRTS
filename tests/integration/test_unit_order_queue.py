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
from airts.presentation.opengl_renderer import OpenGLFrameBuilder
from airts.presentation.rendering import SoftwareRenderer, order_feedback
from airts.simulation import Simulation
from airts.world.entities import OrderKind
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


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


@pytest.mark.parametrize("first_queued", [False, True])
def test_live_box_selection_and_batched_shift_clicks_keep_attack_first_fifo(
    first_queued: bool,
) -> None:
    ids = tuple(f"tank_{index:02}" for index in range(40))
    simulation = Simulation(
        load_map_data(
            {
                "id": "live_attack_fifo",
                "name": "Live attack FIFO",
                "width": 64,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": key, "kind": "light_tank", "position": [2.5 + i % 8, 10.5 + i // 8]}
                    for i, key in enumerate(ids)
                ]
                + [
                    {"id": "friendly_builder", "kind": "builder", "position": [10.5, 14.5]},
                ]
                + [
                    {"id": key, "kind": "builder", "owner": "enemy", "position": position}
                    for key, position in (
                        ("first", [26.5, 12.5]),
                        ("second", [42.5, 12.5]),
                        ("third", [52.5, 25.5]),
                    )
                ],
            }
        )
    )
    pygame.font.init()
    app = AirtsApp(simulation)
    app.ui.font = pygame.font.Font(None, 18)
    try:
        with patch("airts.presentation.app.pygame.key.get_mods", return_value=0):
            for event_type, point in (
                (pygame.MOUSEBUTTONDOWN, Point(1.5, 9.5)),
                (pygame.MOUSEBUTTONUP, Point(11.5, 16.5)),
            ):
                app._handle_event(
                    pygame.event.Event(event_type, button=1, pos=app.screen_point(point))
                )
            assert app.selected_entities == set(ids)
            shift = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LSHIFT, mod=pygame.KMOD_LSHIFT)
            if first_queued:
                app._handle_event(shift)
            app._handle_event(
                pygame.event.Event(
                    pygame.MOUSEBUTTONDOWN,
                    button=3,
                    pos=app.screen_point(simulation.entities["first"].position),
                )
            )
            simulation.advance()
            app._handle_event(shift)
            for point in (
                simulation.entities["second"].position,
                Point(25.5, 25.5),
                Point(25.7, 25.7),
                simulation.entities["third"].position,
            ):
                app._handle_event(
                    pygame.event.Event(
                        pygame.MOUSEBUTTONDOWN, button=3, pos=app.screen_point(point)
                    )
                )
            app._handle_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_LSHIFT, mod=0))
        commands = [entry["command"] for entry in simulation.command_history[-5:]]
        assert [command["type"] for command in commands] == [
            "attack",
            "attack",
            "move",
            "move",
            "attack",
        ]
        assert [command.get("queued", False) for command in commands] == [
            first_queued,
            True,
            True,
            True,
            True,
        ]
        for key in ids:
            entity = simulation.entities[key]
            assert [order.kind for order in entity.order_queue[-4:]] == [
                OrderKind.ATTACK,
                OrderKind.MOVE,
                OrderKind.MOVE,
                OrderKind.ATTACK,
            ]
            assert [order.target_entity_id for order in entity.order_queue[-4:]] == [
                "second",
                None,
                None,
                "third",
            ]
            if entity.pursue_target:
                assert entity.attack_target_id == "first"
        assert simulation.execute(
            MoveCommand(("third",), Point(56.5, 26.5), owner_id="enemy")
        ).accepted
        old_target = simulation.entities["third"].position
        simulation.advance()
        feedback = tuple(order_feedback(app, simulation.entities[ids[0]]))
        assert len(feedback) == 5
        assert [color[0] > color[1] for _, color in feedback] == [True, True, False, False, True]
        assert feedback[-1][0][-1] == simulation.entities["third"].position != old_target
        before = simulation.export_state()
        software = SoftwareRenderer()._representative_path_points(app)
        _, vertices, paths = OpenGLFrameBuilder._build_paths(app, (0, 0), 1.0)
        assert paths == app.MAX_SELECTED_PATHS
        assert vertices == 10 * paths and len(software) == 5 * paths
        assert simulation.export_state() == before
        simulation = restore(simulation)
        simulation.advance(600)
        assert all(key not in simulation.entities for key in ("first", "second", "third"))
        assert all(not simulation.entities[key].order_queue for key in ids)
    finally:
        pygame.font.quit()


@pytest.mark.parametrize("queued", [False, True])
def test_attack_fires_along_the_route_then_prioritizes_its_ordered_building(queued: bool) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "attack_route_fire",
                "name": "Attack route fire",
                "width": 48,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "tank", "kind": "light_tank", "position": [3.5, 10.5]},
                    {
                        "id": "target",
                        "kind": "resource_generator",
                        "owner": "enemy",
                        "position": [24, 9],
                    },
                    {
                        "id": "incidental",
                        "kind": "builder",
                        "owner": "enemy",
                        "position": [7.5, 12.5],
                    },
                ],
            }
        )
    )
    tank = simulation.entities["tank"]
    goal = simulation.entities["target"].selection_position
    assert simulation.execute(AttackCommand(("tank",), "target", queued=queued)).accepted
    assert simulation.execute(MoveCommand(("tank",), Point(4.5, 26.5), queued=True)).accepted
    for _ in range(100):
        simulation.advance()
        assert tank.pursue_target and tank.attack_target_id == "target"
        assert tank.order_queue[-1].kind is OrderKind.MOVE
        shots = simulation.events.query(
            event_types=frozenset({EventType.PROJECTILE_LAUNCHED}), subject_id="tank"
        )
        if any(event.details["target_id"] == "target" for event in shots):
            break
    assert any(event.details["target_id"] == "incidental" for event in shots)
    assert any(event.details["target_id"] == "target" for event in shots)
    for event in shots:
        position = event.details["position"]
        assert isinstance(position, list)
        if Point(*position).distance_to(goal) <= tank.profile.attack_range:
            assert event.details["target_id"] == "target"


@pytest.mark.parametrize(
    ("kind", "count"),
    [
        (EntityKind.SCOUT, 4),
        (EntityKind.LIGHT_TANK, 4),
        (EntityKind.LIGHT_TANK, 40),
        (EntityKind.HEAVY_TANK, 4),
        (EntityKind.LIGHT_ARTILLERY, 4),
        (EntityKind.TANK_DESTROYER, 4),
        (EntityKind.ARTILLERY, 4),
    ],
)
def test_shift_army_destroys_each_enemy_in_order_before_moving(
    kind: EntityKind, count: int
) -> None:
    ids = tuple(f"tank_{index:02}" for index in range(count))
    simulation = Simulation(
        load_map_data(
            {
                "id": "army_attack_fifo",
                "name": "Army attack FIFO",
                "width": 64,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {
                        "id": key,
                        "kind": kind.value,
                        "position": [2.5 + index % 8, 10.5 + index // 8 * 1.1],
                    }
                    for index, key in enumerate(ids)
                ]
                + [
                    {
                        "id": key,
                        "kind": "builder",
                        "owner": "enemy",
                        "position": position,
                    }
                    for key, position in (
                        ("first", [30.5, 12.5]),
                        ("second", [46.5, 12.5]),
                    )
                ],
            }
        )
    )
    app = AirtsApp(simulation)
    app.camera.zoom = 0.5
    app.selected_entities = set(ids)
    targets = (
        simulation.entities["first"].position,
        simulation.entities["second"].position,
        Point(3.5, 26.5),
    )
    origin_x, origin_y = app.map_origin
    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        for point in targets:
            x, y = app.screen_point(point)
            app._handle_mouse_down(3, (x - origin_x, y - origin_y))
            assert app.notice == "Command queued."
    assert [item["command"]["type"] for item in simulation.command_history[-3:]] == [
        "attack",
        "attack",
        "move",
    ]
    destinations = {key: simulation.entities[key].order_queue[-1].position for key in ids}
    loaded = False
    second_destroyed_tick = None
    for _ in range(600):
        simulation.advance()
        if "first" in simulation.entities:
            second = simulation.entities["second"]
            assert second.health == second.profile.max_health
            assert all(simulation.entities[key].attack_target_id in {None, "first"} for key in ids)
        elif not loaded:
            simulation = restore(simulation)
            loaded = True
        if "second" in simulation.entities:
            assert all(
                simulation.entities[key].order_queue[-1].kind is OrderKind.MOVE for key in ids
            )
        elif second_destroyed_tick is None:
            second_destroyed_tick = simulation.tick
        if all(
            not simulation.entities[key].order_queue
            and not simulation.entities[key].path
            and not simulation.entities[key].pursue_target
            for key in ids
        ):
            break
    assert loaded and second_destroyed_tick is not None
    completed = simulation.events.query(
        event_types=frozenset({EventType.MOVEMENT_COMPLETED}), since_tick=second_destroyed_tick + 1
    )
    assert {event.subject_id for event in completed} == set(ids)
    radius = max(point.distance_to(targets[-1]) for point in destinations.values()) + 1.0
    assert all(simulation.entities[key].position.distance_to(targets[-1]) <= radius for key in ids)


@pytest.fixture
def mixed_army() -> Simulation:
    simulation = Simulation(
        load_map_data(
            {
                "id": "mixed_direct_orders",
                "name": "Mixed direct orders",
                "width": 64,
                "height": 32,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "tank", "kind": "light_tank", "position": [3.5, 16.5]},
                    {"id": "builder", "kind": "builder", "position": [5.5, 16.5]},
                    {"id": "factory", "kind": "factory", "position": [16, 20]},
                    {
                        "id": "enemy",
                        "kind": "builder",
                        "owner": "enemy",
                        "position": [30.5, 12.5],
                    },
                ],
            }
        )
    )
    simulation.entities["factory"].health -= 30
    return simulation


@pytest.mark.parametrize("use_input", [False, True], ids=["command", "shift-click"])
def test_mixed_selection_filters_capabilities_without_replacing_other_work(
    mixed_army: Simulation, use_input: bool
) -> None:
    simulation = mixed_army
    ids = ("builder", "tank")
    commands = (
        MoveCommand(ids, Point(9.5, 16.5), queued=True),
        AttackCommand(ids, "enemy", queued=True),
        RepairBuildingCommand(ids, "factory", queued=True),
        MoveCommand(ids, Point(3.5, 26.5), queued=True),
    )
    app = AirtsApp(simulation)
    app.camera.zoom = 0.5
    app.selected_entities = set(ids)
    origin_x, origin_y = app.map_origin
    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        for command in commands:
            if use_input:
                target = (
                    command.target
                    if isinstance(command, MoveCommand)
                    else simulation.entities[command.target_entity_id].selection_position
                )
                x, y = app.screen_point(target)
                app._handle_mouse_down(3, (x - origin_x, y - origin_y))
                assert app.notice == "Command queued."
            else:
                assert simulation.execute(command).accepted
    assert [order.kind for order in simulation.entities["tank"].order_queue] == [
        OrderKind.ATTACK,
        OrderKind.MOVE,
    ]
    assert [order.kind for order in simulation.entities["builder"].order_queue] == [
        OrderKind.REPAIR,
        OrderKind.MOVE,
    ]
    simulation = restore(simulation)
    simulation.advance(500)
    assert "enemy" not in simulation.entities
    assert (
        simulation.entities["factory"].health == simulation.entities["factory"].profile.max_health
    )
    for key in ids:
        assert not simulation.entities[key].order_queue and not simulation.entities[key].path
        assert simulation.entities[key].position.distance_to(Point(3.5, 26.5)) < 2


@pytest.mark.parametrize("queued", [False, True])
def test_mixed_commands_leave_incapable_members_unchanged(
    mixed_army: Simulation, queued: bool
) -> None:
    simulation = mixed_army
    ids = ("builder", "tank")
    assert simulation.execute(MoveCommand(ids, Point(9.5, 16.5), queued=True)).accepted
    builder_before = simulation.export_state()["entities"]["builder"]
    assert simulation.execute(AttackCommand(ids, "enemy", queued=queued)).accepted
    assert simulation.export_state()["entities"]["builder"] == builder_before
    tank_before = simulation.export_state()["entities"]["tank"]
    assert simulation.execute(RepairBuildingCommand(ids, "factory", queued=queued)).accepted
    assert simulation.export_state()["entities"]["tank"] == tank_before


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("kind", [OrderKind.ATTACK, OrderKind.REPAIR])
def test_context_orders_without_capable_units_preserve_their_current_work(
    mixed_army: Simulation, queued: bool, kind: OrderKind
) -> None:
    simulation = mixed_army
    key, target = ("builder", "enemy") if kind is OrderKind.ATTACK else ("tank", "factory")
    assert simulation.execute(MoveCommand((key,), Point(8.5, 16.5), queued=True)).accepted
    before = simulation.export_state()["entities"][key]
    app = AirtsApp(simulation)
    app.camera.zoom = 0.5
    app.selected_entities = {key}
    origin_x, origin_y = app.map_origin
    x, y = app.screen_point(simulation.entities[target].selection_position)
    with patch(
        "airts.presentation.app.pygame.key.get_mods",
        return_value=pygame.KMOD_SHIFT if queued else 0,
    ):
        app._handle_mouse_down(3, (x - origin_x, y - origin_y))
    command = simulation.command_history[-1]["command"]
    assert command["type"] == ("attack" if kind is OrderKind.ATTACK else "repair_building")
    assert simulation.export_state()["entities"][key] == before


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
