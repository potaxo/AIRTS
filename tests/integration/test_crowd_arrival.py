"""Normal command and production chains starting with physically legal discs."""

from __future__ import annotations

from math import ceil, sqrt

from airts.contracts.automations import DefendParameters, ProductionParameters
from airts.contracts.commands import (
    CreateProductionCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
)
from airts.contracts.spatial import Point, rectangle_region
from airts.navigation.collision import collision_radius
from airts.navigation.spatial_index import SpatialIndex
from airts.simulation import Simulation
from airts.systems.movement import position_clear
from airts.world.map import EntityKind, load_map_data


def _assert_physical_clearance(simulation: Simulation) -> None:
    units = {key: entity for key, entity in simulation.entities.items() if entity.is_movable}
    if not units:
        return
    index = SpatialIndex({key: entity.position for key, entity in units.items()})
    radius = max(collision_radius(entity.kind) for entity in units.values())
    for first_id, second_id in index.candidate_pairs(radius * 2):
        first, second = units[first_id], units[second_id]
        distance = first.position.distance_to(second.position)
        contact = collision_radius(first.kind) + collision_radius(second.kind)
        assert distance >= contact - 1e-8, (
            f"first penetration tick={simulation.tick}, pair={first_id}/{second_id}, "
            f"d={distance}, R={contact}, d/R={distance / contact}"
        )
    blocked = simulation._terrain_obstacles | simulation._building_cells()
    for entity in units.values():
        assert position_clear(
            entity.position,
            collision_radius(entity.kind),
            simulation.game_map.width,
            simulation.game_map.height,
            blocked,
        ), f"static intrusion tick={simulation.tick}, unit={entity.entity_id}"
        assert simulation.occupancy.cells_for(entity.entity_id) == entity.occupied_cells


def test_continuous_mixed_production_defense_arrivals_and_internal_squad_exit() -> None:
    """Actual production/defense creates the crowd; no position injection occurs."""
    target = rectangle_region(Point(39, 27), Point(41, 29))
    simulation = Simulation(
        load_map_data(
            {
                "id": "normal_mixed_factory_internal_exit",
                "name": "Normal mixed factory internal exit",
                "width": 80,
                "height": 60,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "factory_light", "kind": "factory", "position": [6, 8]},
                    {"id": "factory_heavy", "kind": "factory", "position": [6, 20]},
                    {"id": "generator", "kind": "resource_generator", "position": [2, 40]},
                ],
            }
        ),
        random_seed=173,
    )
    productions = []
    for factory, kind in (
        ("factory_light", EntityKind.LIGHT_TANK),
        ("factory_heavy", EntityKind.HEAVY_TANK),
    ):
        result = simulation.execute(
            CreateProductionCommand(factory, kind, 1, continuous=True, defend_target=target)
        )
        assert result.accepted
        productions.append(simulation.automations[result.automation_id or ""])
    paused = set()
    for _ in range(700):
        simulation.advance()
        _assert_physical_clearance(simulation)
        for production in productions:
            parameters = production.parameters
            assert isinstance(parameters, ProductionParameters)
            if parameters.produced_count == 50 and production.automation_id not in paused:
                assert simulation.execute(PauseAutomationCommand(production.automation_id)).accepted
                paused.add(production.automation_id)
        if len(paused) == 2:
            break
    assert len(paused) == 2, f"factory capacity failed to produce100 by tick{simulation.tick}"
    ids = tuple(key for key, entity in simulation.entities.items() if entity.is_movable)
    assert len(ids) == 100
    stations = {
        key: station
        for automation in simulation.automations.values()
        if isinstance(automation.parameters, DefendParameters)
        for key, station in automation.parameters.stations.items()
    }
    for _ in range(400):
        simulation.advance()
        _assert_physical_clearance(simulation)
        if all(
            not simulation.entities[key].path
            and simulation.entities[key].position.distance_to(stations[key])
            <= Simulation.DEFEND_STATION_TOLERANCE
            for key in ids
        ):
            break
    assert all(
        simulation.entities[key].position.distance_to(stations[key])
        <= Simulation.DEFEND_STATION_TOLERANCE
        for key in ids
    ), "normally produced defenders did not settle before the internal selection"
    center = Point(40, 28)
    selected = tuple(
        sorted(ids, key=lambda key: (simulation.entities[key].position.distance_to(center), key))[
            :5
        ]
    )
    neighbors = tuple(key for key in ids if key not in selected)
    assignments = {key: simulation.assignments[key] for key in neighbors}
    starts = {key: simulation.entities[key].position for key in selected}
    assert simulation.execute(MoveCommand(selected, Point(70, 48))).accepted
    # Direct selection changes defense membership and may contract its deployment.
    # Subsequent passive yielding must preserve these accepted station identities.
    stations_after_command = {
        key: simulation.automations[assignments[key]].parameters.stations[key] for key in neighbors
    }
    for production in productions:
        assert simulation.execute(ResumeAutomationCommand(production.automation_id)).accepted
    followup_paused: set[str] = set()
    for _ in range(400):
        simulation.advance()
        _assert_physical_clearance(simulation)
        for production in productions:
            parameters = production.parameters
            assert isinstance(parameters, ProductionParameters)
            if parameters.produced_count == 55 and production.automation_id not in followup_paused:
                assert simulation.execute(PauseAutomationCommand(production.automation_id)).accepted
                followup_paused.add(production.automation_id)
        if all(not simulation.entities[key].path for key in selected):
            break
    assert len(followup_paused) == 2
    assert sum(entity.is_movable for entity in simulation.entities.values()) == 110
    outer_x = max(simulation.entities[key].position.x for key in neighbors)
    assert all(not simulation.entities[key].path for key in selected)
    assert all(simulation.entities[key].position.x > outer_x + 0.9 for key in selected), (
        f"internal squad did not fully escape at tick{simulation.tick}: "
        f"{[(key, simulation.entities[key].position) for key in selected]}"
    )
    assert all(simulation.entities[key].position.distance_to(starts[key]) > 20 for key in selected)
    assert all(simulation.assignments[key] == assignments[key] for key in neighbors)
    assert all(
        simulation.automations[assignments[key]].parameters.stations[key]
        == stations_after_command[key]
        for key in neighbors
    )
    final_stations = {
        key: station
        for automation in simulation.automations.values()
        if isinstance(automation.parameters, DefendParameters)
        for key, station in automation.parameters.stations.items()
    }
    for _ in range(100):
        simulation.advance()
        _assert_physical_clearance(simulation)
        if all(
            not simulation.entities[key].path
            and simulation.entities[key].position.distance_to(station)
            <= Simulation.DEFEND_STATION_TOLERANCE
            for key, station in final_stations.items()
        ):
            break
    assert all(
        simulation.entities[key].position.distance_to(station)
        <= Simulation.DEFEND_STATION_TOLERANCE
        for key, station in final_stations.items()
    ), "defenders did not safely recover their stations after the internal squad escaped"
    stable = {
        key: entity.position for key, entity in simulation.entities.items() if entity.is_movable
    }
    for _ in range(20):
        simulation.advance()
        _assert_physical_clearance(simulation)
    assert all(
        simulation.entities[key].position.distance_to(point) <= 1e-8
        for key, point in stable.items()
    )


def test_repeated_mixed_batches_to_same_area_do_not_introduce_overlap() -> None:
    count = 100
    side = ceil(sqrt(count))
    simulation = Simulation(
        load_map_data(
            {
                "id": "normal_repeated_mixed_arrivals",
                "name": "Normal repeated mixed arrivals",
                "width": 80,
                "height": 60,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": f"unit_{index:03d}",
                        "kind": ("scout", "light_tank", "heavy_tank", "builder")[index % 4],
                        "position": [4.5 + index % side, 8.5 + index // side],
                    }
                    for index in range(count)
                ],
            }
        ),
        random_seed=191,
    )
    ids = tuple(simulation.entities)
    _assert_physical_clearance(simulation)
    for group in range(4):
        batch = ids[group * 25 : (group + 1) * 25]
        assert simulation.execute(MoveCommand(batch, Point(48, 30))).accepted
        for _ in range(35):
            simulation.advance()
            _assert_physical_clearance(simulation)
    for _ in range(160):
        simulation.advance()
        _assert_physical_clearance(simulation)
    selected = tuple(
        sorted(
            ids, key=lambda key: (simulation.entities[key].position.distance_to(Point(48, 30)), key)
        )[:3]
    )
    others = tuple(key for key in ids if key not in selected)
    assert simulation.execute(MoveCommand(selected, Point(70, 30))).accepted
    for _ in range(300):
        simulation.advance()
        _assert_physical_clearance(simulation)
        if all(not simulation.entities[key].path for key in selected):
            break
    assert all(not simulation.entities[key].path for key in selected)
    assert min(simulation.entities[key].position.x for key in selected) > (
        max(simulation.entities[key].position.x for key in others) + 0.9
    )
