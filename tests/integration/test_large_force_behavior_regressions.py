"""Regression contracts for user-visible large-force movement behavior."""

from __future__ import annotations

from statistics import median

from airts.automations import DefendParameters, ProductionParameters
from airts.commands import (
    CreateDefendCommand,
    CreateProductionCommand,
    HoldPositionCommand,
    MoveCommand,
)
from airts.geometry import Point, rectangle_region
from airts.simulation import Simulation
from airts.world.map_model import EntityKind, load_map_data


def _river_terrain() -> dict[str, object]:
    return {
        "default": "grass",
        "rectangles": [
            [30, 0, 4, 30, "water"],
            [30, 34, 4, 30, "water"],
            [30, 30, 4, 4, "bridge"],
        ],
    }


def _station_distances(simulation: Simulation, parameters: DefendParameters) -> list[float]:
    return sorted(
        simulation.entities[entity_id].position.distance_to(parameters.stations[entity_id])
        for entity_id in parameters.stations
    )


def _same_direction_heavy_force() -> tuple[Simulation, tuple[str, ...]]:
    entity_ids = tuple(f"heavy_{index:03d}" for index in range(140))
    simulation = Simulation(
        load_map_data(
            {
                "id": "same_direction_identity_stability",
                "name": "Same-direction identity stability",
                "width": 80,
                "height": 40,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "heavy_tank",
                        "owner": "player",
                        "position": [5.5 + index % 14, 12.5 + index // 14],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=101,
    )
    assert simulation.execute(MoveCommand(entity_ids, Point(65.5, 20.5))).accepted
    return simulation, entity_ids


def test_same_direction_large_force_never_exchanges_exact_unit_positions() -> None:
    """A traffic optimization may change lanes, but identities must not flicker by swapping."""

    simulation, entity_ids = _same_direction_heavy_force()

    simulation.advance()
    previous = {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids}
    exchanged_pairs: set[tuple[str, str]] = set()
    for _ in range(40):
        simulation.advance()
        current = {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids}
        previous_occupants = {position: entity_id for entity_id, position in previous.items()}
        for entity_id, position in current.items():
            other_id = previous_occupants.get(position)
            if (
                other_id is not None
                and other_id != entity_id
                and current[other_id] == previous[entity_id]
            ):
                exchanged_pairs.add(tuple(sorted((entity_id, other_id))))
        previous = current

    assert not exchanged_pairs, (
        f"{len(exchanged_pairs)} exact position exchanges occurred in 40 ticks; swapping entity "
        "identities makes selection outlines, health bars, and mixed unit sprites flicker"
    )


def test_unobstructed_large_force_front_rank_moves_without_stop_and_go_bursts() -> None:
    """Average speed may not be implemented as long pauses followed by full-slot jumps."""

    simulation, entity_ids = _same_direction_heavy_force()
    front_ids = tuple(entity_id for entity_id in entity_ids if int(entity_id[-3:]) % 14 == 13)
    previous = {entity_id: simulation.entities[entity_id].position for entity_id in front_ids}
    stationary_runs = {entity_id: 0 for entity_id in front_ids}
    maximum_stationary_runs = {entity_id: 0 for entity_id in front_ids}
    maximum_step = {entity_id: 0.0 for entity_id in front_ids}

    for _ in range(40):
        simulation.advance()
        for entity_id in front_ids:
            entity = simulation.entities[entity_id]
            displacement = entity.position.distance_to(previous[entity_id])
            maximum_step[entity_id] = max(maximum_step[entity_id], displacement)
            if displacement <= 1e-9:
                stationary_runs[entity_id] += 1
                maximum_stationary_runs[entity_id] = max(
                    maximum_stationary_runs[entity_id], stationary_runs[entity_id]
                )
            else:
                stationary_runs[entity_id] = 0
            previous[entity_id] = entity.position

    allowed_step = (
        max(simulation.entities[item].speed for item in front_ids) * Simulation.TICK_SECONDS
    )
    assert max(maximum_step.values()) <= allowed_step + 1e-6, (
        f"front-rank units jumped {max(maximum_step.values()):.3f} units in one tick; "
        f"their physical speed permits only {allowed_step:.3f}"
    )
    assert max(maximum_stationary_runs.values()) <= 1, (
        f"an unobstructed front-rank unit remained stationary for "
        f"{max(maximum_stationary_runs.values())} consecutive ticks before its next burst"
    )


def test_large_defend_order_keeps_making_progress_through_the_bridge() -> None:
    """A defend formation may queue at a choke, but it may not enter a permanent stall."""

    entity_ids = tuple(f"scout_{index:03d}" for index in range(307))
    simulation = Simulation(
        load_map_data(
            {
                "id": "large_defend_bridge_progress",
                "name": "Large defend bridge progress",
                "width": 64,
                "height": 64,
                "terrain": _river_terrain(),
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": [40.5 + index % 18, 22.5 + index // 18],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=103,
    )
    target = rectangle_region(Point(3, 8), Point(23, 27))
    result = simulation.execute(CreateDefendCommand(entity_ids, target))
    assert result.accepted
    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    assert all(station.x < 30 for station in automation.parameters.stations.values())

    starting_median = median(_station_distances(simulation, automation.parameters))
    crossed = 0
    last_progress_tick = simulation.tick
    while simulation.tick < 2_000 and crossed < len(entity_ids):
        simulation.advance()
        next_crossed = sum(
            simulation.entities[entity_id].position.x < 30 for entity_id in entity_ids
        )
        if next_crossed > crossed:
            crossed = next_crossed
            last_progress_tick = simulation.tick
        assert simulation.tick - last_progress_tick < 250, (
            f"defend traffic stalled for 250 ticks with {crossed}/{len(entity_ids)} units "
            "across the bridge"
        )

    ending_median = median(_station_distances(simulation, automation.parameters))
    assert crossed == len(entity_ids), (
        f"only {crossed}/{len(entity_ids)} defenders crossed within the generous deadlock guard"
    )
    assert ending_median < starting_median * 0.25


def test_overlapping_manual_defend_orders_share_collision_safe_area_stations() -> None:
    """A new defend order must account for defenders already assigned to the same area."""

    first_ids = tuple(f"first_{index:03d}" for index in range(80))
    second_ids = tuple(f"second_{index:03d}" for index in range(80))
    simulation = Simulation(
        load_map_data(
            {
                "id": "shared_manual_defense",
                "name": "Shared manual defense",
                "width": 64,
                "height": 64,
                "terrain": _river_terrain(),
                "entities": [
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [5.5 + index % 10, 10.5 + index // 10],
                        }
                        for index, entity_id in enumerate(first_ids)
                    ),
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [42.5 + index % 10, 22.5 + index // 10],
                        }
                        for index, entity_id in enumerate(second_ids)
                    ),
                ],
            }
        ),
        random_seed=105,
    )
    target = rectangle_region(Point(3, 8), Point(23, 27))
    results = (
        simulation.execute(CreateDefendCommand(first_ids, target)),
        simulation.execute(CreateDefendCommand(second_ids, target)),
    )
    assert all(result.accepted for result in results)
    parameters = tuple(
        simulation.automations[result.automation_id or ""].parameters for result in results
    )
    assert all(isinstance(item, DefendParameters) for item in parameters)
    stations = tuple(
        station
        for item in parameters
        if isinstance(item, DefendParameters)
        for station in item.stations.values()
    )

    assert len(stations) == 160
    assert len(set(stations)) == len(stations), (
        "separate manual defend automations assigned duplicate physical stations in the same area"
    )
    minimum_separation = min(
        first.distance_to(second)
        for index, first in enumerate(stations)
        for second in stations[index + 1 :]
    )
    assert minimum_separation >= 0.90

    previous_stations = {
        entity_id: station
        for item in parameters
        if isinstance(item, DefendParameters)
        for entity_id, station in item.stations.items()
    }
    assert simulation.remove_entity(second_ids[-1], "CASUALTY_REGRESSION").accepted
    live_stations = {
        entity_id: station
        for result in results
        for item in (simulation.automations[result.automation_id or ""].parameters,)
        if isinstance(item, DefendParameters)
        for entity_id, station in item.stations.items()
    }
    assert len(live_stations) == 159
    assert len(set(live_stations.values())) == len(live_stations)
    changed = sum(
        previous_stations[entity_id] != station for entity_id, station in live_stations.items()
    )
    assert changed <= 1, f"one casualty globally rematched {changed} surviving defenders"


def test_multiple_factories_share_one_collision_safe_defense_formation() -> None:
    """Factories reinforcing one area must not allocate duplicate independent stations."""

    simulation = Simulation(
        load_map_data(
            {
                "id": "factory_defense_group_shape",
                "name": "Factory defense group shape",
                "width": 64,
                "height": 64,
                "terrain": _river_terrain(),
                "entities": [
                    {
                        "id": f"factory_{index}",
                        "kind": "factory",
                        "owner": "player",
                        "position": [5 + index * 6, 42],
                    }
                    for index in range(4)
                ],
            }
        ),
        random_seed=107,
    )
    simulation.resources["player"] = 1_000_000
    target = rectangle_region(Point(48, 22), Point(52, 28))
    results = tuple(
        simulation.execute(
            CreateProductionCommand(
                f"factory_{index}",
                EntityKind.SCOUT,
                33,
                defend_target=target,
            )
        )
        for index in range(4)
    )
    assert all(result.accepted for result in results)
    productions = tuple(simulation.automations[result.automation_id or ""] for result in results)
    assert all(isinstance(item.parameters, ProductionParameters) for item in productions)

    while simulation.tick < 2_000 and any(
        isinstance(item.parameters, ProductionParameters) and item.parameters.produced_count < 33
        for item in productions
    ):
        simulation.advance()
    production_parameters = tuple(
        item.parameters for item in productions if isinstance(item.parameters, ProductionParameters)
    )
    assert len(production_parameters) == 4
    assert all(parameters.produced_count == 33 for parameters in production_parameters)
    defends = tuple(
        simulation.automations[parameters.defend_automation_id or ""]
        for parameters in production_parameters
    )
    assert all(isinstance(item.parameters, DefendParameters) for item in defends)
    defend_parameters = tuple(
        item.parameters for item in defends if isinstance(item.parameters, DefendParameters)
    )
    all_stations = {
        entity_id: station
        for parameters in defend_parameters
        for entity_id, station in parameters.stations.items()
    }
    assert len(all_stations) == 132
    assert len(set(all_stations.values())) == len(all_stations), (
        "factories assigned duplicate stations because each linked defend automation planned "
        "the same area independently"
    )
    minimum_station_separation = min(
        first.distance_to(second)
        for index, first in enumerate(all_stations.values())
        for second in tuple(all_stations.values())[index + 1 :]
    )
    assert minimum_station_separation >= 0.90

    unsettled = len(all_stations)
    while simulation.tick < 4_000 and unsettled:
        simulation.advance()
        unsettled = sum(
            bool(simulation.entities[entity_id].path)
            or simulation.entities[entity_id].position.distance_to(all_stations[entity_id])
            > Simulation.DEFEND_FORMATION_TOLERANCE
            for entity_id in all_stations
        )

    positions = [simulation.entities[entity_id].position for entity_id in all_stations]
    x_span = max(point.x for point in positions) - min(point.x for point in positions)
    y_span = max(point.y for point in positions) - min(point.y for point in positions)
    assert unsettled == 0, (
        f"{unsettled}/{len(all_stations)} factory-produced defenders remained on their "
        "routes instead of joining the defense group"
    )
    assert x_span >= 5 and y_span >= 5, (
        f"factory defenders formed a route-like line with spans x={x_span:.2f}, y={y_span:.2f}"
    )
    assert max(x_span, y_span) / min(x_span, y_span) <= 2.5


def test_continuous_factory_reinforcements_preserve_existing_defend_stations() -> None:
    """A newly produced unit must not turn the whole existing defense around."""

    defender_ids = tuple(f"defender_{index:03d}" for index in range(296))
    simulation = Simulation(
        load_map_data(
            {
                "id": "stable_factory_reinforcements",
                "name": "Stable factory reinforcements",
                "width": 80,
                "height": 64,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [3.5 + index % 20, 5.5 + index // 20],
                        }
                        for index, entity_id in enumerate(defender_ids)
                    ),
                    *(
                        {
                            "id": f"factory_{index}",
                            "kind": "factory",
                            "owner": "player",
                            "position": [3 + index * 6, 50],
                        }
                        for index in range(4)
                    ),
                ],
            }
        ),
        random_seed=113,
    )
    simulation.resources["player"] = 1_000_000
    target = rectangle_region(Point(40, 8), Point(68, 35))
    defend_result = simulation.execute(
        CreateDefendCommand(defender_ids, target, gathering_point=True)
    )
    assert defend_result.accepted
    defend = simulation.automations[defend_result.automation_id or ""]
    assert isinstance(defend.parameters, DefendParameters)
    original_stations = dict(defend.parameters.stations)
    original_positions = {
        entity_id: simulation.entities[entity_id].position for entity_id in defender_ids
    }
    original_distances = {
        entity_id: simulation.entities[entity_id].position.distance_to(station)
        for entity_id, station in original_stations.items()
    }

    production_results = tuple(
        simulation.execute(
            CreateProductionCommand(
                f"factory_{index}",
                EntityKind.SCOUT,
                1,
                defend_target=target,
                continuous=True,
            )
        )
        for index in range(4)
    )
    assert all(result.accepted for result in production_results)

    simulation.advance(80)

    current_stations = {
        entity_id: parameters.stations[entity_id]
        for automation in simulation.automations.values()
        if automation.kind.value == "defend"
        and isinstance((parameters := automation.parameters), DefendParameters)
        and parameters.target == target
        for entity_id in automation.entity_ids
    }
    assert len(current_stations) == 360
    assert len(set(current_stations.values())) == len(current_stations)
    changed_stations = {
        entity_id: (station, current_stations[entity_id])
        for entity_id, station in original_stations.items()
        if current_stations[entity_id] != station
    }
    assert not changed_stations, (
        f"{len(changed_stations)} established defenders were reassigned when reinforcements "
        f"spawned; first change: {next(iter(changed_stations.items()), None)}"
    )
    progressing = sum(
        simulation.entities[entity_id].position.distance_to(station) < original_distances[entity_id]
        for entity_id, station in original_stations.items()
    )
    assert progressing >= 250, f"only {progressing}/296 established defenders made progress"
    materially_moved = sum(
        simulation.entities[entity_id].position.distance_to(original_positions[entity_id]) >= 5.0
        for entity_id in defender_ids
    )
    assert materially_moved >= 250, (
        f"only {materially_moved}/296 established defenders escaped their initial block"
    )


def test_large_force_never_moves_an_idle_friendly_through_a_factory() -> None:
    """A stale traffic slot cannot turn a pathless friendly into an implicit mover."""

    mover_ids = tuple(f"mover_{index:03d}" for index in range(160))
    idle_start = Point(10.947213595499958, 43.27639320225002)
    simulation = Simulation(
        load_map_data(
            {
                "id": "idle_factory_collision_regression",
                "name": "Idle factory collision regression",
                "width": 100,
                "height": 64,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "factory",
                        "kind": "factory",
                        "owner": "player",
                        "position": [11, 43],
                    },
                    {
                        "id": "idle_victim",
                        "kind": "scout",
                        "owner": "player",
                        "position": [idle_start.x, idle_start.y],
                    },
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [20.5 + index % 40, 2.5 + index // 40],
                        }
                        for index, entity_id in enumerate(mover_ids)
                    ),
                ],
            }
        ),
        random_seed=127,
    )
    assert simulation.execute(MoveCommand(mover_ids, Point(80.5, 30.5))).accepted
    source_slots = {
        entity_id: (
            int(simulation.entities[entity_id].position.x),
            int(simulation.entities[entity_id].position.y),
        )
        for entity_id in mover_ids
    }
    source_slots["idle_victim"] = (12, 42)
    simulation._open_force_slots = (
        1.0,
        source_slots,
        {slot: entity_id for entity_id, slot in source_slots.items()},
    )
    idle_cells = simulation.occupancy.cells_for("idle_victim")

    simulation.advance()

    assert simulation.entities["idle_victim"].position == idle_start
    assert simulation.occupancy.cells_for("idle_victim") == idle_cells
    building_cells = simulation._building_cells()
    assert all(
        simulation._cells_at(entity, entity.position).isdisjoint(building_cells)
        for entity in simulation.entities.values()
        if entity.is_movable
    )
    assert "factory" in simulation.occupancy.occupants((11, 43))


def test_large_force_routes_around_a_held_group_without_moving_the_holders() -> None:
    """Held units are fixed obstacles, not a map-wide barrier for friendly traffic."""

    mover_ids = tuple(f"mover_{index:03d}" for index in range(140))
    holder_ids = tuple(f"holder_{index:03d}" for index in range(60))
    entities: list[dict[str, object]] = [
        {
            "id": entity_id,
            "kind": "scout",
            "owner": "player",
            "position": [7.5 + index % 14, 24.5 + index // 14],
        }
        for index, entity_id in enumerate(mover_ids)
    ]
    entities.extend(
        {
            "id": entity_id,
            "kind": "heavy_tank",
            "owner": "player",
            "position": [34.5 + index % 2, 15.5 + index // 2],
        }
        for index, entity_id in enumerate(holder_ids)
    )
    simulation = Simulation(
        load_map_data(
            {
                "id": "held_group_bypass",
                "name": "Held group bypass",
                "width": 72,
                "height": 60,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": entities,
            }
        ),
        random_seed=109,
    )
    holder_starts = {entity_id: simulation.entities[entity_id].position for entity_id in holder_ids}
    assert simulation.execute(HoldPositionCommand(holder_ids)).accepted
    assert simulation.execute(MoveCommand(mover_ids, Point(60.5, 30.5))).accepted

    crossed = 0
    last_progress_tick = simulation.tick
    while simulation.tick < 1_500 and crossed < len(mover_ids):
        simulation.advance()
        assert all(
            simulation.entities[entity_id].position == holder_starts[entity_id]
            for entity_id in holder_ids
        )
        next_crossed = sum(
            simulation.entities[entity_id].position.x > 37 for entity_id in mover_ids
        )
        if next_crossed > crossed:
            crossed = next_crossed
            last_progress_tick = simulation.tick
        assert simulation.tick - last_progress_tick < 250, (
            f"friendly traffic stalled for 250 ticks behind the held group after only "
            f"{crossed}/{len(mover_ids)} units passed"
        )

    assert crossed == len(mover_ids), (
        f"only {crossed}/{len(mover_ids)} moving units routed around the held formation"
    )
