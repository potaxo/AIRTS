from __future__ import annotations

from airts.automations import AutomationStatus
from airts.commands import CreatePatrolCommand, MoveCommand
from airts.entities import UnitState
from airts.events import EventType
from airts.geometry import Point, PolylineTarget
from airts.map_model import EntityKind, load_map_data
from airts.movement import collision_radius, unit_mass
from airts.simulation import Simulation


def _collision_simulation(
    mover_kind: EntityKind,
    blocker_kind: EntityKind,
    *,
    lane_width: int = 3,
) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "physical_collision",
                "name": "Physical Collision",
                "width": 14,
                "height": 5,
                "terrain": {
                    "default": "water",
                    "rectangles": [[0, 2 - lane_width // 2, 14, lane_width, "grass"]],
                },
                "entities": [
                    {
                        "id": "mover",
                        "kind": mover_kind.value,
                        "owner": "player",
                        "position": [2.5, 2.5],
                    },
                    {
                        "id": "blocker",
                        "kind": blocker_kind.value,
                        "owner": "player",
                        "position": [6.5, 2.5],
                    },
                ],
            }
        ),
        random_seed=31,
    )


def _advance_until_push(
    simulation: Simulation, limit: int = 80, subject_id: str | None = None
) -> None:
    for _ in range(limit):
        simulation.advance()
        if simulation.events.query(
            event_types=frozenset({EventType.UNIT_PUSHED}), subject_id=subject_id
        ):
            return
    raise AssertionError("units never entered physical pushing contact")


def test_all_units_push_but_heavier_units_accelerate_more_slowly() -> None:
    distances: dict[EntityKind, float] = {}
    for blocker_kind in (EntityKind.SCOUT, EntityKind.HEAVY_TANK):
        simulation = _collision_simulation(EntityKind.LIGHT_TANK, blocker_kind, lane_width=1)
        original = simulation.entities["blocker"].position
        assert simulation.execute(MoveCommand(("mover",), Point(12.5, 2.5))).accepted

        _advance_until_push(simulation, subject_id="blocker")
        simulation.advance(3)
        blocker = simulation.entities["blocker"]
        distances[blocker_kind] = blocker.position.distance_to(original)
        pushes = simulation.events.query(
            event_types=frozenset({EventType.UNIT_PUSHED}), subject_id="blocker"
        )
        assert len(pushes) > 1
        assert all(0 < event.details["amount"] <= 0.18 for event in pushes)
        assert blocker.position.y == original.y

    assert 0 < distances[EntityKind.HEAVY_TANK] < distances[EntityKind.SCOUT]


def test_moving_unit_is_pushed_without_losing_its_order() -> None:
    simulation = _collision_simulation(EntityKind.SCOUT, EntityKind.HEAVY_TANK, lane_width=1)
    blocker = simulation.entities["blocker"]
    blocker.path = [Point(x, 2.5) for _ in range(30) for x in (6.9, 6.1)]
    blocker.move_target = Point(6.1, 2.5)
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 2.5))).accepted

    _advance_until_push(simulation, subject_id="blocker")

    pushes = simulation.events.query(
        event_types=frozenset({EventType.UNIT_PUSHED}), subject_id="blocker"
    )
    assert any(event.details["pushed_was_moving"] for event in pushes)
    assert simulation.entities["blocker"].move_target == Point(6.1, 2.5)
    assert simulation.entities["blocker"].path


def test_moving_unit_pushes_a_congestion_stopped_unit_gradually() -> None:
    simulation = _collision_simulation(EntityKind.LIGHT_TANK, EntityKind.SCOUT, lane_width=1)
    blocker = simulation.entities["blocker"]
    blocker.state = UnitState.HOLDING
    blocker.congestion_stopped = True
    original = blocker.position
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 2.5))).accepted

    _advance_until_push(simulation, subject_id="blocker")
    simulation.advance(4)

    assert blocker.position.x > original.x
    assert blocker.position.x - original.x < 1.1
    assert blocker.state is UnitState.HOLDING
    assert blocker.congestion_stopped
    assert not blocker.path


def test_ordered_unit_pushes_stationary_blocker_and_reaches_destination() -> None:
    simulation = _collision_simulation(EntityKind.HEAVY_TANK, EntityKind.SCOUT, lane_width=1)
    destination = Point(12.5, 2.5)
    assert simulation.execute(MoveCommand(("mover",), destination)).accepted

    simulation.advance(100)

    assert simulation.events.query(
        event_types=frozenset({EventType.UNIT_PUSHED}), subject_id="blocker"
    )
    assert simulation.entities["mover"].position == destination
    assert not simulation.entities["mover"].path


def test_equal_units_give_way_and_complete_head_on_orders_without_overlap() -> None:
    simulation = _collision_simulation(EntityKind.LIGHT_TANK, EntityKind.LIGHT_TANK, lane_width=1)
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 2.5))).accepted
    assert simulation.execute(MoveCommand(("blocker",), Point(0.5, 2.5))).accepted

    minimum_separation = float("inf")
    for _ in range(80):
        simulation.advance()
        minimum_separation = min(
            minimum_separation,
            simulation.entities["mover"].position.distance_to(
                simulation.entities["blocker"].position
            ),
        )

    assert simulation.entities["mover"].position == Point(12.5, 2.5)
    assert simulation.entities["blocker"].position == Point(0.5, 2.5)
    assert all(not entity.path for entity in simulation.entities.values())
    assert minimum_separation >= collision_radius(EntityKind.LIGHT_TANK) * 2 - 1e-6


def test_line_patrol_preserves_its_assignment_while_pushing_a_blocker() -> None:
    simulation = _collision_simulation(EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK, lane_width=1)
    created = simulation.execute(
        CreatePatrolCommand(
            ("mover",),
            PolylineTarget((Point(2.5, 2.5), Point(12.5, 2.5))),
        )
    )

    reached_far_end = False
    returned_after_far_end = False
    for _ in range(240):
        simulation.advance()
        position_x = simulation.entities["mover"].position.x
        reached_far_end = reached_far_end or position_x >= 11.5
        returned_after_far_end = returned_after_far_end or (reached_far_end and position_x <= 3.5)

    automation = simulation.automations[created.automation_id or ""]
    assert automation.status is AutomationStatus.ACTIVE
    assert automation.entity_ids == ["mover"]
    assert simulation.assignments["mover"] == automation.automation_id
    assert simulation.entities["mover"].state is UnitState.PATROLLING
    assert reached_far_end
    assert returned_after_far_end


def test_continuous_physical_pushing_is_deterministic() -> None:
    first = _collision_simulation(EntityKind.SCOUT, EntityKind.HEAVY_TANK, lane_width=1)
    second = _collision_simulation(EntityKind.SCOUT, EntityKind.HEAVY_TANK, lane_width=1)
    for simulation in (first, second):
        assert simulation.execute(MoveCommand(("mover",), Point(12.5, 2.5))).accepted
        simulation.advance(60)

    assert first.snapshot() == second.snapshot()
    assert [event.to_dict() for event in first.events.events] == [
        event.to_dict() for event in second.events.events
    ]


def test_builder_uses_mobile_collision_profile() -> None:
    assert unit_mass(EntityKind.BUILDER) == unit_mass(EntityKind.SCOUT)
    assert collision_radius(EntityKind.SCOUT) <= collision_radius(EntityKind.BUILDER) < 0.4
