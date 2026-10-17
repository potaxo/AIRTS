"""A line defense covers its full frontage, including dense multi-rank armies."""

from pathlib import Path

import pytest

from airts.adapters.persistence import load_simulation, save_simulation
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import DefendParameters, ProductionParameters
from airts.contracts.commands import (
    CreateDefendCommand,
    CreateProductionCommand,
    ModifyAutomationCommand,
    RemoveEntityCommand,
)
from airts.contracts.spatial import Point, PolylineTarget
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.spatial_index import SpatialIndex
from airts.simulation import Simulation
from airts.systems.movement import position_clear
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


def line_army(count: int) -> tuple[Simulation, tuple[str, ...]]:
    ids = tuple(f"unit_{i:04d}" for i in range(count))
    return Simulation(
        load_map_data(
            {
                "id": "line_defense",
                "name": "Line defense",
                "width": 160,
                "height": 100,
                "terrain": {"default": "grass"},
                "entities": [
                    {
                        "id": key,
                        "kind": "heavy_tank",
                        "position": [3.5 + i % 40 * 1.1, 3.5 + i // 40 * 1.1],
                    }
                    for i, key in enumerate(ids)
                ],
            }
        )
    ), ids


def assert_frontage(stations: dict[str, Point], target: PolylineTarget) -> None:
    a, b = target.points
    length = a.distance_to(b)
    ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
    along = [(p.x - a.x) * ux + (p.y - a.y) * uy for p in stations.values()]
    across = [-(p.x - a.x) * uy + (p.y - a.y) * ux for p in stations.values()]
    assert min(along) <= 1e-8 and max(along) >= length - 1e-8
    assert max(along) - min(along) >= 2 * (max(across) - min(across))
    bin_count = 4
    bins = [0] * bin_count
    for distance in along:
        index = min(
            bin_count - 1, int((distance - min(along)) / (max(along) - min(along)) * bin_count)
        )
        bins[index] += 1
    assert min(bins) > 0 and max(bins) <= 2 * min(bins), bins
    index = SpatialIndex(stations)
    assert all(
        stations[a].distance_to(stations[b]) >= 0.9 - 1e-8 for a, b in index.candidate_pairs(0.9)
    )


@pytest.mark.parametrize("count", [48, 300, 1000])
@pytest.mark.parametrize("gathering", [False, True])
@pytest.mark.parametrize(
    "start,end",
    [((65, 50), (85, 50)), ((75, 40), (75, 60)), ((68, 43), (82, 57))],
    ids=["horizontal", "vertical", "diagonal"],
)
def test_dense_defense_expands_as_parallel_ranks_along_the_entire_line(
    count: int, gathering: bool, start: tuple[int, int], end: tuple[int, int]
) -> None:
    simulation, ids = line_army(count)
    target = PolylineTarget((Point(*start), Point(*end)))
    result = simulation.execute(CreateDefendCommand(ids, target, gathering_point=gathering))
    assert result.accepted, result.reason
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, DefendParameters)
    assert len(parameters.stations) == count
    assert_frontage(parameters.stations, target)
    simulation.advance(3)
    assert simulation.automation_route_count <= Simulation.TOTAL_AUTOMATION_ROUTE_BUDGET
    assert all(simulation.assignments[key] == result.automation_id for key in ids)
    assert any(simulation.entities[key].path for key in ids)


def test_shared_dense_line_keeps_unique_stations_after_casualty_and_save(tmp_path: Path) -> None:
    simulation, ids = line_army(48)
    target = PolylineTarget((Point(60.5, 50.5), Point(80.5, 50.5)))
    first = simulation.execute(CreateDefendCommand(ids[:24], target))
    second = simulation.execute(CreateDefendCommand(ids[24:], target))
    assert first.accepted and second.accepted
    tasks = [simulation.automations[result.automation_id or ""] for result in (first, second)]
    before = {
        key: point
        for task in tasks
        if isinstance(task.parameters, DefendParameters)
        for key, point in task.parameters.stations.items()
    }
    assert_frontage(before, target)
    assert simulation.execute(RemoveEntityCommand(ids[0])).accepted
    after = {
        key: point
        for task in tasks
        if isinstance(task.parameters, DefendParameters)
        for key, point in task.parameters.stations.items()
    }
    assert len(after) == 47 and len(set(after.values())) == 47
    assert sum(point != before[key] for key, point in after.items()) <= 1
    destination = tmp_path / "line.json"
    save_simulation(simulation, destination)
    restored = load_simulation(destination)
    assert restored.export_state() == simulation.export_state()


@pytest.mark.parametrize("count", [1, 3])
def test_sparse_gathering_marked_line_stays_linear_and_saves(count: int, tmp_path: Path) -> None:
    simulation, ids = line_army(count)
    target = PolylineTarget((Point(60.5, 50.5), Point(80.5, 50.5)))
    result = simulation.execute(CreateDefendCommand(ids, target, gathering_point=True))
    assert result.accepted
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, DefendParameters)
    assert all(point.y == 50.5 for point in parameters.stations.values())
    path = tmp_path / "sparse-line.json"
    save_simulation(simulation, path)
    assert load_simulation(path).export_state() == simulation.export_state()


@pytest.mark.usefixtures("dense_production")
def test_factory_reinforcements_add_rows_instead_of_a_central_gathering_disk() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "line_reinforcements",
                "name": "Line reinforcements",
                "width": 80,
                "height": 60,
                "terrain": {"default": "grass"},
                "entities": [{"id": "factory", "kind": "factory", "position": [8, 8]}],
            }
        )
    )
    simulation.resources["player"] = 100000
    target = PolylineTarget((Point(40.5, 30.5), Point(50.5, 30.5)))
    result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.SCOUT, 25, defend_target=target)
    )
    assert result.accepted
    simulation.advance(125)
    defense = next(
        task.parameters
        for task in simulation.automations.values()
        if isinstance(task.parameters, DefendParameters)
    )
    assert len(defense.stations) == 25
    assert_frontage(defense.stations, target)


@pytest.mark.usefixtures("dense_production")
@pytest.mark.parametrize("workflow", ["create", "edit-loop", "edit-defense"])
@pytest.mark.parametrize("obstacle", ["rock", "water", "building", "body-clearance"])
def test_factory_line_preflight_uses_actual_deployment_clearance(
    workflow: str,
    obstacle: str,
) -> None:
    target = PolylineTarget((Point(30.8, 18.5), Point(50.8, 18.5)))
    rectangles = (
        [[41, 18, 1, 1, "rock"]]
        if obstacle == "body-clearance"
        else [[40, 18, 1, 1, obstacle]]
        if obstacle in {"rock", "water"}
        else []
    )
    entities = [{"id": "factory", "kind": "factory", "position": [8, 8]}]
    if obstacle == "building":
        entities.append({"id": "obstacle", "kind": "factory", "position": [39, 17]})
    simulation = Simulation(
        load_map_data(
            {
                "id": "production_line_clearance",
                "name": "Production line clearance",
                "width": 64,
                "height": 40,
                "terrain": {"default": "grass", "rectangles": rectangles},
                "entities": entities,
            }
        )
    )
    old_target = PolylineTarget((Point(22.5, 25.5), Point(30.5, 25.5)))
    result = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.HEAVY_TANK,
            1,
            continuous=True,
            defend_target=target if workflow == "create" else old_target,
        )
    )
    assert result.accepted, result.reason
    production = simulation.automations[result.automation_id or ""]
    parameters = production.parameters
    assert isinstance(parameters, ProductionParameters)
    if workflow != "create":
        controller = production.automation_id
        if workflow == "edit-defense":
            simulation.advance(5)
            assert parameters.defend_automation_id is not None
            controller = parameters.defend_automation_id
        changed = simulation.execute(ModifyAutomationCommand(controller, target=target))
        assert changed.accepted, changed.reason
    simulation.advance(5)
    assert parameters.defend_target == target
    defense = simulation.automations[parameters.defend_automation_id or ""].parameters
    assert isinstance(defense, DefendParameters) and defense.stations
    blocked = simulation._building_cells() | simulation._terrain_obstacles
    radius = collision_radius(EntityKind.HEAVY_TANK)
    assert all(
        position_clear(point, radius, 64, 40, blocked) for point in defense.stations.values()
    )
    midpoint = Point(40.8, 18.5)
    assert not position_clear(midpoint, radius, 64, 40, blocked)
    assert run_replay(capture_replay(simulation)).snapshot() == simulation.snapshot()
