from __future__ import annotations

from pathlib import Path

from airts.automations import AutomationKind, AutomationStatus
from airts.commands import (
    AttackCommand,
    CreateEconomyCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
)
from airts.events import EventType
from airts.map_model import EntityKind, load_map_data
from airts.persistence import load_simulation, save_simulation
from airts.replay import load_replay, run_replay, save_replay
from airts.simulation import Simulation


def _phase5_simulation() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "phase5_test",
                "name": "Phase 5 Test",
                "width": 16,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "tank", "kind": "light_tank", "owner": "player", "position": [2.5, 2.5]},
                    {"id": "enemy", "kind": "scout", "owner": "enemy", "position": [4.5, 2.5]},
                    {"id": "factory", "kind": "factory", "owner": "player", "position": [2, 8]},
                    {"id": "repair", "kind": "repair_hub", "owner": "player", "position": [8, 8]},
                    {
                        "id": "generator",
                        "kind": "resource_generator",
                        "owner": "player",
                        "position": [12, 9],
                    },
                ],
            }
        ),
        random_seed=41,
    )


def test_production_charges_cost_and_waits_for_resources() -> None:
    simulation = _phase5_simulation()
    simulation.execute(CreateProductionCommand("factory", EntityKind.LIGHT_TANK, 1))
    simulation.advance()
    assert simulation.resources["player"] == 400
    assert any(
        event.event_type is EventType.RESOURCE_CHANGED
        and event.details["reason"] == "PRODUCTION_COST"
        for event in simulation.events.events
    )

    poor = _phase5_simulation()
    poor.resources["player"] = 50
    result = poor.execute(CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1))
    poor.advance()
    assert poor.automations[result.automation_id or ""].status is AutomationStatus.WAITING
    assert poor.resources["player"] == 50


def test_economy_automation_collects_generator_income_to_target() -> None:
    simulation = _phase5_simulation()
    result = simulation.execute(CreateEconomyCommand(("generator",), 530))
    simulation.advance(31)
    automation = simulation.automations[result.automation_id or ""]

    assert simulation.resources["player"] == 530
    assert automation.kind is AutomationKind.ECONOMY
    assert automation.status is AutomationStatus.COMPLETED
    assert automation.parameters.to_dict()["collected"] == 30


def test_attack_damage_destruction_and_repair_only_when_requested() -> None:
    simulation = _phase5_simulation()
    assert simulation.execute(AttackCommand(("tank",), "enemy")).accepted
    simulation.advance()
    assert simulation.entities["enemy"].health == 48
    assert simulation.events.query(event_types=frozenset({EventType.COMBAT_ATTACK}))

    simulation.entities["enemy"].health = 5
    simulation.entities["tank"].attack_cooldown = 0
    simulation.advance()
    assert "enemy" not in simulation.entities
    assert simulation.events.query(event_types=frozenset({EventType.ENTITY_DESTROYED}))

    simulation.entities["tank"].health = 20
    simulation.advance()
    assert "tank" not in simulation.assignments
    assert not any(
        automation.kind is AutomationKind.REPAIR_AND_RETURN
        for automation in simulation.automations.values()
    )
    assert not simulation.events.query(event_types=frozenset({EventType.RETREAT_STARTED}))

    requested = simulation.execute(CreateRepairAndReturnCommand(("tank",)))
    assigned = simulation.automations[simulation.assignments["tank"]]
    assert requested.accepted
    assert assigned.kind is AutomationKind.REPAIR_AND_RETURN
    assert assigned.title == "Repair And Return"


def test_phase5_save_and_replay_preserve_resources_and_combat(
    tmp_path: Path,
) -> None:
    simulation = _phase5_simulation()
    simulation.execute(CreateEconomyCommand(("generator",), 520))
    simulation.execute(AttackCommand(("tank",), "enemy"))
    simulation.advance(12)

    save_path = tmp_path / "phase5-save.json"
    replay_path = tmp_path / "phase5-replay.json"
    save_simulation(simulation, save_path)
    save_replay(simulation, replay_path)

    restored = load_simulation(save_path)
    replayed = run_replay(load_replay(replay_path))
    assert restored.snapshot() == simulation.snapshot()
    assert replayed.snapshot() == simulation.snapshot()

    restored.advance(10)
    simulation.advance(10)
    assert restored.snapshot() == simulation.snapshot()
