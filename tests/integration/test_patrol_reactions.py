"""Patrol encounters, bounded pursuit, builder work, and continuation."""

from pathlib import Path

import pytest

from airts.adapters.persistence import PersistenceError, load_simulation, save_simulation
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import patrol_parameters
from airts.contracts.commands import (
    CancelAutomationCommand,
    CreatePatrolCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import (
    Point,
    PointTarget,
    PolygonRegion,
    PolylineTarget,
    SpatialTarget,
    distance_to_target,
    rectangle_region,
)
from airts.simulation import Simulation
from airts.systems.automation_execution import AutomationQueries
from airts.world.entities import UnitState
from airts.world.map import load_map_data


def patrol_simulation(kind: str = "scout", *, enemy_x: float = 11.5) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "patrol_encounters",
                "name": "Patrol encounters",
                "width": 50,
                "height": 30,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "unit", "kind": kind, "owner": "player", "position": [4.5, 4.5]},
                    {
                        "id": "enemy",
                        "kind": "builder",
                        "owner": "enemy",
                        "position": [enemy_x, 4.5],
                    },
                    {
                        "id": "building",
                        "kind": "resource_generator",
                        "owner": "player",
                        "position": [12, 8],
                    },
                ],
            }
        )
    )


ROUTE = PolylineTarget((Point(4.5, 4.5), Point(20.5, 4.5)))


def test_patrol_chases_beyond_weapon_range_kills_and_replays() -> None:
    simulation = patrol_simulation()
    result = simulation.execute(CreatePatrolCommand(("unit",), ROUTE))
    assert result.accepted
    simulation.advance()
    unit = simulation.entities["unit"]
    assert unit.pursue_target and unit.attack_target_id == "enemy"
    assert unit.path
    assert (
        unit.position.distance_to(simulation.entities["enemy"].position)
        > unit.kind.profile.attack_range
    )
    assert simulation.assignments["unit"] == result.automation_id
    simulation.advance(200)
    assert "enemy" not in simulation.entities
    assert not unit.pursue_target
    assert unit.state is UnitState.PATROLLING
    assert unit.position.distance_to(Point(4.5, 4.5)) > 5
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


@pytest.mark.parametrize(
    "target, fleeing",
    [
        (PointTarget(Point(8, 8), radius=2), Point(18, 8)),
        (PolylineTarget((Point(4, 8), Point(35, 8))), Point(15, 16)),
        (rectangle_region(Point(4, 4), Point(35, 12)), Point(15, 20)),
    ],
    ids=["point-disk", "long-line", "filled-area"],
)
def test_patrol_leash_uses_geometry_and_returns_after_enemy_flees(
    target: SpatialTarget,
    fleeing: Point,
    tmp_path: Path,
) -> None:
    simulation = patrol_simulation(enemy_x=30.5)
    result = simulation.execute(CreatePatrolCommand(("unit",), target))
    simulation.advance(2)
    unit = simulation.entities["unit"]
    interrupted_goal = unit.move_target
    assert interrupted_goal is not None
    simulation.entities["enemy"].position = Point(unit.position.x + 6, unit.position.y)
    simulation.advance()
    assert unit.pursue_target
    cursor = patrol_parameters(simulation.automations[result.automation_id or ""]).waypoint_indices[
        "unit"
    ]
    save_path = tmp_path / "chasing.json"
    save_simulation(simulation, save_path)
    restored = load_simulation(save_path)
    for current in (simulation, restored):
        current.entities["enemy"].position = fleeing
        current.advance()
        patroller = current.entities["unit"]
        assert not patroller.pursue_target
        assert patroller.state is UnitState.PATROLLING
        assert patroller.move_target == interrupted_goal
        parameters = patrol_parameters(current.automations[result.automation_id or ""])
        assert parameters.waypoint_indices["unit"] == (cursor + 1) % len(parameters.waypoints)
    assert restored.export_state() == simulation.export_state()


def test_patrol_does_not_acquire_enemies_outside_vision_or_leash() -> None:
    simulation = patrol_simulation(enemy_x=20.5)
    assert simulation.execute(CreatePatrolCommand(("unit",), ROUTE)).accepted
    simulation.advance()
    assert not simulation.entities["unit"].pursue_target
    # Visible at the deployment position, but far from the actual patrol area.
    simulation = patrol_simulation(enemy_x=5.5)
    assert simulation.execute(CreatePatrolCommand(("unit",), PointTarget(Point(30, 20)))).accepted
    simulation.advance()
    assert not simulation.entities["unit"].pursue_target
    assert simulation.entities["unit"].path


def test_patrol_reactions_cover_members_deferred_by_route_budget() -> None:
    ids = tuple(f"scout_{index:02}" for index in range(40))
    simulation = Simulation(
        load_map_data(
            {
                "id": "group_patrol_encounter",
                "name": "Group patrol encounter",
                "width": 50,
                "height": 30,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": key, "kind": "scout", "position": [6.5 + i % 5, 5.5 + i // 5]}
                    for i, key in enumerate(ids)
                ]
                + [{"id": "enemy", "kind": "builder", "owner": "enemy", "position": [11.5, 9.5]}],
            }
        )
    )
    assert simulation.execute(
        CreatePatrolCommand(ids, PointTarget(Point(8.5, 8.5), radius=3))
    ).accepted
    simulation.advance()
    assert all(simulation.entities[key].pursue_target for key in ids)
    assert any(not simulation.entities[key].path for key in ids)
    simulation.entities["enemy"].position = Point(40.5, 20.5)
    simulation.advance()
    assert all(not simulation.entities[key].pursue_target for key in ids)


def test_builder_encounters_repairs_and_resumes_interrupted_leg(tmp_path: Path) -> None:
    simulation = patrol_simulation("builder")
    building = simulation.entities["building"]
    building.health -= 80
    result = simulation.execute(CreatePatrolCommand(("unit",), ROUTE))
    assert result.accepted
    simulation.advance()
    assert simulation.entities["unit"].repair_target_id is None
    for _ in range(50):
        simulation.advance()
        if simulation.entities["unit"].repair_target_id is not None:
            break
    builder = simulation.entities["unit"]
    assert builder.repair_target_id == "building"
    assert simulation.assignments["unit"] == result.automation_id
    save_path = tmp_path / "repairing.json"
    save_simulation(simulation, save_path)
    restored = load_simulation(save_path)
    simulation.advance(100)
    restored.advance(100)
    assert building.health == building.kind.profile.max_health
    assert builder.repair_target_id is None
    assert builder.state is UnitState.PATROLLING
    assert any(
        event.details["position"] == [20.5, 4.5]
        for event in simulation.events.query(
            event_types=frozenset({EventType.MOVEMENT_COMPLETED}), subject_id="unit"
        )
    )
    assert restored.export_state() == simulation.export_state()
    building.health -= 20
    simulation.advance(100)
    assert building.health == building.kind.profile.max_health
    assert len(simulation.events.query(event_types=frozenset({EventType.REPAIR_COMPLETED}))) == 2


@pytest.mark.parametrize("ineligible", ["healthy", "enemy", "unfinished", "mobile"])
def test_builder_patrol_only_repairs_eligible_buildings(ineligible: str) -> None:
    simulation = patrol_simulation("builder")
    building = simulation.entities["building"]
    building.health -= 20
    if ineligible == "healthy":
        building.health = building.kind.profile.max_health
    elif ineligible == "enemy":
        building.owner_id = "enemy"
    elif ineligible == "unfinished":
        building.construction_progress = 50
    else:
        simulation.entities["unit"].health -= 20
        building.health = building.kind.profile.max_health
    assert simulation.execute(CreatePatrolCommand(("unit",), ROUTE)).accepted
    simulation.advance(80)
    assert simulation.entities["unit"].repair_target_id is None
    assert not simulation.events.query(event_types=frozenset({EventType.REPAIR_STARTED}))


def test_paused_builder_patrol_cannot_restore_active_repair(tmp_path: Path) -> None:
    simulation = patrol_simulation("builder")
    simulation.entities["building"].health -= 100
    result = simulation.execute(CreatePatrolCommand(("unit",), ROUTE))
    assert simulation.execute(PauseAutomationCommand(result.automation_id or "")).accepted
    simulation.entities["unit"].repair_target_id = "building"
    path = tmp_path / "invalid-paused-repair.json"
    save_simulation(simulation, path)
    with pytest.raises(PersistenceError, match="repair conflicts"):
        load_simulation(path)


@pytest.mark.parametrize("kind", ["scout", "builder"])
@pytest.mark.parametrize("control", ["pause", "cancel", "move", "removed-target"])
def test_patrol_reaction_obeys_control_and_target_removal(kind: str, control: str) -> None:
    simulation = patrol_simulation(kind)
    target = ROUTE
    if kind == "builder":
        simulation.entities["building"].health -= 100
        simulation.entities["unit"].position = Point(9.5, 8.5)
        target = PolylineTarget((Point(9.5, 8.5), Point(20.5, 8.5)))
    result = simulation.execute(CreatePatrolCommand(("unit",), target))
    simulation.advance()
    unit = simulation.entities["unit"]
    assert unit.pursue_target or unit.repair_target_id is not None
    automation_id = result.automation_id or ""
    if control == "pause":
        assert simulation.execute(PauseAutomationCommand(automation_id)).accepted
    elif control == "cancel":
        assert simulation.execute(CancelAutomationCommand(automation_id)).accepted
    elif control == "move":
        assert simulation.execute(MoveCommand(("unit",), Point(2.5, 20.5))).accepted
    else:
        assert simulation.remove_entity("enemy" if kind == "scout" else "building").accepted
    simulation.advance(2)
    assert not unit.pursue_target
    assert unit.repair_target_id is None
    if control == "pause":
        assert unit.state is UnitState.IDLE
        assert simulation.execute(ResumeAutomationCommand(automation_id)).accepted
        simulation.advance()
        assert unit.pursue_target or unit.repair_target_id is not None
    elif control == "removed-target":
        assert unit.state is UnitState.PATROLLING


@pytest.mark.parametrize(
    "target",
    [
        PointTarget(Point(40.5, 40.5), radius=10),
        PolylineTarget((Point(15, 25), Point(75, 25), Point(75, 80))),
        PolygonRegion(
            (
                Point(25, 25),
                Point(85, 25),
                Point(85, 45),
                Point(45, 45),
                Point(45, 85),
                Point(25, 85),
            )
        ),
    ],
    ids=["disk", "bent-route", "concave-area"],
)
def test_patrol_broadphase_and_shared_queries_match_exhaustive_target_selection(
    target: SpatialTarget,
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "query_geometry",
                "name": "Query geometry",
                "width": 120,
                "height": 120,
                "terrain": {"default": "grass"},
                "entities": [
                    {
                        "id": f"enemy_{i:03}",
                        "kind": "builder",
                        "owner": "enemy",
                        "position": [2.5 + (i % 20) * 5, 2.5 + (i // 20) * 5],
                    }
                    for i in range(400)
                ],
            }
        )
    )
    queries = AutomationQueries()
    eligible, index = queries.patrol_index(simulation, "player", target)
    expected = {
        key: entity.selection_position
        for key, entity in simulation.entities.items()
        if distance_to_target(entity.selection_position, target) <= Simulation.PATROL_PURSUIT_RADIUS
    }
    assert eligible == expected
    centers = (Point(16, 30), Point(80, 30), Point(45, 85), Point(90, 95))
    actual = index.nearest_many(tuple((point, 12.0) for point in centers))
    oracle = tuple(
        min(
            (key for key, enemy in expected.items() if point.distance_to(enemy) <= 12),
            key=lambda key: (point.distance_to(expected[key]), key),
            default=None,
        )
        for point in centers
    )
    assert actual == oracle
    cached, cached_index = queries.patrol_index(simulation, "player", target)
    assert (
        cached == expected
        and cached_index.nearest_many(tuple((point, 12.0) for point in centers)) == oracle
    )
