"""Automation ownership, defense, reinforcement, and repair contracts."""

from __future__ import annotations

import pytest
from tests.integration.scenarios import (
    runtime_map,
)

from airts.automations import (
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    RepairParameters,
)
from airts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
)
from airts.events import EventType
from airts.geometry import Point, PointTarget, rectangle_region
from airts.simulation import Simulation
from airts.world.entities import UnitState
from airts.world.map_model import EntityKind, load_map_data


def test_ownership_validation_is_atomic_and_structured() -> None:
    game_map = load_map_data(
        {
            "id": "owners",
            "name": "Owners",
            "width": 8,
            "height": 8,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [
                {"id": "enemy", "kind": "scout", "owner": "enemy", "position": [1.5, 1.5]}
            ],
        }
    )
    simulation = Simulation(game_map)
    before = simulation.snapshot()

    result = simulation.execute(MoveCommand(("enemy",), Point(5, 5), owner_id="player"))

    assert not result.accepted
    assert result.reason == "ENTITY_NOT_OWNED:enemy"
    assert simulation.snapshot() == before
    assert simulation.events.events[-2].event_type is EventType.VALIDATION_FAILED
    assert simulation.events.events[-2].details["phase"] == "ownership"


def test_new_unit_automation_replaces_older_assignment_even_at_lower_priority() -> None:
    simulation = Simulation(runtime_map())
    patrol = simulation.execute(
        CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3)), priority=10)
    )
    patrol_id = patrol.automation_id or ""

    lower = simulation.execute(
        CreateDefendCommand(("unit_01",), PointTarget(Point(12, 3)), priority=5)
    )
    equal_newer = simulation.execute(
        CreateDefendCommand(("unit_01",), PointTarget(Point(12, 3)), priority=10)
    )

    assert lower.accepted
    assert simulation.automations[patrol_id].status is AutomationStatus.CANCELED
    assert equal_newer.accepted
    assert simulation.assignments["unit_01"] == equal_newer.automation_id
    assert simulation.automations[lower.automation_id or ""].status is AutomationStatus.CANCELED
    assert [item.automation_id for item in simulation.live_automations] == [
        equal_newer.automation_id
    ]


def test_low_health_does_not_override_the_current_automation() -> None:
    simulation = Simulation(runtime_map())
    patrol = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3))))
    simulation.entities["unit_01"].health = 1

    simulation.advance(5)

    assert simulation.assignments["unit_01"] == patrol.automation_id
    assert all(
        automation.kind is not AutomationKind.REPAIR_AND_RETURN
        for automation in simulation.automations.values()
    )


@pytest.mark.parametrize(
    ("health", "threshold"), [(10, 0.3), (20, 0.5)], ids=["default", "custom-threshold"]
)
def test_canceling_explicit_repair_restores_current_work_and_hides_canceled_work(
    health: int, threshold: float
) -> None:
    simulation = Simulation(runtime_map())
    patrol = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3))))
    simulation.entities["unit_01"].health = health
    repair = simulation.execute(
        CreateRepairAndReturnCommand(("unit_01",), health_threshold=threshold)
    )
    assert repair.accepted
    assert simulation.suspended_assignments["unit_01"] == patrol.automation_id

    canceled = simulation.execute(CancelAutomationCommand(repair.automation_id or ""))

    assert canceled.accepted
    assert simulation.assignments["unit_01"] == patrol.automation_id
    assert "unit_01" not in simulation.suspended_assignments
    assert repair.automation_id not in {
        automation.automation_id for automation in simulation.live_automations
    }
    assert patrol.automation_id in {
        automation.automation_id for automation in simulation.live_automations
    }


def test_replaced_and_empty_automations_do_not_consume_live_panel_space() -> None:
    simulation = Simulation(runtime_map())
    patrol = simulation.execute(
        CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3)), priority=4)
    )
    defend = simulation.execute(
        CreateDefendCommand(("unit_01",), PointTarget(Point(12, 3)), priority=4)
    )

    assert simulation.assignments["unit_01"] == defend.automation_id
    assert [automation.automation_id for automation in simulation.live_automations] == [
        defend.automation_id
    ]

    simulation.remove_entity("unit_01")

    assert not simulation.live_automations
    assert simulation.automations[patrol.automation_id or ""].status is AutomationStatus.CANCELED
    assert simulation.automations[defend.automation_id or ""].status is AutomationStatus.CANCELED


def test_reassigning_a_tank_group_discards_the_empty_older_automation() -> None:
    simulation = Simulation(runtime_map())
    group = ("unit_01", "unit_02", "unit_03")
    defend = simulation.execute(
        CreateDefendCommand(
            group,
            rectangle_region(Point(8, 2), Point(13, 7)),
            priority=10,
        )
    )
    patrol = simulation.execute(
        CreatePatrolCommand(
            group,
            rectangle_region(Point(14, 1), Point(22, 7)),
            priority=1,
        )
    )

    older = simulation.automations[defend.automation_id or ""]
    assert patrol.accepted
    assert older.status is AutomationStatus.CANCELED
    assert older.reason_code == "NO_ASSIGNED_ENTITIES"
    assert not older.entity_ids
    assert all(simulation.assignments[entity_id] == patrol.automation_id for entity_id in group)
    assert [item.automation_id for item in simulation.live_automations] == [patrol.automation_id]


def test_defend_returns_displaced_unit_and_holds_inside_target() -> None:
    simulation = Simulation(runtime_map())
    created = simulation.execute(
        CreateDefendCommand(("unit_01",), PointTarget(Point(12, 3), radius=2))
    )

    simulation.advance(40)

    assert created.accepted
    assert Point(12, 3).distance_to(simulation.entities["unit_01"].position) <= 2
    assert simulation.entities["unit_01"].state is UnitState.DEFENDING


def test_defend_distributes_units_across_exact_area_stations() -> None:
    simulation = Simulation(runtime_map())
    created = simulation.execute(
        CreateDefendCommand(
            ("unit_01", "unit_02", "unit_03"),
            rectangle_region(Point(8, 1), Point(22, 7)),
        )
    )
    automation = simulation.automations[created.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)

    simulation.advance(100)

    stations = automation.parameters.stations
    assert {
        entity_id: simulation.entities[entity_id].position for entity_id in stations
    } == stations
    assert (
        min(
            first.distance_to(second)
            for index, first in enumerate(stations.values())
            for second in tuple(stations.values())[index + 1 :]
        )
        >= 5
    )
    assert all(
        simulation.entities[entity_id].state is UnitState.DEFENDING for entity_id in stations
    )


def test_gathering_reinforcement_fills_center_before_outer_slots() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "gathering_outskirts",
                "name": "Gathering Outskirts",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "blocker",
                        "kind": "light_tank",
                        "position": [5.5, 5.5],
                    },
                    {
                        "id": "incoming",
                        "kind": "light_tank",
                        "position": [4.5, 5.5],
                    },
                ],
            }
        )
    )
    result = simulation.execute(
        CreateDefendCommand(
            ("blocker", "incoming"),
            rectangle_region(Point(5, 5), Point(7, 7)),
            gathering_point=True,
        )
    )
    simulation.advance(10)

    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    center = Point(6, 6)
    blocker_station = automation.parameters.stations["blocker"]
    incoming_station = automation.parameters.stations["incoming"]
    assert blocker_station == center
    assert blocker_station.distance_to(center) < incoming_station.distance_to(center)
    assert simulation.entities["blocker"].position.distance_to(center) < 0.1
    assert not any(
        event.details.get("reason") == "GATHERING_OUTSKIRTS_SETTLED"
        for event in simulation.events.query(
            event_types=frozenset({EventType.MOVEMENT_COMPLETED}),
            subject_id="incoming",
        )
    )


def test_nearby_defenders_retaliate_then_return_when_attacker_runs_away() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "defend_response",
                "name": "Defend Response",
                "width": 26,
                "height": 20,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": f"guard_{index}",
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [2.5, 5.5 + index * 2],
                    }
                    for index in range(1, 4)
                ]
                + [
                    {
                        "id": "raider",
                        "kind": "heavy_tank",
                        "owner": "enemy",
                        "position": [23.5, 2.5],
                    }
                ],
            }
        )
    )
    created = simulation.execute(
        CreateDefendCommand(
            ("guard_1", "guard_2", "guard_3"),
            PointTarget(Point(10.5, 10.5), radius=2),
        )
    )
    automation = simulation.automations[created.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    simulation.advance(80)
    victim_station = automation.parameters.stations["guard_1"]
    raider = simulation.entities["raider"]
    raider.position = Point(victim_station.x, victim_station.y - 3)
    simulation.occupancy.move("raider", raider.occupied_cells)

    assert simulation.execute(AttackCommand(("raider",), "guard_1", owner_id="enemy")).accepted
    simulation.advance(9)

    engaged = simulation.events.query(event_types=frozenset({EventType.DEFEND_ENGAGED}))
    assert {event.subject_id for event in engaged} == {
        "guard_1",
        "guard_2",
        "guard_3",
    }
    assert all(
        simulation.entities[entity_id].attack_target_id == "raider"
        for entity_id in ("guard_1", "guard_2", "guard_3")
    )

    raider.position = Point(23.5, 17.5)
    simulation.occupancy.move("raider", raider.occupied_cells)
    raider.path.clear()
    raider.move_target = None
    raider.attack_target_id = None
    raider.state = UnitState.HOLDING
    simulation.advance(100)

    assert all(
        simulation.entities[entity_id].attack_target_id is None
        and simulation.entities[entity_id].position == station
        and simulation.entities[entity_id].state is UnitState.DEFENDING
        for entity_id, station in automation.parameters.stations.items()
    )


def test_reinforcement_transfers_units_to_target_automation() -> None:
    simulation = Simulation(runtime_map())
    target = simulation.execute(
        CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3)), priority=5)
    )
    target_id = target.automation_id or ""
    reinforcement = simulation.execute(
        CreateReinforcementCommand(("unit_02", "unit_03"), target_id, 3)
    )

    simulation.advance()

    assert reinforcement.accepted
    assert (
        simulation.automations[reinforcement.automation_id or ""].status
        is AutomationStatus.COMPLETED
    )
    assert simulation.automations[target_id].entity_ids == ["unit_01", "unit_02", "unit_03"]
    assert simulation.assignments["unit_02"] == target_id


def test_reinforcement_waits_when_higher_priority_claims_block_candidates() -> None:
    simulation = Simulation(runtime_map())
    target = simulation.execute(
        CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3)), priority=0)
    )
    simulation.execute(CreateDefendCommand(("unit_02",), PointTarget(Point(8, 5)), priority=100))
    reinforcement = simulation.execute(
        CreateReinforcementCommand(("unit_02",), target.automation_id or "", 2)
    )

    simulation.advance()

    assert (
        simulation.automations[reinforcement.automation_id or ""].status is AutomationStatus.WAITING
    )


def test_reinforcement_fails_if_target_automation_is_canceled() -> None:
    simulation = Simulation(runtime_map())
    target = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3))))
    reinforcement = simulation.execute(
        CreateReinforcementCommand(("unit_02",), target.automation_id or "", 2)
    )
    simulation.execute(CancelAutomationCommand(target.automation_id or ""))

    simulation.advance()

    automation = simulation.automations[reinforcement.automation_id or ""]
    assert automation.status is AutomationStatus.FAILED
    assert automation.reason_code == "TARGET_AUTOMATION_UNAVAILABLE"


def test_emergency_repair_suspends_and_restores_original_assignment() -> None:
    simulation = Simulation(runtime_map())
    patrol = simulation.execute(
        CreatePatrolCommand(("unit_01",), PointTarget(Point(12, 3)), priority=100)
    )
    patrol_id = patrol.automation_id or ""
    simulation.entities["unit_01"].health = 15

    repair = simulation.execute(
        CreateRepairAndReturnCommand(("unit_01",), health_threshold=0.5, repair_rate=15)
    )
    repair_id = repair.automation_id or ""

    assert simulation.assignments["unit_01"] == repair_id
    assert simulation.suspended_assignments["unit_01"] == patrol_id
    denied = simulation.execute(
        CreateDefendCommand(("unit_01",), PointTarget(Point(8, 3)), priority=100)
    )
    assert not denied.accepted

    simulation.advance(40)

    assert simulation.entities["unit_01"].health == 60
    assert simulation.automations[repair_id].status is AutomationStatus.COMPLETED
    assert simulation.assignments["unit_01"] == patrol_id
    assert "unit_01" not in simulation.suspended_assignments


def test_repair_prefers_repair_hub_before_closer_factory() -> None:
    simulation = Simulation(runtime_map())
    simulation.entities["unit_03"].position = Point(10.5, 7.5)
    simulation.occupancy.move("unit_03", frozenset({(10, 7)}))
    simulation.entities["unit_03"].health = 20

    repair = simulation.execute(CreateRepairAndReturnCommand(("unit_03",), health_threshold=0.5))
    automation = simulation.automations[repair.automation_id or ""]

    assert isinstance(automation.parameters, RepairParameters)
    assert automation.parameters.destinations["unit_03"] == "repair"


def test_repair_only_claims_selected_units_strictly_below_thirty_percent() -> None:
    simulation = Simulation(runtime_map())
    patrol = simulation.execute(
        CreatePatrolCommand(("unit_01", "unit_02", "unit_03"), PointTarget(Point(12, 3)))
    )
    simulation.entities["unit_01"].health = 17  # 28.3% of scout health.
    simulation.entities["unit_02"].health = 30  # Exactly 30% is not below the threshold.
    simulation.entities["unit_03"].health = 150

    repair = simulation.execute(CreateRepairAndReturnCommand(("unit_01", "unit_02", "unit_03")))

    automation = simulation.automations[repair.automation_id or ""]
    assert automation.entity_ids == ["unit_01"]
    assert simulation.assignments["unit_01"] == automation.automation_id
    assert simulation.assignments["unit_02"] == patrol.automation_id
    assert simulation.assignments["unit_03"] == patrol.automation_id


def test_unassigned_repaired_unit_returns_to_its_pre_repair_position() -> None:
    simulation = Simulation(runtime_map())
    origin = simulation.entities["unit_01"].position
    simulation.entities["unit_01"].health = 5

    repair = simulation.execute(CreateRepairAndReturnCommand(("unit_01",), repair_rate=20))
    simulation.advance(100)

    assert simulation.automations[repair.automation_id or ""].status is AutomationStatus.COMPLETED
    assert simulation.entities["unit_01"].health == EntityKind.SCOUT.profile.max_health
    assert simulation.entities["unit_01"].position == origin
    assert "unit_01" not in simulation.assignments


def test_repair_validation_fails_without_supported_friendly_building() -> None:
    game_map = load_map_data(
        {
            "id": "no_repair",
            "name": "No Repair",
            "width": 8,
            "height": 8,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [{"id": "unit", "kind": "scout", "position": [1.5, 1.5]}],
        }
    )
    simulation = Simulation(game_map)
    simulation.entities["unit"].health = 5

    result = simulation.execute(CreateRepairAndReturnCommand(("unit",)))

    assert not result.accepted
    assert result.reason == "NO_REPAIR_DESTINATION"


@pytest.mark.parametrize("replacement_ids", (("unit_0", "unit_1", "unit_2"), ("unit_0",)))
@pytest.mark.parametrize("gathering_point", (False, True))
def test_repeating_expanded_defend_preserves_stations_during_ownership_transfer(
    replacement_ids: tuple[str, ...],
    gathering_point: bool,
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "repeat_defend",
                "name": "Repeat expanded defense",
                "width": 20,
                "height": 20,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": f"unit_{index}", "kind": "heavy_tank", "position": [2.5 + index, 2.5]}
                    for index in range(3)
                ],
            }
        )
    )
    target = rectangle_region(Point(12, 12), Point(13, 13))
    assert simulation.execute(
        CreateDefendCommand(("unit_0", "unit_1", "unit_2"), target, gathering_point=True)
    ).accepted
    for _ in range(3):
        result = simulation.execute(
            CreateDefendCommand(replacement_ids, target, gathering_point=gathering_point)
        )
        assert result.accepted
        stations = {}
        for automation in simulation.automations.values():
            if automation.status.terminal:
                continue
            assert isinstance(automation.parameters, DefendParameters)
            assert set(automation.parameters.stations) == set(automation.entity_ids)
            stations.update(automation.parameters.stations)
        assert len(stations) == len(set(stations.values())) == 3
        assert all(simulation.assignments[key] == result.automation_id for key in replacement_ids)
    simulation.advance(10)
