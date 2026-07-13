"""Performance contracts for saturated destinations and focus-fire crowds."""

from __future__ import annotations

from statistics import median
from time import perf_counter

from airts.automations import DefendParameters
from airts.commands import AttackCommand, CreateDefendCommand, CreatePatrolCommand
from airts.geometry import Point, rectangle_region
from airts.map_model import load_map_data
from airts.simulation import Simulation


def _crowd_simulation(
    unit_count: int,
    *,
    enemy_position: Point | None = None,
    bridge: bool = False,
) -> tuple[Simulation, tuple[str, ...]]:
    columns = 32 if bridge else 40
    entity_ids = tuple(f"scout_{index:04d}" for index in range(unit_count))
    entities: list[dict[str, object]] = [
        {
            "id": entity_id,
            "kind": "scout",
            "owner": "player",
            "position": [2.5 + index % columns, 2.5 + index // columns],
        }
        for index, entity_id in enumerate(entity_ids)
    ]
    if enemy_position is not None:
        entities.append(
            {
                "id": "focus_target",
                "kind": "scout",
                "owner": "enemy",
                "position": [enemy_position.x, enemy_position.y],
            }
        )
    simulation = Simulation(
        load_map_data(
            {
                "id": f"crowd_congestion_{unit_count}",
                "name": "Crowd Congestion Performance",
                "width": 80,
                "height": 60,
                "terrain": {
                    "default": "grass",
                    "rectangles": (
                        [[38, 0, 5, 27, "water"], [38, 33, 5, 27, "water"]] if bridge else []
                    ),
                },
                "entities": entities,
            }
        ),
        random_seed=73,
    )
    if enemy_position is not None:
        simulation.entities["focus_target"].health = 1_000_000
    return simulation, entity_ids


def test_focus_attackers_hold_at_weapon_range_instead_of_converging_on_adjacency() -> None:
    simulation, entity_ids = _crowd_simulation(64, enemy_position=Point(46.5, 5.5))
    result = simulation.execute(AttackCommand(entity_ids, "focus_target"))

    simulation.advance(60)

    target = simulation.entities["focus_target"]
    in_range = tuple(
        simulation.entities[entity_id]
        for entity_id in entity_ids
        if entity_id in simulation.entities
        and simulation.entities[entity_id].position.distance_to(target.position)
        <= simulation.entities[entity_id].kind.profile.attack_range
    )
    assert result.accepted
    assert len(in_range) >= 16
    assert all(not entity.path and entity.move_target is None for entity in in_range)


def test_tiny_defend_area_allocates_distinct_physical_stations() -> None:
    simulation, entity_ids = _crowd_simulation(128)
    target = rectangle_region(Point(60, 28), Point(62, 30))
    result = simulation.execute(CreateDefendCommand(entity_ids, target))

    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    stations = tuple(automation.parameters.stations.values())
    assert result.accepted
    assert len(set(stations)) == len(entity_ids)
    assert (
        min(
            first.distance_to(second)
            for index, first in enumerate(stations)
            for second in stations[index + 1 :]
        )
        >= 0.6
    )


def test_tiny_patrol_area_uses_a_collision_safe_group_formation() -> None:
    simulation, entity_ids = _crowd_simulation(128)
    target = rectangle_region(Point(60, 28), Point(62, 30))
    result = simulation.execute(CreatePatrolCommand(entity_ids, target))

    simulation.advance(8)

    destinations = tuple(simulation.entities[entity_id].move_target for entity_id in entity_ids)
    assert result.accepted
    assert all(destination is not None for destination in destinations)
    concrete = tuple(destination for destination in destinations if destination is not None)
    assert len(set(concrete)) == len(entity_ids)
    assert (
        min(
            first.distance_to(second)
            for index, first in enumerate(concrete)
            for second in concrete[index + 1 :]
        )
        >= 0.6
    )


def test_thousand_scout_tiny_defend_congestion_ticks_fit_realtime_budget() -> None:
    simulation, entity_ids = _crowd_simulation(1_000)
    target = rectangle_region(Point(65, 28), Point(67, 30))
    assert simulation.execute(CreateDefendCommand(entity_ids, target)).accepted
    simulation.advance(180)
    tick_times: list[float] = []

    for _ in range(20):
        started = perf_counter()
        simulation.advance()
        tick_times.append(perf_counter() - started)

    ordered = sorted(tick_times)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    assert p95 < Simulation.TICK_SECONDS, (
        f"tiny-defense ticks had median {median(tick_times) * 1_000:.1f} ms, "
        f"p95 {p95 * 1_000:.1f} ms, max {max(tick_times) * 1_000:.1f} ms, and "
        f"{simulation.collision_pair_check_count} final collision checks"
    )


def test_thousand_scout_bridge_queue_preserves_throughput_and_tick_budget() -> None:
    simulation, entity_ids = _crowd_simulation(1_000, bridge=True)
    target = rectangle_region(Point(65, 28), Point(67, 30))
    assert simulation.execute(CreateDefendCommand(entity_ids, target)).accepted

    simulation.advance(180)
    tick_times: list[float] = []
    for _ in range(20):
        started = perf_counter()
        simulation.advance()
        tick_times.append(perf_counter() - started)

    ordered = sorted(tick_times)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    crossed = sum(simulation.entities[entity_id].position.x > 45 for entity_id in entity_ids)
    assert crossed >= 500, f"only {crossed} of 1,000 scouts crossed by tick 200"
    assert p95 < Simulation.TICK_SECONDS, (
        f"bridge ticks had median {median(tick_times) * 1_000:.1f} ms, "
        f"p95 {p95 * 1_000:.1f} ms, and max {max(tick_times) * 1_000:.1f} ms"
    )


def test_thousand_entity_focus_attack_holds_range_and_fits_realtime_budget() -> None:
    simulation, entity_ids = _crowd_simulation(999, enemy_position=Point(70.5, 30.5))
    assert simulation.execute(AttackCommand(entity_ids, "focus_target")).accepted
    simulation.advance(200)
    tick_times: list[float] = []

    for _ in range(20):
        started = perf_counter()
        simulation.advance()
        tick_times.append(perf_counter() - started)

    ordered = sorted(tick_times)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    target = simulation.entities["focus_target"]
    in_range = tuple(
        simulation.entities[entity_id]
        for entity_id in entity_ids
        if entity_id in simulation.entities
        and simulation.entities[entity_id].position.distance_to(target.position)
        <= simulation.entities[entity_id].kind.profile.attack_range
    )
    assert len(in_range) >= 24
    assert all(not entity.path and entity.move_target is None for entity in in_range)
    assert p95 < Simulation.TICK_SECONDS, (
        f"focus ticks had median {median(tick_times) * 1_000:.1f} ms, "
        f"p95 {p95 * 1_000:.1f} ms, max {max(tick_times) * 1_000:.1f} ms, and "
        f"{len(simulation.projectiles)} live projectiles"
    )
