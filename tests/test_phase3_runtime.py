from __future__ import annotations

from airts.automations import (
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    ProductionParameters,
    RepairParameters,
)
from airts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
    StopCommand,
)
from airts.entities import UnitState
from airts.events import EventType
from airts.geometry import Point, PointTarget, rectangle_region
from airts.map_model import EntityKind, GameMap, load_map_data
from airts.simulation import Simulation


def _runtime_map() -> GameMap:
    return load_map_data(
        {
            "id": "runtime",
            "name": "Runtime",
            "width": 24,
            "height": 16,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [
                {"id": "unit_01", "kind": "scout", "position": [1.5, 2.5]},
                {"id": "unit_02", "kind": "light_tank", "position": [1.5, 4.5]},
                {"id": "unit_03", "kind": "heavy_tank", "position": [1.5, 6.5]},
                {"id": "factory", "kind": "factory", "position": [7, 8]},
                {"id": "repair", "kind": "repair_hub", "position": [4, 9]},
                {"id": "command", "kind": "command_center", "position": [15, 8]},
            ],
        }
    )


def _production_ids(simulation: Simulation, automation_id: str) -> list[str]:
    parameters = simulation.automations[automation_id].parameters
    assert isinstance(parameters, ProductionParameters)
    return parameters.produced_entity_ids


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
    simulation = Simulation(_runtime_map())
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
    simulation = Simulation(_runtime_map())
    patrol = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3))))
    simulation.entities["unit_01"].health = 1

    simulation.advance(5)

    assert simulation.assignments["unit_01"] == patrol.automation_id
    assert all(
        automation.kind is not AutomationKind.REPAIR_AND_RETURN
        for automation in simulation.automations.values()
    )


def test_canceling_explicit_repair_restores_current_work_and_hides_canceled_work() -> None:
    simulation = Simulation(_runtime_map())
    patrol = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(10, 3))))
    simulation.entities["unit_01"].health = 20
    repair = simulation.execute(CreateRepairAndReturnCommand(("unit_01",), health_threshold=0.5))

    canceled = simulation.execute(CancelAutomationCommand(repair.automation_id or ""))

    assert canceled.accepted
    assert simulation.assignments["unit_01"] == patrol.automation_id
    assert repair.automation_id not in {
        automation.automation_id for automation in simulation.live_automations
    }
    assert patrol.automation_id in {
        automation.automation_id for automation in simulation.live_automations
    }


def test_replaced_and_empty_automations_do_not_consume_live_panel_space() -> None:
    simulation = Simulation(_runtime_map())
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
    simulation = Simulation(_runtime_map())
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
    simulation = Simulation(_runtime_map())
    created = simulation.execute(
        CreateDefendCommand(("unit_01",), PointTarget(Point(12, 3), radius=2))
    )

    simulation.advance(40)

    assert created.accepted
    assert Point(12, 3).distance_to(simulation.entities["unit_01"].position) <= 2
    assert simulation.entities["unit_01"].state is UnitState.DEFENDING


def test_defend_distributes_units_across_exact_area_stations() -> None:
    simulation = Simulation(_runtime_map())
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


def test_cost_free_fixed_tick_production_completes_with_deterministic_ids() -> None:
    simulation = Simulation(_runtime_map())
    created = simulation.execute(
        CreateProductionCommand("factory", EntityKind.SCOUT, 2, Point(14.5, 5.5))
    )
    automation_id = created.automation_id or ""

    simulation.advance(20)

    automation = simulation.automations[automation_id]
    assert automation.status is AutomationStatus.COMPLETED
    assert isinstance(automation.parameters, ProductionParameters)
    assert automation.parameters.produced_entity_ids == ["scout_001", "scout_002"]
    assert all(entity_id in simulation.entities for entity_id in ("scout_001", "scout_002"))
    assert "factory" not in simulation.assignments


def test_continuous_factory_production_assigns_every_unit_to_area_defense() -> None:
    simulation = Simulation(_runtime_map())
    simulation.resources["player"] = 10_000
    defend_target = rectangle_region(Point(14, 2), Point(22, 7))
    created = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.LIGHT_TANK,
            1,
            continuous=True,
            defend_target=defend_target,
        )
    )

    simulation.advance(16)

    production = simulation.automations[created.automation_id or ""]
    assert isinstance(production.parameters, ProductionParameters)
    parameters = production.parameters
    assert production.status is AutomationStatus.ACTIVE
    assert parameters.continuous
    assert parameters.produced_count == 3
    defend = simulation.automations[parameters.defend_automation_id or ""]
    assert defend.kind is AutomationKind.DEFEND
    assert defend.entity_ids == parameters.produced_entity_ids
    assert all(
        simulation.assignments[entity_id] == defend.automation_id
        for entity_id in parameters.produced_entity_ids
    )

    simulation.advance(10)
    assert parameters.produced_count == 5
    assert production.status is AutomationStatus.ACTIVE


def test_production_waits_when_no_spawn_cell_is_available() -> None:
    game_map = load_map_data(
        {
            "id": "blocked_factory",
            "name": "Blocked Factory",
            "width": 6,
            "height": 6,
            "terrain": {"default": "water", "rectangles": [[1, 1, 4, 4, "grass"]]},
            "entities": [{"id": "factory", "kind": "factory", "position": [1, 1]}],
        }
    )
    simulation = Simulation(game_map)
    created = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))

    simulation.advance(10)

    automation = simulation.automations[created.automation_id or ""]
    assert automation.status is AutomationStatus.WAITING
    assert automation.reason_code == "SPAWN_BLOCKED"


def test_manual_factory_override_pauses_and_resume_reclaims_factory() -> None:
    simulation = Simulation(_runtime_map())
    created = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    automation_id = created.automation_id or ""

    simulation.execute(StopCommand(("factory",)))

    assert simulation.automations[automation_id].status is AutomationStatus.PAUSED
    assert "factory" not in simulation.assignments

    resumed = simulation.execute(ResumeAutomationCommand(automation_id))
    assert resumed.accepted
    assert simulation.assignments["factory"] == automation_id
    assert simulation.automations[automation_id].status is AutomationStatus.ACTIVE


def test_factory_production_requests_run_fifo_and_paused_jobs_resume_without_conflict() -> None:
    simulation = Simulation(_runtime_map())
    first = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    second = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    third = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    first_id = first.automation_id or ""
    second_id = second.automation_id or ""
    third_id = third.automation_id or ""

    assert first.accepted and second.accepted and third.accepted
    assert simulation.automations[first_id].status is AutomationStatus.ACTIVE
    assert simulation.automations[second_id].reason_code == "FACTORY_QUEUED"
    assert simulation.automations[third_id].reason_code == "FACTORY_QUEUED"
    assert [item.automation_id for item in simulation.production_queue("factory")] == [
        first_id,
        second_id,
        third_id,
    ]
    assert simulation.execute(PauseAutomationCommand(second_id)).accepted
    resumed_queued = simulation.execute(ResumeAutomationCommand(second_id))
    assert resumed_queued.accepted
    assert simulation.automations[second_id].status is AutomationStatus.WAITING
    assert simulation.automations[second_id].reason_code == "FACTORY_QUEUED"

    assert simulation.execute(PauseAutomationCommand(first_id)).accepted
    resumed_active = simulation.execute(ResumeAutomationCommand(first_id))
    assert resumed_active.accepted
    assert simulation.assignments["factory"] == first_id

    simulation.advance(30)

    assert [simulation.automations[item].status for item in (first_id, second_id, third_id)] == [
        AutomationStatus.COMPLETED
    ] * 3
    assert [_production_ids(simulation, item) for item in (first_id, second_id, third_id)] == [
        ["scout_001"],
        ["scout_002"],
        ["scout_003"],
    ]
    active_ticks = [
        next(
            transition.tick
            for transition in simulation.automations[item].transition_history
            if transition.reason_code == reason
        )
        for item, reason in (
            (first_id, "VALIDATION_SUCCEEDED"),
            (second_id, "FACTORY_QUEUE_STARTED"),
            (third_id, "FACTORY_QUEUE_STARTED"),
        )
    ]
    assert active_ticks == [0, 5, 10]


def test_canceling_factory_job_starts_next_without_disturbing_the_active_job() -> None:
    simulation = Simulation(_runtime_map())
    first = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    second = simulation.execute(CreateProductionCommand("factory", EntityKind.LIGHT_TANK, 1))
    third = simulation.execute(CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1))

    assert simulation.execute(CancelAutomationCommand(third.automation_id or "")).accepted
    assert simulation.assignments["factory"] == first.automation_id
    assert [item.automation_id for item in simulation.production_queue("factory")] == [
        first.automation_id,
        second.automation_id,
    ]

    assert simulation.execute(CancelAutomationCommand(first.automation_id or "")).accepted
    assert simulation.assignments["factory"] == second.automation_id
    assert simulation.automations[second.automation_id or ""].status is AutomationStatus.ACTIVE
    assert simulation.automations[second.automation_id or ""].reason_code == "FACTORY_QUEUE_STARTED"


def test_removing_production_source_fails_automation() -> None:
    simulation = Simulation(_runtime_map())
    created = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 2))

    simulation.remove_entity("factory", "DESTROYED")

    automation = simulation.automations[created.automation_id or ""]
    assert automation.status is AutomationStatus.FAILED
    assert automation.reason_code == "SOURCE_ENTITY_REMOVED"


def test_reinforcement_transfers_units_to_target_automation() -> None:
    simulation = Simulation(_runtime_map())
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
    simulation = Simulation(_runtime_map())
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
    simulation = Simulation(_runtime_map())
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
    simulation = Simulation(_runtime_map())
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
    simulation = Simulation(_runtime_map())
    simulation.entities["unit_03"].position = Point(10.5, 7.5)
    simulation.occupancy.move("unit_03", frozenset({(10, 7)}))
    simulation.entities["unit_03"].health = 20

    repair = simulation.execute(CreateRepairAndReturnCommand(("unit_03",), health_threshold=0.5))
    automation = simulation.automations[repair.automation_id or ""]

    assert isinstance(automation.parameters, RepairParameters)
    assert automation.parameters.destinations["unit_03"] == "repair"


def test_canceling_repair_restores_suspended_assignment() -> None:
    simulation = Simulation(_runtime_map())
    patrol = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(12, 3))))
    simulation.entities["unit_01"].health = 10
    repair = simulation.execute(CreateRepairAndReturnCommand(("unit_01",)))

    canceled = simulation.execute(CancelAutomationCommand(repair.automation_id or ""))

    assert canceled.accepted
    assert simulation.assignments["unit_01"] == patrol.automation_id
    assert "unit_01" not in simulation.suspended_assignments


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

    result = simulation.execute(CreateRepairAndReturnCommand(("unit",)))

    assert not result.accepted
    assert result.reason == "NO_REPAIR_DESTINATION"
