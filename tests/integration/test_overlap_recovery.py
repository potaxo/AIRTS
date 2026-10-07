"""Authoritative severe-overlap recovery; preparation injection is not a player replay."""

from __future__ import annotations

from math import hypot

import numpy as np
import pytest
from tests.integration.recovery_checks import RecoveryChecks, _disc_metrics, _positions
from tests.integration.scenarios import injected_overlap_simulation

from airts.contracts.automations import DefendParameters
from airts.contracts.commands import (
    CreateDefendCommand,
    HoldPositionCommand,
    MoveCommand,
    StopCommand,
)
from airts.contracts.spatial import Point, rectangle_region
from airts.navigation.collision import collision_radius
from airts.navigation.pathfinding import RoutingService
from airts.simulation import Simulation
from airts.systems.movement import position_clear
from airts.world._numeric import FloatArray
from airts.world.entities import UnitState
from airts.world.map import EntityKind


def _assert_recovers_and_escapes(
    simulation: Simulation,
    ids: tuple[str, ...],
    selected: tuple[str, ...],
    *,
    maximum_ticks: int = 600,
) -> tuple[int, FloatArray]:
    identities = {k: simulation.entities[k] for k in ids}
    goals = {k: simulation.entities[k].move_target for k in selected}
    assert all(p is not None for p in goals.values())
    # Each accepted Move has its own placement envelope, including opposite squads.
    # Completed bodies become Idle and can still yield to their later arrivals.
    pending = set(selected)
    destinations: dict[Point, list[str]] = {}
    for record in reversed(simulation.command_history):
        command = record["command"]
        if command["type"] != "move":
            continue
        members = [key for key in command["entity_ids"] if key in pending]
        if members:
            center = Point(*command["target"])
            destinations.setdefault(center, []).extend(members)
            pending.difference_update(members)
    assert not pending, "selected recovery bodies must retain their accepted Move command"
    before = initial = _positions(simulation, ids)
    checks = RecoveryChecks(simulation, ids)
    initial_depth = _disc_metrics(before, before, checks.radii, checks.speeds)[3]
    observed_depth = initial_depth
    for _ in range(maximum_ticks):
        before, metrics = checks.advance(before)
        if simulation.tick == 100:
            observed_depth = metrics[3]
            assert observed_depth < initial_depth * 0.75, (
                "available area did not release meaningful capacity within 100 ticks"
            )
        if metrics[2] <= 1e-7 and all(not simulation.entities[k].path for k in selected):
            break
    else:
        pytest.fail(
            f"{len(ids)} bodies at tick {simulation.tick}: {metrics[4]} overlaps, maximum depth {metrics[2]:.6f}; {sum(not simulation.entities[k].path for k in selected)}/{len(selected)} selected completed"
        )
    assert set(simulation.entities) == set(ids)
    assert all(simulation.entities[k] is identities[k] for k in ids)
    assert all(
        simulation.entities[k].position.distance_to(Point(*initial[ids.index(k)])) > 15
        for k in selected
    ), "internal selection did not physically leave the original cluster"
    for key, goal in goals.items():
        assert goal is not None
        completed = next(
            event
            for event in reversed(simulation.events.events)
            if event.subject_id == key and event.event_type.value == "movement_completed"
        )
        assert Point(*completed.details["position"]).distance_to(goal) <= 1e-8, (
            "movement completion must reach each accepted destination"
        )
    for center, members in destinations.items():
        envelope = max(
            goal.distance_to(center) for key in members if (goal := goals[key]) is not None
        )
        contact = 2 * max(collision_radius(simulation.entities[key].kind) for key in members)
        assert all(
            simulation.entities[key].position.distance_to(center) <= envelope + contact
            for key in members
        ), "post-arrival yielding left the accepted group placement"
        initial_center = initial[[ids.index(key) for key in members]].mean(axis=0)
        hx, hy = center.x - float(initial_center[0]), center.y - float(initial_center[1])
        heading_length = hypot(hx, hy)
        assert heading_length > 0
        hx, hy = hx / heading_length, hy / heading_length
        passive_frontier = max(
            entity.position.x * hx + entity.position.y * hy + collision_radius(entity.kind)
            for key in ids
            if key not in selected
            for entity in (simulation.entities[key],)
        )
        selected_frontier = min(
            entity.position.x * hx + entity.position.y * hy - collision_radius(entity.kind)
            for key in members
            for entity in (simulation.entities[key],)
        )
        final_goal_distances = {
            key: simulation.entities[key].position.distance_to(goal)
            for key in members
            if (goal := goals[key]) is not None
        }
        assert selected_frontier > passive_frontier, (
            f"not every selected body passed the passive formation's frontier: "
            f"selected {selected_frontier}, passive {passive_frontier}; "
            f"final accepted-goal distances {final_goal_distances}"
        )
    return simulation.tick, before


@pytest.mark.parametrize("unit_count", [100, 400, 1000])
@pytest.mark.parametrize(
    "spacing_ratio",
    [0.8, 0.5, 0.3, 0.0],
    ids=["80-percent", "50-percent", "30-percent", "coincident"],
)
def test_severe_overlap_recovers_every_body_and_extracts_entire_internal_squad(
    unit_count: int, spacing_ratio: float
) -> None:
    simulation, ids, selected = injected_overlap_simulation(unit_count, spacing_ratio)
    assert simulation.execute(MoveCommand(selected, Point(80, 48))).accepted
    _, before = _assert_recovers_and_escapes(simulation, ids, selected)
    rested = before.copy()
    checks = RecoveryChecks(simulation, ids)
    for _ in range(40):
        before, metrics = checks.advance(before)
        assert metrics[2] <= 1e-7
    assert float(np.linalg.norm(before - rested, axis=1).max()) < 0.05, (
        "released formation keeps drifting or jittering without pressure"
    )


@pytest.mark.parametrize("selected_count", [1, 9], ids=["single", "squad"])
@pytest.mark.parametrize(
    "target", [Point(16, 48), Point(48, 16), Point(48, 80)], ids=["west", "north", "south"]
)
def test_internal_selection_escapes_in_other_directions(selected_count: int, target: Point) -> None:
    simulation, ids, selected = injected_overlap_simulation(100, 0.3, selected_count=selected_count)
    assert simulation.execute(MoveCommand(selected, target)).accepted
    _assert_recovers_and_escapes(simulation, ids, selected)


def test_two_internal_squads_escape_opposite_directions_without_sharing_orders() -> None:
    simulation, ids, selected = injected_overlap_simulation(100, 0.3, selected_count=10)
    assert simulation.execute(MoveCommand(selected[:5], Point(80, 48))).accepted
    assert simulation.execute(MoveCommand(selected[5:], Point(16, 48))).accepted
    _assert_recovers_and_escapes(simulation, ids, selected)


@pytest.mark.parametrize("behavior", ["idle", "stop", "hold", "settled-defend"])
def test_mixed_discs_preserve_passive_behavior_and_conflicting_anchors(behavior: str) -> None:
    simulation, ids, selected = injected_overlap_simulation(100, 0.3)
    kinds = (EntityKind.SCOUT, EntityKind.LIGHT_TANK, EntityKind.HEAVY_TANK, EntityKind.BUILDER)
    for index, key in enumerate(ids):
        # Preparation only: mixed profiles share the same explicitly injected body lattice.
        simulation.entities[key].kind = kinds[index % len(kinds)]
    passive = tuple(k for k in ids if k not in selected)
    anchors = {k: simulation.entities[k].position for k in passive}
    stations = None
    automation_id = None
    if behavior == "stop":
        assert simulation.execute(StopCommand(passive)).accepted
    elif behavior == "hold":
        assert simulation.execute(HoldPositionCommand(passive)).accepted
    elif behavior == "settled-defend":
        result = simulation.execute(
            CreateDefendCommand(passive, rectangle_region(Point(44, 44), Point(52, 52)))
        )
        assert result.accepted and result.automation_id is not None
        automation_id = result.automation_id
        parameters = simulation.automations[automation_id].parameters
        assert isinstance(parameters, DefendParameters)
        # Artificial conflicting station preparation verifies safe waiting instead of rematching.
        parameters.stations = dict(anchors)
        stations = dict(parameters.stations)
        for key in passive:
            entity = simulation.entities[key]
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.DEFENDING
            entity.defend_arrived = True
    assert simulation.execute(MoveCommand(selected, Point(80, 48))).accepted
    _, before = _assert_recovers_and_escapes(simulation, ids, selected)
    rested = before.copy()
    checks = RecoveryChecks(simulation, ids)
    for _ in range(40):
        before, metrics = checks.advance(before)
        assert metrics[2] <= 1e-7
    assert float(np.linalg.norm(before - rested, axis=1).max()) < 0.08
    if behavior == "hold":
        assert all(
            simulation.entities[k].hold_anchor == anchors[k]
            and simulation.entities[k].state is UnitState.HOLDING
            for k in passive
        )
    if stations is not None:
        parameters = simulation.automations[automation_id or ""].parameters
        assert isinstance(parameters, DefendParameters) and parameters.stations == stations
        assert all(simulation.assignments[k] == automation_id for k in passive)
    if stations is None:
        assert all(simulation.entities[k].move_target is None for k in passive)
    else:
        assert all(simulation.entities[k].move_target in (None, stations[k]) for k in passive)


def test_wall_bounded_overlap_releases_through_available_exit() -> None:
    simulation, ids, selected = injected_overlap_simulation(100, 0.3)
    # Shift the prepared cluster beside the map's hard west boundary; ample grass remains east.
    minimum_x = min(simulation.entities[k].position.x for k in ids)
    for key in ids:
        entity = simulation.entities[key]
        entity.position = Point(entity.position.x - minimum_x + 0.31, entity.position.y)
        simulation.occupancy.move(key, entity.occupied_cells, frozenset(ids))
    assert simulation.execute(MoveCommand(selected, Point(35, 48))).accepted
    _assert_recovers_and_escapes(simulation, ids, selected)


def test_closed_insufficient_area_reports_constraint_and_retains_move_intent() -> None:
    simulation, ids, selected = injected_overlap_simulation(100, 0.3)
    # A 4x4 enclosure cannot fit 100 radius-.3 discs (disk area alone is >28).
    from dataclasses import replace

    from airts.world.map import Terrain

    terrain = tuple(
        tuple(Terrain.GRASS if 46 <= x < 50 and 46 <= y < 50 else Terrain.ROCK for x in range(96))
        for y in range(96)
    )
    simulation.game_map = replace(simulation.game_map, terrain=terrain)
    simulation._terrain_obstacles = frozenset(
        (x, y) for y in range(96) for x in range(96) if terrain[y][x] is Terrain.ROCK
    )
    simulation._routes = RoutingService(
        simulation.game_map,
        automation_budget=simulation.TOTAL_AUTOMATION_ROUTE_BUDGET,
        combat_budget=simulation.COMBAT_PATH_BUDGET,
    )
    simulation._invalidate_navigation_cache()
    # The command remains statically reachable inside the enclosure, but its discs lack capacity.
    assert simulation.execute(MoveCommand(selected, Point(49, 48))).accepted
    before = _positions(simulation, ids)
    blocked = False
    checks = RecoveryChecks(simulation, ids)
    for _ in range(100):
        before, metrics = checks.advance(before)
        blocked |= simulation.contact_blocked_component_count > 0
        for key in ids:
            entity = simulation.entities[key]
            assert position_clear(
                entity.position,
                collision_radius(entity.kind),
                96,
                96,
                simulation._terrain_obstacles,
            )
    assert blocked
    assert metrics[2] > 0.01
    assert any(simulation.entities[k].path for k in selected)


def test_recovery_is_deterministic_with_reversed_entity_insertion_order() -> None:
    first, ids, selected = injected_overlap_simulation(100, 0.0)
    second, _, second_selected = injected_overlap_simulation(100, 0.0)
    second.entities = dict(reversed(tuple(second.entities.items())))
    assert second_selected == selected
    assert first.execute(MoveCommand(selected, Point(80, 48))).accepted
    assert second.execute(MoveCommand(selected, Point(80, 48))).accepted
    for _ in range(200):
        first.advance()
        second.advance()
        assert first.export_state() == second.export_state()
    assert all(
        not first.entities[key].path and first.entities[key].position.x > 75 for key in selected
    )
    positions = _positions(first, ids)
    assert (
        _disc_metrics(positions, positions, np.array([0.3] * len(ids)), np.array([0.5] * len(ids)))[
            2
        ]
        <= 1e-7
    )


@pytest.mark.parametrize("spacing_ratio", [0.3, 0.0], ids=["dense", "coincident"])
def test_recovery_tolerates_reversed_stable_ids(spacing_ratio: float) -> None:
    from dataclasses import replace

    from airts.world.map import OccupancyGrid

    simulation, ids, selected = injected_overlap_simulation(100, spacing_ratio)
    replacements = {key: f"reversed_{99 - index:04d}" for index, key in enumerate(ids)}
    simulation.game_map = replace(
        simulation.game_map,
        entities=tuple(
            replace(spec, entity_id=replacements[spec.entity_id])
            for spec in simulation.game_map.entities
        ),
    )
    simulation.entities = {replacements[key]: entity for key, entity in simulation.entities.items()}
    simulation.occupancy = OccupancyGrid(96, 96)
    ids = tuple(replacements[key] for key in ids)
    selected = tuple(replacements[key] for key in selected)
    for key, entity in simulation.entities.items():
        entity.entity_id = key
        simulation.occupancy.place(key, entity.occupied_cells, frozenset(ids))
    assert simulation.execute(MoveCommand(selected, Point(80, 48))).accepted
    _assert_recovers_and_escapes(simulation, ids, selected)


def test_internal_squad_keeps_escaping_while_later_legal_force_arrives() -> None:
    from dataclasses import replace

    from airts.world.entities import Entity
    from airts.world.map import EntitySpec

    simulation, original_ids, selected = injected_overlap_simulation(100, 0.3)
    incoming = tuple(f"arrival_{index:03d}" for index in range(25))
    specs = tuple(
        EntitySpec(key, EntityKind.SCOUT, "player", Point(25.5 + index % 5, 42.5 + index // 5))
        for index, key in enumerate(incoming)
    )
    simulation.game_map = replace(
        simulation.game_map, entities=simulation.game_map.entities + specs
    )
    for spec in specs:
        entity = Entity(
            spec.entity_id, spec.kind, spec.owner_id, spec.position, spec.kind.profile.max_health
        )
        simulation.entities[spec.entity_id] = entity
        simulation.occupancy.place(spec.entity_id, entity.occupied_cells)
    ids = original_ids + incoming
    assert simulation.execute(MoveCommand(selected, Point(80, 48))).accepted
    assert simulation.execute(MoveCommand(incoming, Point(48, 48))).accepted
    _assert_recovers_and_escapes(simulation, ids, selected)
    assert all(simulation.entities[k].owner_id == "player" for k in ids)
