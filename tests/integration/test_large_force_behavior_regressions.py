"""Outcome-focused regressions for moving groups through crowded spaces."""

from __future__ import annotations

from collections.abc import Callable
from statistics import median

from airts.automations import DefendParameters
from airts.commands import CreateDefendCommand, HoldPositionCommand, MoveCommand
from airts.geometry import Point, rectangle_region
from airts.simulation import Simulation
from airts.world.map_model import load_map_data


def _river_terrain() -> dict[str, object]:
    """Split the map with a four-cell-wide bridge."""

    return {
        "default": "grass",
        "rectangles": [
            [30, 0, 4, 30, "water"],
            [30, 34, 4, 30, "water"],
            [30, 30, 4, 4, "bridge"],
        ],
    }


def _river_simulation(
    entity_ids: tuple[str, ...],
    *,
    random_seed: int,
) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "group_bridge_crossing",
                "name": "Group bridge crossing",
                "width": 64,
                "height": 64,
                "terrain": _river_terrain(),
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": [4.5 + index % 12, 16.5 + index // 12],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=random_seed,
    )


def _defend_parameters(simulation: Simulation, automation_id: str | None) -> DefendParameters:
    assert automation_id is not None
    parameters = simulation.automations[automation_id].parameters
    assert isinstance(parameters, DefendParameters)
    return parameters


def _advance_until(
    simulation: Simulation,
    condition: Callable[[], bool],
    *,
    maximum_ticks: int,
) -> bool:
    for _ in range(maximum_ticks):
        if condition():
            return True
        simulation.advance()
    return condition()


def _station_distances(
    simulation: Simulation,
    parameters: DefendParameters,
) -> list[float]:
    return [
        simulation.entities[entity_id].position.distance_to(station)
        for entity_id, station in parameters.stations.items()
    ]


def test_automated_defenders_cross_a_bridge_and_reach_their_stations() -> None:
    entity_ids = tuple(f"defender_{index:03d}" for index in range(72))
    simulation = _river_simulation(entity_ids, random_seed=103)
    target = rectangle_region(Point(42, 20), Point(58, 42))

    result = simulation.execute(CreateDefendCommand(entity_ids, target, gathering_point=True))

    assert result.accepted
    parameters = _defend_parameters(simulation, result.automation_id)
    assert all(station.x >= 34 for station in parameters.stations.values())
    starting_median = median(_station_distances(simulation, parameters))

    all_crossed = _advance_until(
        simulation,
        lambda: all(simulation.entities[entity_id].position.x >= 34 for entity_id in entity_ids),
        maximum_ticks=1_200,
    )

    assert all_crossed, (
        f"only {sum(simulation.entities[item].position.x >= 34 for item in entity_ids)}"
        f"/{len(entity_ids)} defenders crossed the bridge"
    )
    assert median(_station_distances(simulation, parameters)) < starting_median * 0.25


def test_manual_group_crosses_a_bridge() -> None:
    entity_ids = tuple(f"scout_{index:03d}" for index in range(72))
    simulation = _river_simulation(entity_ids, random_seed=107)

    result = simulation.execute(MoveCommand(entity_ids, Point(49.5, 31.5)))

    assert result.accepted
    all_crossed = _advance_until(
        simulation,
        lambda: all(simulation.entities[entity_id].position.x >= 34 for entity_id in entity_ids),
        maximum_ticks=1_200,
    )
    assert all_crossed, (
        f"only {sum(simulation.entities[item].position.x >= 34 for item in entity_ids)}"
        f"/{len(entity_ids)} manually moved units crossed the bridge"
    )


def test_same_target_defenses_share_unique_passable_stations_and_converge() -> None:
    first_ids = tuple(f"north_{index:03d}" for index in range(12))
    second_ids = tuple(f"south_{index:03d}" for index in range(12))
    entity_ids = first_ids + second_ids
    simulation = Simulation(
        load_map_data(
            {
                "id": "shared_defense",
                "name": "Shared defense",
                "width": 56,
                "height": 44,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [3.5 + index % 8, 5.5 + index // 8],
                        }
                        for index, entity_id in enumerate(first_ids)
                    ),
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [3.5 + index % 8, 31.5 + index // 8],
                        }
                        for index, entity_id in enumerate(second_ids)
                    ),
                ],
            }
        ),
        random_seed=109,
    )
    target = rectangle_region(Point(30, 12), Point(48, 30))
    results = (
        simulation.execute(CreateDefendCommand(first_ids, target, gathering_point=True)),
        simulation.execute(CreateDefendCommand(second_ids, target, gathering_point=True)),
    )

    assert all(result.accepted for result in results)
    parameters = tuple(_defend_parameters(simulation, result.automation_id) for result in results)

    def current_stations() -> dict[str, Point]:
        return {
            entity_id: station
            for defend in parameters
            for entity_id, station in defend.stations.items()
        }

    stations = current_stations()
    assert set(stations) == set(entity_ids)
    assert len(set(stations.values())) == len(entity_ids)
    assert all(simulation.game_map.is_passable(station) for station in stations.values())

    def converged() -> bool:
        return all(
            not simulation.entities[entity_id].path
            and simulation.entities[entity_id].position.distance_to(station)
            <= Simulation.DEFEND_FORMATION_TOLERANCE
            for entity_id, station in current_stations().items()
        )

    did_converge = _advance_until(simulation, converged, maximum_ticks=1_000)
    stations = current_stations()
    settled_count = sum(
        not simulation.entities[entity_id].path
        and simulation.entities[entity_id].position.distance_to(station)
        <= Simulation.DEFEND_FORMATION_TOLERANCE
        for entity_id, station in stations.items()
    )
    assert did_converge, f"{settled_count}/{len(entity_ids)} defenders converged"
    assert len(set(stations.values())) == len(entity_ids)
    assert all(simulation.game_map.is_passable(station) for station in stations.values())


def test_movers_route_around_held_units_without_displacing_them() -> None:
    mover_ids = tuple(f"mover_{index:03d}" for index in range(48))
    holder_ids = tuple(f"holder_{index:03d}" for index in range(8))
    simulation = Simulation(
        load_map_data(
            {
                "id": "held_unit_bypass",
                "name": "Held unit bypass",
                "width": 72,
                "height": 48,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    *(
                        {
                            "id": entity_id,
                            "kind": "scout",
                            "owner": "player",
                            "position": [5.5 + index % 8, 18.5 + index // 8],
                        }
                        for index, entity_id in enumerate(mover_ids)
                    ),
                    *(
                        {
                            "id": entity_id,
                            "kind": "heavy_tank",
                            "owner": "player",
                            "position": [34.5, 17.5 + index],
                        }
                        for index, entity_id in enumerate(holder_ids)
                    ),
                ],
            }
        ),
        random_seed=113,
    )
    holder_starts = {entity_id: simulation.entities[entity_id].position for entity_id in holder_ids}

    assert simulation.execute(HoldPositionCommand(holder_ids)).accepted
    assert simulation.execute(MoveCommand(mover_ids, Point(60.5, 21.5))).accepted

    all_passed = False
    for _ in range(1_000):
        assert all(
            simulation.entities[entity_id].position == start
            for entity_id, start in holder_starts.items()
        )
        if all(simulation.entities[entity_id].position.x > 38 for entity_id in mover_ids):
            all_passed = True
            break
        simulation.advance()

    assert all(
        simulation.entities[entity_id].position == start
        for entity_id, start in holder_starts.items()
    )
    assert all_passed, (
        f"only {sum(simulation.entities[item].position.x > 38 for item in mover_ids)}"
        f"/{len(mover_ids)} movers passed the held group"
    )


def test_group_movement_is_deterministic_for_the_same_seed() -> None:
    entity_ids = tuple(f"scout_{index:03d}" for index in range(36))
    simulations = (
        _river_simulation(entity_ids, random_seed=127),
        _river_simulation(entity_ids, random_seed=127),
    )

    for simulation in simulations:
        result = simulation.execute(MoveCommand(entity_ids, Point(49.5, 31.5)))
        assert result.accepted

    for _ in range(300):
        simulations[0].advance()
        simulations[1].advance()

    assert simulations[0].snapshot() == simulations[1].snapshot()
