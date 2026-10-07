"""Complete persistence and normal-command replay through coordinated weak return."""

from __future__ import annotations

import json

import pytest
from tests.integration.recovery_checks import (
    RecoveryChecks,
    _assert_restored_and_stable,
    _displace_legally,
    _positions,
    _resting_formation,
)

from airts.adapters.persistence import SAVE_SCHEMA, load_simulation_data
from airts.adapters.replay import capture_replay, load_replay_data, run_replay
from airts.contracts.commands import HoldPositionCommand, MoveCommand
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems.movement import HOLD_TOLERANCE
from airts.world.map import load_map_data


@pytest.mark.parametrize("unit_count", [100, 400])
@pytest.mark.parametrize("phase", ["opening", "joint-return"])
def test_complete_save_continues_identically_during_both_weak_return_phases(
    unit_count: int, phase: str
) -> None:
    simulation, ids, selected, anchors, _ = _resting_formation(unit_count)
    # Legal displacement is preparation, and therefore only complete saves can
    # represent it. This case makes no claim of a replayable player operation chain.
    _displace_legally(simulation, ids, selected, Point(1, 0))
    before = _positions(simulation, ids)
    checks = RecoveryChecks(simulation, ids)
    for _ in range(800):
        before, _ = checks.advance_weak(before)
        entities = [simulation.entities[key] for key in ids]
        if phase == "opening":
            reached_phase = any(
                entity.recovery_request_tick is not None and not entity.recovery_released
                for entity in entities
            )
        else:
            reached_phase = any(entity.recovery_released for entity in entities) and any(
                entity.position.distance_to(anchors[entity.entity_id]) > HOLD_TOLERANCE
                for entity in entities
            )
        if reached_phase:
            break
    else:
        pytest.fail(f"The observable {phase} phase was never reached")
    document = {
        "schema": SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": simulation.export_state(),
    }
    restored = load_simulation_data(json.loads(json.dumps(document)))
    assert restored.export_state() == simulation.export_state()
    assert any(simulation.entities[key].recovery_request_tick is not None for key in ids)
    while simulation.tick < 1_000:
        before, _ = checks.advance_weak(before)
        restored.advance()
        assert restored.export_state() == simulation.export_state()
    _assert_restored_and_stable(simulation, ids, anchors, "hold")
    _assert_restored_and_stable(restored, ids, anchors, "hold")
    assert restored.export_state() == simulation.export_state()


def test_normal_command_pressure_and_complete_hold_return_replay_identically() -> None:
    """Map, seed, and ordinary commands include every operation in this replay."""
    ids = tuple(f"scout_{index:04d}" for index in range(100))
    simulation = Simulation(
        load_map_data(
            {
                "id": "normal_compatible_hold_return",
                "name": "Normal pressure and compatible Hold return",
                "width": 96,
                "height": 96,
                "terrain": {"default": "grass"},
                "entities": [
                    {
                        "id": key,
                        "kind": "scout",
                        "position": [35.5 + index % 10, 35.5 + index // 10],
                    }
                    for index, key in enumerate(ids)
                ],
            }
        ),
        random_seed=199,
    )
    selected = (ids[44],)
    passive = tuple(key for key in ids if key not in selected)
    assert simulation.execute(HoldPositionCommand(ids)).accepted
    anchors = {key: simulation.entities[key].position for key in passive}
    before = _positions(simulation, ids)
    saw_request = False
    checks = RecoveryChecks(simulation, ids)
    for destination in (Point(70, 40), Point(20, 40)):
        assert simulation.execute(MoveCommand(selected, destination)).accepted
        accepted = simulation.entities[selected[0]].move_target
        assert accepted is not None
        for _ in range(800):
            after, metrics = checks.advance(before)
            checks.assert_passive_half_steps(passive, before, after)
            before = after
            assert metrics[2] <= 1e-7
            saw_request |= any(
                simulation.entities[key].recovery_request_tick is not None for key in passive
            )
        completed = next(
            event
            for event in reversed(simulation.events.events)
            if event.subject_id == selected[0] and event.event_type.value == "movement_completed"
        )
        assert Point(*completed.details["position"]).distance_to(accepted) <= 1e-8
        assert simulation.entities[selected[0]].position.distance_to(accepted) <= 0.6
        _assert_restored_and_stable(simulation, passive, anchors, "hold")
        before = _positions(simulation, ids)
    assert saw_request, "The normal command sequence must exercise actual passive displacement"
    replay = load_replay_data(json.loads(json.dumps(capture_replay(simulation).to_dict())))
    replayed = run_replay(replay)
    assert replayed.export_state() == simulation.export_state()
