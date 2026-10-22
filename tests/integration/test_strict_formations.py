"""Direct move intent, cohort motion, bottlenecks and saved formation ownership."""

from unittest.mock import patch

import pygame
import pytest

from airts.adapters.persistence import PersistenceError, capture_save, load_simulation_data
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.commands import (
    AttackCommand,
    MoveCommand,
    RemoveEntityCommand,
    SetFormationCommand,
    command_from_dict,
    command_to_dict,
)
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.world.map import load_map_data


def army(*, narrow: bool = False) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "strict_formation",
                "name": "Strict formation",
                "width": 64,
                "height": 40,
                "terrain": {
                    "default": "grass",
                    "rectangles": (
                        [[26, 0, 3, 19, "water"], [26, 21, 3, 19, "water"]] if narrow else []
                    ),
                },
                "entities": [
                    {"id": "a", "kind": "scout", "position": [5.5, 16.5]},
                    {"id": "b", "kind": "light_tank", "position": [7.5, 16.5]},
                    {"id": "c", "kind": "heavy_tank", "position": [5.5, 22.5]},
                    {"id": "enemy", "kind": "builder", "owner": "enemy", "position": [59.5, 32.5]},
                ],
            }
        )
    )


IDS = ("a", "b", "c")


def saved(simulation: Simulation) -> dict[str, object]:
    value = capture_save(simulation)
    return {
        "schema": value.schema,
        "map": value.game_map,
        "state": value.state | {"events": [e.to_dict() for e in value.events]},
    }


@pytest.mark.parametrize("target", [Point(12.5, 20.5), Point(49.5, 20.5)])
def test_default_move_always_gathers_and_strict_move_preserves_offsets(target: Point) -> None:
    simulation = army()
    assert simulation.execute(MoveCommand(IDS, target)).accepted
    assert max(simulation.entities[k].move_target.distance_to(target) for k in IDS) < 2
    assert simulation.execute(SetFormationCommand(IDS, True)).accepted
    assert simulation.execute(MoveCommand(IDS, target)).accepted
    for key in IDS:
        entity = simulation.entities[key]
        assert entity.formation_offset is not None and entity.move_target is not None
        assert entity.move_target == Point(
            target.x + entity.formation_offset.x, target.y + entity.formation_offset.y
        )


def test_strict_mixed_army_moves_as_one_at_slowest_speed_and_replays() -> None:
    simulation = army()
    command = SetFormationCommand(IDS, True)
    assert command_from_dict(command_to_dict(command)) == command
    assert simulation.execute(command).accepted
    positions = {key: simulation.entities[key].position for key in IDS}
    slowest = min(simulation.entities[key].speed for key in IDS)
    assert simulation.execute(MoveCommand(IDS, Point(49.5, 20.5))).accepted
    for tick in range(35):
        simulation.advance()
        for key in IDS:
            entity = simulation.entities[key]
            assert entity.position.distance_to(positions[key]) == pytest.approx(
                slowest * Simulation.TICK_SECONDS * (tick + 1), abs=1e-7
            )
        a, b = (simulation.entities[key] for key in IDS[:2])
        assert b.position.x - a.position.x == pytest.approx(2.0)
        assert b.position.y == pytest.approx(a.position.y)
    restored = load_simulation_data(saved(simulation))
    for world in (simulation, restored):
        world.advance(300)
        assert all(not world.entities[key].path for key in IDS)
    assert simulation.snapshot() == restored.snapshot()
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()


def test_strict_cohort_uses_slowest_terrain_adjusted_speed() -> None:
    data = army().game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[5, 22, 3, 1, "forest"]]}
    simulation = Simulation(load_map_data(data))
    assert simulation.execute(SetFormationCommand(IDS, True)).accepted
    assert simulation.execute(MoveCommand(IDS, Point(49.5, 20.5))).accepted
    before = {key: simulation.entities[key].position for key in IDS}
    cap = simulation.entities["c"].speed * Simulation.TICK_SECONDS / 1.5
    simulation.advance()
    for key in IDS:
        assert simulation.entities[key].position.distance_to(before[key]) <= cap + 1e-9


def test_blocked_strict_destination_rejects_without_collapsing_or_replacing_work() -> None:
    simulation = army(narrow=True)
    assert simulation.execute(SetFormationCommand(IDS, True)).accepted
    assert simulation.execute(MoveCommand(IDS, Point(12.5, 20.5))).accepted
    before = {key: simulation.entities[key].to_dict() for key in IDS}
    result = simulation.execute(MoveCommand(IDS, Point(27.5, 20.5)))
    assert not result.accepted and "FORMATION_DESTINATION_BLOCKED" in result.reason
    assert {key: simulation.entities[key].to_dict() for key in IDS} == before


def test_overlapping_mixed_modes_reject_atomically_until_selection_is_one_cohort() -> None:
    data = army().game_map.to_dict()
    data["entities"][1]["position"] = [6.3, 16.5]
    simulation = Simulation(load_map_data(data))
    assert simulation.execute(SetFormationCommand(("a", "b"), True)).accepted
    before = {key: simulation.entities[key].to_dict() for key in IDS}
    result = simulation.execute(MoveCommand(IDS, Point(35.5, 20.5)))
    assert not result.accepted and "FORMATION_COHORTS_OVERLAP" in result.reason
    assert {key: simulation.entities[key].to_dict() for key in IDS} == before
    assert simulation.execute(SetFormationCommand(IDS, True)).accepted
    assert simulation.execute(MoveCommand(IDS, Point(35.5, 20.5))).accepted


def test_internal_commands_use_distinct_barriers_for_repeated_destinations() -> None:
    simulation = army()
    assert simulation.execute(SetFormationCommand(IDS, True)).accepted
    assert simulation.execute(MoveCommand(IDS, Point(35.5, 20.5))).accepted
    for _ in range(2):
        assert simulation._execute_command(
            MoveCommand(IDS, Point(35.5, 20.5), queued=True), record=False
        ).accepted
    unit = simulation.entities["a"]
    assert len({unit.formation_leg, *(order.formation_leg for order in unit.order_queue)}) == 3
    simulation.advance(400)
    assert all(
        not simulation.entities[key].path and not simulation.entities[key].order_queue
        for key in IDS
    )


@pytest.mark.parametrize("final", [Point(47.5, 20.5), Point(51.5, 20.5)], ids=["repeat", "next"])
def test_narrow_passage_reforms_and_fifo_waits_for_the_cohort_after_load(final: Point) -> None:
    simulation = army(narrow=True)
    assert simulation.execute(SetFormationCommand(IDS, True)).accepted
    assert simulation.execute(MoveCommand(IDS, Point(47.5, 20.5))).accepted
    first_leg = simulation.entities["a"].formation_leg
    assert simulation.execute(MoveCommand(IDS, final, queued=True)).accepted
    next_leg = simulation.entities["a"].order_queue[0].formation_leg
    assert first_leg != next_leg
    simulation.advance(40)
    simulation = load_simulation_data(saved(simulation))
    for _ in range(1200):
        simulation.advance()
        assert not (
            any(
                simulation.entities[k].formation_leg == first_leg and simulation.entities[k].path
                for k in IDS
            )
            and any(simulation.entities[k].formation_leg == next_leg for k in IDS)
        )
        if all(
            not simulation.entities[k].path and not simulation.entities[k].order_queue for k in IDS
        ):
            break
    assert all(
        not simulation.entities[k].path and not simulation.entities[k].order_queue for k in IDS
    )
    for key in IDS:
        entity = simulation.entities[key]
        assert entity.formation_offset is not None
        goal = Point(final.x + entity.formation_offset.x, final.y + entity.formation_offset.y)
        assert entity.position.distance_to(goal) < 0.05


@pytest.mark.parametrize("narrow", [False, True], ids=["open", "passage"])
def test_large_strict_queue_admits_batches_without_splitting_the_cohort(narrow: bool) -> None:
    data = army(narrow=narrow).game_map.to_dict()
    ids = tuple(f"tank_{i:02}" for i in range(40))
    data["entities"] = [
        {"id": key, "kind": "light_tank", "position": [3.5 + i % 8, 8.5 + i // 8]}
        for i, key in enumerate(ids)
    ]
    simulation = Simulation(load_map_data(data))
    assert simulation.execute(SetFormationCommand(ids, True)).accepted
    assert simulation.execute(MoveCommand(ids, Point(40.5, 20.5))).accepted
    assert simulation.execute(MoveCommand(ids, Point(45.5, 20.5), queued=True)).accepted
    second = simulation.entities[ids[0]].order_queue[-1].formation_leg
    for _ in range(400):
        simulation.advance()
        assert simulation.automation_route_count <= Simulation.TOTAL_AUTOMATION_ROUTE_BUDGET
        activated = [key for key in ids if simulation.entities[key].formation_leg == second]
        if 0 < len(activated) < len(ids):
            for key in activated:
                entity = simulation.entities[key]
                assert entity.formation_offset is not None
                assert (
                    entity.position.distance_to(
                        Point(40.5 + entity.formation_offset.x, 20.5 + entity.formation_offset.y)
                    )
                    < 0.01
                )
        if all(
            not simulation.entities[key].path and not simulation.entities[key].order_queue
            for key in ids
        ):
            break
    assert all(
        not simulation.entities[key].path and not simulation.entities[key].order_queue
        for key in ids
    ), [
        (
            key,
            round(simulation.entities[key].position.x, 2),
            round(simulation.entities[key].position.y, 2),
            len(simulation.entities[key].path),
            simulation.entities[key].path[:1],
        )
        for key in ids
    ]
    simulation.advance(20)
    for key in ids:
        entity = simulation.entities[key]
        assert entity.formation_offset is not None
        assert (
            entity.position.distance_to(
                Point(45.5 + entity.formation_offset.x, 20.5 + entity.formation_offset.y)
            )
            < 0.05
        )


def test_cancel_and_death_release_speed_and_attack_keeps_its_existing_semantics() -> None:
    simulation = army()
    assert simulation.execute(SetFormationCommand(IDS, True)).accepted
    assert simulation.execute(MoveCommand(IDS, Point(49.5, 20.5))).accepted
    simulation.advance()
    assert simulation.execute(RemoveEntityCommand("c")).accepted
    before = simulation.entities["a"].position
    simulation.advance()
    assert simulation.entities["a"].position.distance_to(before) == pytest.approx(
        simulation.entities["b"].speed * Simulation.TICK_SECONDS
    )
    assert simulation.execute(SetFormationCommand(("a", "b"), False)).accepted
    assert simulation.execute(MoveCommand(("a", "b"), Point(49.5, 20.5))).accepted
    before = simulation.entities["a"].position
    simulation.advance()
    assert (
        simulation.entities["a"].position.distance_to(before)
        > simulation.entities["b"].speed * Simulation.TICK_SECONDS
    )
    assert simulation.execute(AttackCommand(("a", "b"), "enemy")).accepted
    assert all(
        simulation.entities[k].pursue_target and simulation.entities[k].formation_leg is None
        for k in ("a", "b")
    )


def test_command_bar_toggle_shows_active_border_and_preserves_queued_work() -> None:
    simulation = army()
    app = AirtsApp(simulation)
    app.selected_entities = set(IDS)
    assert simulation.execute(MoveCommand(IDS, Point(35.5, 20.5))).accepted
    assert simulation.execute(AttackCommand(IDS, "enemy", queued=True)).accepted
    queued = {key: list(simulation.entities[key].order_queue) for key in IDS}
    pygame.font.init()
    try:
        app.ui.small_font = pygame.font.Font(None, 18)
        surface = pygame.Surface(app.window_size)
        for index, enabled in enumerate((False, True, False)):
            with patch.object(app.ui, "button", wraps=app.ui.button) as button:
                app._draw_command_bar(surface)
            call = next(call for call in button.call_args_list if call.args[2] == "Keep formation")
            assert call.kwargs["active"] is enabled
            rectangle = next(rect for rect, action in app._command_buttons if action == "formation")
            if index == 2:
                break
            app._handle_command_click(rectangle.center)
        assert all(simulation.entities[key].formation_group is None for key in IDS)
        assert {key: simulation.entities[key].order_queue for key in IDS} == queued
    finally:
        pygame.font.quit()


@pytest.mark.parametrize(
    "field,value",
    [("formation_offset", None), ("formation_leg", "bad"), ("formation_goal", [float("inf"), 2])],
)
def test_malformed_saved_formation_is_rejected(field: str, value: object) -> None:
    simulation = army()
    assert simulation.execute(SetFormationCommand(IDS, True)).accepted
    snapshot = saved(simulation)
    snapshot["state"]["entities"]["a"][field] = value
    with pytest.raises(PersistenceError):
        load_simulation_data(snapshot)
