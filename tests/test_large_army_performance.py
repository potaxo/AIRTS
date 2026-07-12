from __future__ import annotations

from time import perf_counter

from airts.automations import AutomationStatus, DefendParameters
from airts.commands import (
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
)
from airts.geometry import Point, PolygonRegion, rectangle_region
from airts.map_model import load_map_data
from airts.simulation import Simulation


def _large_simulation(unit_count: int, *, with_repair_hub: bool = False) -> Simulation:
    entities: list[dict[str, object]] = [
        {
            "id": f"unit_{index:04d}",
            "kind": "light_tank",
            "owner": "player",
            "position": [index % 50 + 0.5, index // 50 + 0.5],
        }
        for index in range(unit_count)
    ]
    if with_repair_hub:
        entities.append(
            {
                "id": "repair",
                "kind": "repair_hub",
                "owner": "player",
                "position": [60, 40],
            }
        )
    return Simulation(
        load_map_data(
            {
                "id": f"large_{unit_count}",
                "name": "Large Army Performance",
                "width": 80,
                "height": 60,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": entities,
            }
        )
    )


def _assembled_simulation(
    unit_count: int,
) -> tuple[Simulation, tuple[str, ...], PolygonRegion]:
    width = 50
    height = 50
    center = Point(width / 2, height / 2)
    cells = sorted(
        ((x, y) for y in range(height) for x in range(width)),
        key=lambda cell: (
            (cell[0] + 0.5 - center.x) ** 2 + (cell[1] + 0.5 - center.y) ** 2,
            cell[1],
            cell[0],
        ),
    )[:unit_count]
    entity_ids = tuple(f"unit_{index:04d}" for index in range(unit_count))
    simulation = Simulation(
        load_map_data(
            {
                "id": f"assembled_{unit_count}",
                "name": "Large Gathering Point",
                "width": width,
                "height": height,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [cell[0] + 0.5, cell[1] + 0.5],
                    }
                    for entity_id, cell in zip(entity_ids, cells, strict=True)
                ],
            }
        )
    )
    return simulation, entity_ids, rectangle_region(Point(24, 24), Point(26, 26))


def test_thousand_unit_repair_selection_filters_before_shared_routing() -> None:
    simulation = _large_simulation(1_000, with_repair_hub=True)
    selected = tuple(f"unit_{index:04d}" for index in range(1_000))
    damaged = selected[::100]
    for entity_id in damaged:
        simulation.entities[entity_id].health = 17

    started = perf_counter()
    result = simulation.execute(CreateRepairAndReturnCommand(selected))
    elapsed = perf_counter() - started

    automation = simulation.automations[result.automation_id or ""]
    assert automation.entity_ids == list(damaged)
    assert simulation.navigation_field_build_count == 1
    assert elapsed < 1.0


def test_thousand_unit_patrol_uses_bounded_shared_paths_and_realtime_ticks() -> None:
    simulation = _large_simulation(1_000)
    selected = tuple(f"unit_{index:04d}" for index in range(1_000))
    target = rectangle_region(Point(60, 5), Point(75, 25))

    command_started = perf_counter()
    result = simulation.execute(CreatePatrolCommand(selected, target))
    command_elapsed = perf_counter() - command_started
    tick_started = perf_counter()
    simulation.advance(10)
    tick_elapsed = perf_counter() - tick_started

    assert result.accepted
    assert simulation.automations[result.automation_id or ""].status is AutomationStatus.ACTIVE
    assert simulation.navigation_field_build_count <= 24
    assert command_elapsed < 2.0
    assert tick_elapsed < 2.0


def test_thousand_unit_move_clusters_paths_without_blocking_the_command_loop() -> None:
    simulation = _large_simulation(1_000)
    selected = tuple(f"unit_{index:04d}" for index in range(1_000))

    started = perf_counter()
    result = simulation.execute(MoveCommand(selected, Point(70.5, 50.5)))
    elapsed = perf_counter() - started

    destinations = {simulation.entities[entity_id].move_target for entity_id in selected}
    assert result.accepted
    assert len(destinations) == 1_000
    assert simulation.navigation_field_build_count <= 64
    assert elapsed < 2.0


def test_thousand_unit_gathering_point_expands_and_remains_stable_in_realtime() -> None:
    simulation, entity_ids, target = _assembled_simulation(1_000)

    command_started = perf_counter()
    result = simulation.execute(CreateDefendCommand(entity_ids, target, gathering_point=True))
    command_elapsed = perf_counter() - command_started
    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    parameters = automation.parameters
    initial_positions = {
        entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
    }
    radius_at_500 = max(
        point.distance_to(Point(25, 25)) for point in parameters.deployment_slots[:500]
    )

    tick_started = perf_counter()
    simulation.advance(10)
    tick_elapsed = perf_counter() - tick_started

    assert result.accepted
    assert parameters.gathering_point
    assert len(parameters.deployment_slots) == 1_000
    assert len(set(parameters.deployment_slots)) == 1_000
    assert parameters.assembly_radius > radius_at_500
    assert all(not simulation.entities[entity_id].path for entity_id in entity_ids)
    assert {
        entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
    } == initial_positions
    assert command_elapsed < 1.0
    assert tick_elapsed < 2.0
