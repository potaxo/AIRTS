from __future__ import annotations

from airts.automations import (
    AutomationKind,
    AutomationStatus,
    ProductionParameters,
    RepairParameters,
)
from airts.commands import (
    CancelAutomationCommand,
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    CreateReinforcementCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
    ResumeAutomationCommand,
    StopCommand,
)
from airts.entities import UnitState
from airts.events import EventType
from airts.geometry import Point, PointTarget
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


def test_priority_then_newness_resolves_automation_conflicts() -> None:
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

    assert not lower.accepted
    assert lower.reason == "CONTROL_CONFLICT"
    assert equal_newer.accepted
    assert simulation.assignments["unit_01"] == equal_newer.automation_id
    assert simulation.automations[patrol_id].status is AutomationStatus.CANCELED


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


def test_defend_returns_displaced_unit_and_holds_inside_target() -> None:
    simulation = Simulation(_runtime_map())
    created = simulation.execute(
        CreateDefendCommand(("unit_01",), PointTarget(Point(12, 3), radius=2))
    )

    simulation.advance(40)

    assert created.accepted
    assert Point(12, 3).distance_to(simulation.entities["unit_01"].position) <= 2
    assert simulation.entities["unit_01"].state is UnitState.DEFENDING


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
