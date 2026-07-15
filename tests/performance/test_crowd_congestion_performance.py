"""Performance contracts for saturated destinations and focus-fire crowds."""

from __future__ import annotations

from statistics import median
from time import perf_counter

from airts.automations import DefendParameters
from airts.commands import AttackCommand, CreateDefendCommand, CreatePatrolCommand, MoveCommand
from airts.geometry import Point, rectangle_region
from airts.map_model import load_map_data
from airts.simulation import Simulation
from airts.spatial_index import SpatialIndex

LARGE_FORMATION_UNIT_COUNT = 400
BRIDGE_UNIT_COUNT = 400
MINIMUM_SETTLED_SCOUT_SPACING = 0.90
MAXIMUM_MOVING_OVERLAP_FRACTION = 0.10
SCOUT_CONTACT_DISTANCE = 0.60
BRIDGE_EAST_BANK_X = 63.5


def _crowd_simulation(
    unit_count: int,
    *,
    enemy_position: Point | None = None,
    bridge: bool = False,
) -> tuple[Simulation, tuple[str, ...]]:
    if bridge:
        columns = 20
        start_x = 8.5
        start_y = 30.5
        map_width = 120
        map_height = 80
        terrain_rectangles = [[58, 0, 5, 35, "water"], [58, 44, 5, 36, "water"]]
    else:
        columns = 40
        start_x = 2.5
        start_y = 2.5
        map_width = 80
        map_height = 60
        terrain_rectangles = []
    entity_ids = tuple(f"scout_{index:04d}" for index in range(unit_count))
    entities: list[dict[str, object]] = [
        {
            "id": entity_id,
            "kind": "scout",
            "owner": "player",
            "position": [start_x + index % columns, start_y + index // columns],
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
                "width": map_width,
                "height": map_height,
                "terrain": {
                    "default": "grass",
                    "rectangles": terrain_rectangles,
                },
                "entities": entities,
            }
        ),
        random_seed=73,
    )
    if enemy_position is not None:
        simulation.entities["focus_target"].health = 1_000_000
    return simulation, entity_ids


def _minimum_unit_separation(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    search_radius: float,
) -> float:
    positions = {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids}
    index = SpatialIndex(positions)
    return min(
        (
            positions[first_id].distance_to(positions[second_id])
            for first_id, second_id in index.candidate_pairs(search_radius)
        ),
        default=float("inf"),
    )


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
        >= MINIMUM_SETTLED_SCOUT_SPACING
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
        >= MINIMUM_SETTLED_SCOUT_SPACING
    )


def test_crowded_waypoint_lookahead_preserves_the_bridge_turn() -> None:
    simulation, entity_ids = _crowd_simulation(8, bridge=True)
    mover = simulation.entities[entity_ids[0]]
    allowed_conflicts = frozenset(entity_ids)
    mover_position = Point(57.5, 28.5)
    simulation.occupancy.move(mover.entity_id, frozenset({(57, 28)}), allowed_conflicts)
    mover.position = mover_position
    for y, entity_id in zip(range(29, 36), entity_ids[1:], strict=True):
        blocker = simulation.entities[entity_id]
        blocker_position = Point(57.5, y + 0.5)
        simulation.occupancy.move(entity_id, frozenset({(57, y)}), allowed_conflicts)
        blocker.position = blocker_position
    mover.path = [
        *(Point(57.5, y + 0.5) for y in range(29, 36)),
        *(Point(x + 0.5, 35.5) for x in range(58, 66)),
    ]

    simulation._skip_crowded_waypoints(mover)

    assert mover.path[0] == Point(57.5, 35.5)


def test_large_scout_move_flows_past_a_stationary_enemy_heavy_without_jitter() -> None:
    scout_count = 152
    scout_ids = tuple(f"scout_{index:04d}" for index in range(scout_count))
    simulation = Simulation(
        load_map_data(
            {
                "id": "stationary_heavy_anchor",
                "name": "Stationary Heavy Anchor",
                "width": 44,
                "height": 44,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [8.5 + index % 19, 26.5 + index // 19],
                        }
                        for index, entity_id in enumerate(scout_ids)
                    ),
                    {
                        "id": "stationary_heavy",
                        "kind": "heavy_tank",
                        "owner": "enemy",
                        "position": [17.5, 20.5],
                    },
                ],
            }
        ),
        random_seed=91,
    )
    simulation.entities["stationary_heavy"].health = 1_000_000
    starts = {entity_id: simulation.entities[entity_id].position for entity_id in scout_ids}
    heavy_start = simulation.entities["stationary_heavy"].position

    assert simulation.execute(MoveCommand(scout_ids, Point(17.5, 4.5))).accepted
    for _ in range(50):
        simulation.advance()
        assert simulation.entities["stationary_heavy"].position == heavy_start

    progressed = sum(
        starts[entity_id].y - simulation.entities[entity_id].position.y >= 8.0
        for entity_id in scout_ids
        if entity_id in simulation.entities
    )
    assert progressed >= 145, (
        f"only {progressed} of {scout_count} scouts made northward progress around the anchor"
    )


def test_large_tiny_defend_formation_settles_with_clearance_and_realtime_ticks() -> None:
    simulation, entity_ids = _crowd_simulation(LARGE_FORMATION_UNIT_COUNT)
    target = rectangle_region(Point(65, 28), Point(67, 30))
    result = simulation.execute(CreateDefendCommand(entity_ids, target))
    assert result.accepted
    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    tick_times: list[float] = []

    unsettled = len(entity_ids)
    while simulation.tick < 800 and unsettled:
        started = perf_counter()
        simulation.advance()
        tick_times.append(perf_counter() - started)
        unsettled = sum(
            bool(simulation.entities[entity_id].path)
            or simulation.entities[entity_id].position.distance_to(
                automation.parameters.stations[entity_id]
            )
            > Simulation.DEFEND_FORMATION_TOLERANCE
            for entity_id in entity_ids
        )

    ordered = sorted(tick_times)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    center = Point(66, 29)
    distances = sorted(
        simulation.entities[entity_id].position.distance_to(
            automation.parameters.stations[entity_id]
        )
        for entity_id in entity_ids
    )
    assert unsettled == 0, (
        f"{unsettled} of {len(entity_ids)} defenders remained unsettled at tick {simulation.tick}; "
        f"{sum(bool(simulation.entities[item].path) for item in entity_ids)} retained paths, "
        f"{sum(simulation.entities[item].congestion_stopped for item in entity_ids)} yielded, "
        f"{sum(simulation.entities[item].collision_pressure > 0 for item in entity_ids)} under collision pressure, "
        f"{sum(simulation.entities[item].route_ticks >= Simulation.DESTINATION_REPATH_TICKS for item in entity_ids)} on mature routes, "
        f"{sum(simulation.entities[item].position.distance_to(center) <= automation.parameters.assembly_radius + Simulation.DEFEND_FORMATION_TOLERANCE for item in entity_ids)} inside the assembly envelope, "
        f"{sum(distance <= 1.0 for distance in distances)} were within one unit, "
        f"median station distance {distances[len(distances) // 2]:.2f}, "
        f"maximum {distances[-1]:.2f}, and "
        f"{simulation.navigation_field_build_count} navigation fields were built; "
        f"measured tick p95 was {p95 * 1_000:.1f} ms"
    )
    assert all(not simulation.entities[entity_id].path for entity_id in entity_ids)
    assert all(
        simulation.entities[entity_id].position.distance_to(center)
        <= automation.parameters.assembly_radius + Simulation.DEFEND_FORMATION_TOLERANCE
        for entity_id in entity_ids
    )
    minimum_separation = _minimum_unit_separation(
        simulation,
        entity_ids,
        MINIMUM_SETTLED_SCOUT_SPACING,
    )
    assert minimum_separation >= MINIMUM_SETTLED_SCOUT_SPACING, (
        f"settled defenders were only {minimum_separation:.3f} units apart"
    )
    assert p95 < Simulation.TICK_SECONDS, (
        f"tiny-defense ticks had median {median(tick_times) * 1_000:.1f} ms, "
        f"p95 {p95 * 1_000:.1f} ms, max {max(tick_times) * 1_000:.1f} ms, and "
        f"{simulation.collision_pair_check_count} final collision checks"
    )


def test_large_bridge_queue_fully_crosses_without_deep_overlap() -> None:
    simulation, entity_ids = _crowd_simulation(BRIDGE_UNIT_COUNT, bridge=True)
    target = rectangle_region(Point(100, 38), Point(102, 40))
    result = simulation.execute(CreateDefendCommand(entity_ids, target))
    assert result.accepted
    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    assert all(
        station.x > BRIDGE_EAST_BANK_X for station in automation.parameters.stations.values()
    )

    crossed = 0
    while simulation.tick < 1_200 and crossed < len(entity_ids):
        simulation.advance()
        crossed = sum(
            simulation.entities[entity_id].position.x >= BRIDGE_EAST_BANK_X
            for entity_id in entity_ids
        )

    assert crossed == len(entity_ids), (
        f"only {crossed} of {len(entity_ids)} scouts crossed; every ordered unit must reach "
        f"the east bank even if bridge throughput is slow"
    )

    minimum_separation = _minimum_unit_separation(
        simulation,
        entity_ids,
        SCOUT_CONTACT_DISTANCE,
    )
    required_separation = SCOUT_CONTACT_DISTANCE * (1.0 - MAXIMUM_MOVING_OVERLAP_FRACTION)
    assert minimum_separation >= required_separation, (
        f"bridge force crossed with {minimum_separation:.3f} center separation; overlap may not "
        f"exceed {MAXIMUM_MOVING_OVERLAP_FRACTION:.0%} of scout contact distance"
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
