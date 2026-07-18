"""Integration contracts for the phase 3 runtime milestone."""

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
from airts.events import EventType
from airts.geometry import Point, PointTarget, rectangle_region
from airts.navigation.collision import collision_radius
from airts.simulation import Simulation
from airts.world.entities import UnitState
from airts.world.map_model import EntityKind, GameMap, load_map_data


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


def test_recreating_same_expanded_defense_reassigns_stations_atomically() -> None:
    simulation = Simulation(_runtime_map())
    group = ("unit_01", "unit_02", "unit_03")
    target = rectangle_region(Point(12, 2), Point(13, 3))
    first = simulation.execute(CreateDefendCommand(group, target))
    first_automation = simulation.automations[first.automation_id or ""]
    assert isinstance(first_automation.parameters, DefendParameters)
    assert first_automation.parameters.deployment_slots

    replacement = simulation.execute(CreateDefendCommand(group, target))

    replacement_automation = simulation.automations[replacement.automation_id or ""]
    assert replacement.accepted
    assert first_automation.status is AutomationStatus.CANCELED
    assert isinstance(replacement_automation.parameters, DefendParameters)
    assert set(replacement_automation.parameters.stations) == set(group)
    assert len(set(replacement_automation.parameters.stations.values())) == len(group)
    assert all(
        simulation.game_map.is_passable(station)
        for station in replacement_automation.parameters.stations.values()
    )
    assert all(
        simulation.assignments[entity_id] == replacement.automation_id for entity_id in group
    )

    simulation.advance()

    assert replacement_automation.status is AutomationStatus.ACTIVE


def test_expanded_defense_routes_via_passable_slots_around_impassable_centroid() -> None:
    entity_ids = tuple(f"unit_{index}" for index in range(4))
    simulation = Simulation(
        load_map_data(
            {
                "id": "impassable_defend_centroid",
                "name": "Impassable Defend Centroid",
                "width": 20,
                "height": 20,
                "terrain": {
                    "default": "grass",
                    "rectangles": [[11, 11, 1, 1, "water"]],
                },
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "position": [1.5, 1.5 + index],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        )
    )
    target = rectangle_region(Point(10, 10), Point(12, 12))

    created = simulation.execute(CreateDefendCommand(entity_ids, target))
    automation = simulation.automations[created.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    assert created.accepted
    assert automation.parameters.deployment_slots
    assert len(set(automation.parameters.stations.values())) == len(entity_ids)
    assert all(
        simulation.game_map.is_passable(station)
        for station in automation.parameters.stations.values()
    )

    simulation.advance()

    assert automation.status is AutomationStatus.ACTIVE
    assert all(
        simulation.entities[entity_id].state is UnitState.DEFENDING for entity_id in entity_ids
    )
    assert any(
        simulation.entities[entity_id].path
        or simulation.entities[entity_id].position != Point(1.5, 1.5 + index)
        for index, entity_id in enumerate(entity_ids)
    )


def test_defend_returns_displaced_unit_and_holds_inside_target() -> None:
    simulation = Simulation(_runtime_map())
    created = simulation.execute(
        CreateDefendCommand(("unit_01",), PointTarget(Point(12, 3), radius=2))
    )
    automation = simulation.automations[created.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)

    simulation.advance(40)

    assert created.accepted
    station = automation.parameters.stations["unit_01"]
    assert simulation.game_map.is_passable(station)
    assert (
        simulation.entities["unit_01"].position.distance_to(station)
        <= Simulation.DEFEND_FORMATION_TOLERANCE
    )
    assert simulation.entities["unit_01"].state is UnitState.DEFENDING


def test_defend_distributes_units_across_unique_passable_area_stations() -> None:
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
    assert set(stations) == {"unit_01", "unit_02", "unit_03"}
    assert len(set(stations.values())) == len(stations)
    assert all(simulation.game_map.is_passable(station) for station in stations.values())
    assert all(
        simulation.entities[entity_id].position.distance_to(station)
        <= Simulation.DEFEND_FORMATION_TOLERANCE
        for entity_id, station in stations.items()
    )
    assert all(
        simulation.entities[entity_id].state is UnitState.DEFENDING for entity_id in stations
    )


def test_gathering_reinforcement_assigns_center_before_outer_slots() -> None:
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
    assert len(set(automation.parameters.stations.values())) == 2
    assert all(
        simulation.game_map.is_passable(station)
        for station in automation.parameters.stations.values()
    )
    assert blocker_station.distance_to(center) < incoming_station.distance_to(center)
    blocker = simulation.entities["blocker"]
    assert blocker.position.distance_to(blocker_station) <= collision_radius(blocker.kind)
    assert blocker.state is UnitState.DEFENDING
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
        and simulation.entities[entity_id].position.distance_to(station)
        <= Simulation.DEFEND_FORMATION_TOLERANCE
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


def test_continuous_factory_production_assigns_unique_passable_defense_stations() -> None:
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
    assert isinstance(defend.parameters, DefendParameters)
    assert defend.parameters.gathering_point
    assert len(defend.parameters.deployment_slots) == 3
    assert len(set(defend.parameters.stations.values())) == 3
    assert defend.entity_ids == parameters.produced_entity_ids
    assert all(
        simulation.assignments[entity_id] == defend.automation_id
        for entity_id in parameters.produced_entity_ids
    )
    initial_stations = dict(defend.parameters.stations)

    simulation.advance(10)
    assert parameters.produced_count == 5
    assert production.status is AutomationStatus.ACTIVE
    assert defend.entity_ids == parameters.produced_entity_ids
    assert set(defend.parameters.stations) == set(parameters.produced_entity_ids)
    assert len(set(defend.parameters.stations.values())) == len(parameters.produced_entity_ids)
    assert all(
        simulation.game_map.is_passable(station) for station in defend.parameters.stations.values()
    )
    assert all(
        defend.parameters.stations[entity_id] == station
        for entity_id, station in initial_stations.items()
    )
    assert all(
        simulation.assignments[entity_id] == defend.automation_id
        and simulation.entities[entity_id].state is UnitState.DEFENDING
        for entity_id in parameters.produced_entity_ids
    )


def test_factory_keeps_only_latest_continuous_production_request() -> None:
    simulation = Simulation(_runtime_map())
    simulation.resources["player"] = 10_000
    first_target = rectangle_region(Point(14, 2), Point(18, 6))
    second_target = rectangle_region(Point(18, 2), Point(22, 6))
    first = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.SCOUT,
            1,
            continuous=True,
            defend_target=first_target,
        )
    )
    simulation.advance(5)
    first_automation = simulation.automations[first.automation_id or ""]
    assert isinstance(first_automation.parameters, ProductionParameters)
    first_defend = simulation.automations[first_automation.parameters.defend_automation_id or ""]

    second = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.LIGHT_TANK,
            1,
            continuous=True,
            defend_target=second_target,
        )
    )

    second_id = second.automation_id or ""
    assert first_automation.status is AutomationStatus.CANCELED
    assert first_automation.reason_code == "SUPERSEDED_BY_LATEST_CONTINUOUS_PRODUCTION"
    assert first_defend.status is AutomationStatus.ACTIVE
    assert first_defend.entity_ids == ["scout_001"]
    assert [item.automation_id for item in simulation.production_queue("factory")] == [second_id]
    assert simulation.assignments["factory"] == second_id

    simulation.advance(5)
    second_parameters = simulation.automations[second_id].parameters
    assert isinstance(second_parameters, ProductionParameters)
    second_defend = simulation.automations[second_parameters.defend_automation_id or ""]
    assert isinstance(second_defend.parameters, DefendParameters)
    assert second_defend.parameters.target == second_target
    assert second_defend.parameters.gathering_point
    assert second_defend.entity_ids == ["light_tank_002"]


def test_replacing_continuous_production_preserves_older_finite_jobs() -> None:
    simulation = Simulation(_runtime_map())
    finite = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 2))
    older = simulation.execute(
        CreateProductionCommand("factory", EntityKind.LIGHT_TANK, 1, continuous=True)
    )
    latest = simulation.execute(
        CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1, continuous=True)
    )

    assert simulation.automations[older.automation_id or ""].status is AutomationStatus.CANCELED
    assert [item.automation_id for item in simulation.production_queue("factory")] == [
        finite.automation_id,
        latest.automation_id,
    ]
    assert simulation.assignments["factory"] == finite.automation_id
    assert simulation.automations[latest.automation_id or ""].reason_code == "FACTORY_QUEUED"


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


def test_repair_only_claims_selected_units_strictly_below_thirty_percent() -> None:
    simulation = Simulation(_runtime_map())
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
    simulation = Simulation(_runtime_map())
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
