from __future__ import annotations

from airts.automations import AutomationStatus
from airts.commands import CreatePatrolCommand, MoveCommand
from airts.entities import UnitState
from airts.events import EventType
from airts.geometry import Point, PolylineTarget
from airts.map_model import EntityKind, load_map_data
from airts.movement import collision_radius
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
        assert all(0 < event.details["amount"] <= 0.12 for event in pushes)
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
    assert blocker.position.x - original.x < 0.8
    assert blocker.state is UnitState.HOLDING
    assert blocker.congestion_stopped
    assert not blocker.path


def test_equal_units_pushing_head_on_stalemate_without_overlap_or_bounce() -> None:
    simulation = _collision_simulation(EntityKind.LIGHT_TANK, EntityKind.LIGHT_TANK, lane_width=1)
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 2.5))).accepted
    assert simulation.execute(MoveCommand(("blocker",), Point(0.5, 2.5))).accepted

    simulation.advance(30)
    first = simulation.entities["mover"]
    second = simulation.entities["blocker"]
    separation = first.position.distance_to(second.position)

    assert first.path and second.path
    assert separation >= (collision_radius(EntityKind.LIGHT_TANK) * 2 - 1e-9)
    pushes = simulation.events.query(event_types=frozenset({EventType.UNIT_PUSHED}))
    assert all(event.details["amount"] < 0.02 for event in pushes)


def test_head_on_stalemate_yields_without_discarding_either_order() -> None:
    simulation = _collision_simulation(EntityKind.LIGHT_TANK, EntityKind.LIGHT_TANK, lane_width=1)
    assert simulation.execute(MoveCommand(("mover",), Point(12.5, 2.5))).accepted
    assert simulation.execute(MoveCommand(("blocker",), Point(0.5, 2.5))).accepted

    simulation.advance(80)

    for entity in simulation.entities.values():
        assert entity.state is UnitState.MOVING
        assert entity.path
        assert entity.move_target is not None
    yielded = simulation.events.query(event_types=frozenset({EventType.MOVEMENT_YIELDED}))
    assert yielded
    assert {event.subject_id for event in yielded} <= {"mover", "blocker"}
    assert all(event.details["reason"] == "NO_PROGRESS_YIELD" for event in yielded)

    simulation.remove_entity("blocker")
    simulation.advance(40)
    assert simulation.entities["mover"].position == Point(12.5, 2.5)
    assert not simulation.entities["mover"].path


def test_line_patrol_preserves_its_assignment_while_pushing_a_blocker() -> None:
    simulation = _collision_simulation(EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK, lane_width=1)
    created = simulation.execute(
        CreatePatrolCommand(
            ("mover",),
            PolylineTarget((Point(2.5, 2.5), Point(12.5, 2.5))),
        )
    )

    simulation.advance(140)

    automation = simulation.automations[created.automation_id or ""]
    assert automation.status is AutomationStatus.ACTIVE
    assert automation.entity_ids == ["mover"]
    assert simulation.assignments["mover"] == automation.automation_id
    assert simulation.entities["mover"].state is UnitState.PATROLLING


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
