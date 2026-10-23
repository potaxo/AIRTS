"""Stable field armies, regional objectives and bounded local responses."""

from __future__ import annotations

from tests.integration.opponent_scenarios import campaign_army, fragmented_army

from airts.adapters.persistence import MATCH_SAVE_SCHEMA, load_simulation_data
from airts.adapters.replay import capture_replay, load_replay_data, run_replay
from airts.contracts.commands import MoveCommand
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems import opponent_ai
from airts.world.map import load_map_data
from airts.world.opponents import SquadPhase


def _membership(simulation: Simulation) -> dict[str, int]:
    return {
        key: index
        for index, army in enumerate(simulation.opponents["ai"].field_armies)
        for squad in army.squads
        for key in squad.entity_ids
    }


def test_five_armies_keep_local_membership_and_advance_on_multiple_regions() -> None:
    simulation = campaign_army()
    starts = {
        key: unit.position for key, unit in simulation.entities.items() if key.startswith("army_")
    }
    simulation.advance(10)
    membership = _membership(simulation)
    assert len(simulation.opponents["ai"].field_armies) == 5
    assert set(membership) == set(starts)
    for _ in range(5):
        simulation.advance(10)
        assert simulation._opponent_command_count <= opponent_ai.MAX_ORDERS
        assert simulation._opponent_candidate_count <= 2 * opponent_ai.MAX_TARGET_CANDIDATES
        assert simulation._opponent_forecast_count <= 16
    current = _membership(simulation)
    assert all(current[key] == index for key, index in membership.items())
    armies = simulation.opponents["ai"].field_armies
    assert len({army.objective_id for army in armies} - {None}) >= 3
    assert all(len(squad.entity_ids) <= 32 for army in armies for squad in army.squads)
    assert sum(simulation.entities[key].pursue_target for key in starts) >= len(starts) * 0.9
    assert (
        sum(simulation.entities[key].position.x < point.x - 3 for key, point in starts.items())
        >= len(starts) * 0.8
    )
    simulation.advance(340)
    assert sum(simulation.entities[key].position.x < 80 for key in starts) >= len(starts) * 0.75
    simulation.advance(800)
    assert simulation.match_result == 2


def test_small_armies_retain_squads_and_large_armies_do_not_flap_at_the_threshold() -> None:
    small = campaign_army(per_front=59)
    small.advance(10)
    assert not small.opponents["ai"].field_armies
    large = campaign_army(per_front=60)
    large.advance(10)
    assert len(large.opponents["ai"].field_armies) == 5
    membership = _membership(large)
    for key in tuple(membership)[:40]:
        large.remove_entity(key)
    large.advance(20)
    remaining = _membership(large)
    assert len(large.opponents["ai"].field_armies) == 5
    assert all(membership[key] == index for key, index in remaining.items() if key in membership)


def test_army_missions_restore_from_saved_squads_without_serializing_caches() -> None:
    original = Simulation(campaign_army().game_map)
    original.advance(40)
    state = original.export_state()
    assert "field_armies" not in state["opponents"]["ai"]
    restored = load_simulation_data(
        {"schema": MATCH_SAVE_SCHEMA, "map": original.game_map.to_dict(), "state": state},
        record_history=False,
    )
    assert not restored.opponents["ai"].field_armies
    missions = {
        key: squad.target_id
        for squad in restored.opponents["ai"].squads
        for key in squad.entity_ids
    }
    restored.advance(10)
    assert len(restored.opponents["ai"].field_armies) == 5
    assert {
        key: squad.target_id
        for squad in restored.opponents["ai"].squads
        for key in squad.entity_ids
        if key in missions
    } == missions
    assert all(
        restored.entities[key].pursue_target
        for squad in restored.opponents["ai"].squads
        for key in squad.entity_ids
    )
    replayed = run_replay(load_replay_data(capture_replay(original).to_dict()))
    assert replayed.export_state() == original.export_state()


def test_local_pursuit_is_leashed_and_does_not_erase_the_regional_mission() -> None:
    data = campaign_army().game_map.to_dict()
    data["entities"].append(
        {"id": "decoy", "kind": "heavy_tank", "owner": "player", "position": [188.5, 10.5]}
    )
    simulation = Simulation(load_map_data(data), record_history=False)
    simulation.advance(40)
    memory = simulation.opponents["ai"]
    army = memory.field_armies[0]
    objective = army.objective_id
    assert objective is not None
    # Restore a local pursuit whose decoy has escaped the original engagement area.
    decoy = simulation.entities["decoy"]
    origin = Point(120, 40)
    for squad in army.squads:
        squad.phase = SquadPhase.ATTACKING
        squad.destination = origin
        for key in squad.entity_ids:
            simulation.entities[key].attack_target_id = decoy.entity_id
            simulation.entities[key].pursue_target = True
    army.update_tick = -100
    simulation.advance(10)
    assert army.objective_id == objective
    assert all(squad.target_id == objective for squad in army.squads)
    assert all(
        simulation.entities[key].attack_target_id == objective
        for squad in army.squads
        for key in squad.entity_ids
    )


def test_joint_offensive_does_not_cancel_an_armys_local_retreat() -> None:
    simulation = fragmented_army()
    simulation.advance(10)
    army = max(
        simulation.opponents["ai"].field_armies,
        key=lambda group: sum(len(squad.entity_ids) for squad in group.squads),
    )
    members = tuple(key for squad in army.squads for key in squad.entity_ids)
    assert simulation.execute(MoveCommand(members, Point(150, 150), owner_id="ai")).accepted
    for squad in army.squads:
        squad.phase = SquadPhase.RETREATING
        squad.phase_tick = simulation.tick
        squad.destination = Point(150, 150)
    simulation.advance(20)
    assert all(squad.phase is SquadPhase.RETREATING for squad in army.squads)
    assert all(not simulation.entities[key].pursue_target for key in members)


def test_an_adjacent_siege_target_does_not_cancel_active_regrouping() -> None:
    data = campaign_army().game_map.to_dict()
    data["entities"].append(
        {"id": "near_factory", "kind": "factory", "owner": "player", "position": [110, 40]}
    )
    simulation = Simulation(load_map_data(data), record_history=False)
    simulation.advance(10)
    army = min(
        simulation.opponents["ai"].field_armies,
        key=lambda group: min(
            simulation.entities[key].position.distance_to(Point(106, 24))
            for squad in group.squads
            for key in squad.entity_ids
        ),
    )
    assert army.objective_id == "near_factory"
    members = tuple(key for squad in army.squads for key in squad.entity_ids)
    assert simulation.execute(MoveCommand(members, Point(106, 26), owner_id="ai")).accepted
    for squad in army.squads:
        squad.phase = SquadPhase.RETREATING
        squad.phase_tick = simulation.tick
        squad.destination = Point(106, 26)
    issued = [squad.last_order_tick for squad in army.squads]
    army.update_tick = -100
    simulation.advance(10)
    assert "near_factory" in simulation.entities
    assert all(squad.phase is SquadPhase.RETREATING for squad in army.squads)
    # Native support may engage after arrival; the AI must not replace the regroup order.
    assert [squad.last_order_tick for squad in army.squads] == issued


def test_unreachable_high_value_targets_do_not_hide_a_reachable_front() -> None:
    data = campaign_army().game_map.to_dict()
    data["terrain"] = {"default": "grass", "rectangles": [[80, 0, 6, 192, "water"]]}
    data["entities"] = [
        unit for unit in data["entities"] if unit["id"] not in {"north_income", "middle_income"}
    ]
    for entity in data["entities"]:
        if entity["id"] == "middle_factory":
            entity["position"] = [12, 60]
        elif entity["id"] == "south_factory":
            entity["position"] = [12, 100]
    data["entities"].append(
        {"id": "reachable_factory", "kind": "factory", "owner": "player", "position": [180, 180]}
    )
    simulation = Simulation(load_map_data(data), record_history=False)
    for _ in range(6):
        simulation.advance(10)
        assert simulation._opponent_candidate_count <= 2 * opponent_ai.MAX_TARGET_CANDIDATES
    assert {army.objective_id for army in simulation.opponents["ai"].field_armies} == {
        "reachable_factory"
    }
