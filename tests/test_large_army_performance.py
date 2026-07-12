from __future__ import annotations

from itertools import pairwise
from math import pi, sqrt
from time import perf_counter

from airts.app import AirtsApp
from airts.automations import AutomationStatus, DefendParameters
from airts.commands import (
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateRepairAndReturnCommand,
    MoveCommand,
)
from airts.events import EventType
from airts.geometry import Point, PolygonRegion, PolylineTarget, rectangle_region
from airts.map_model import EntityKind, load_map_data
from airts.movement import collision_radius
from airts.simulation import Simulation
from airts.spatial_index import SpatialIndex


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


def _choke_simulation(unit_count: int = 500) -> tuple[Simulation, tuple[str, ...]]:
    columns = 25
    entity_ids = tuple(f"unit_{index:04d}" for index in range(unit_count))
    return (
        Simulation(
            load_map_data(
                {
                    "id": "generic_mass_choke",
                    "name": "Generic Mass Choke",
                    "width": 80,
                    "height": 40,
                    "terrain": {
                        "default": "grass",
                        "rectangles": [
                            [40, 0, 10, 19, "water"],
                            [40, 22, 10, 18, "water"],
                        ],
                    },
                    "entities": [
                        {
                            "id": entity_id,
                            "kind": "light_tank",
                            "owner": "player",
                            "position": [15.5 + index % columns, 10.5 + index // columns],
                        }
                        for index, entity_id in enumerate(entity_ids)
                    ],
                }
            ),
            random_seed=41,
        ),
        entity_ids,
    )


def _large_enemy_building_simulation() -> tuple[Simulation, tuple[str, ...]]:
    entity_ids = tuple(f"unit_{index:04d}" for index in range(999))
    simulation = Simulation(
        load_map_data(
            {
                "id": "large_enemy_building",
                "name": "Large Enemy Building Focus",
                "width": 80,
                "height": 60,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [index % 50 + 0.5, index // 50 + 0.5],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ]
                + [
                    {
                        "id": "enemy_factory",
                        "kind": "factory",
                        "owner": "enemy",
                        "position": [70, 45],
                    }
                ],
            }
        )
    )
    return simulation, entity_ids


def _head_on_armies(per_group: int = 150) -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    eastbound = tuple(f"east_{index:03d}" for index in range(per_group))
    westbound = tuple(f"west_{index:03d}" for index in range(per_group))
    entities = [
        {
            "id": entity_id,
            "kind": "light_tank",
            "owner": "player",
            "position": [20.5 + index % 10, 5.5 + index // 10],
        }
        for index, entity_id in enumerate(eastbound)
    ] + [
        {
            "id": entity_id,
            "kind": "light_tank",
            "owner": "player",
            "position": [50.5 + index % 10, 5.5 + index // 10],
        }
        for index, entity_id in enumerate(westbound)
    ]
    simulation = Simulation(
        load_map_data(
            {
                "id": "large_head_on_armies",
                "name": "Large Head On Armies",
                "width": 80,
                "height": 40,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": entities,
            }
        )
    )
    return simulation, eastbound, westbound


def _large_line_simulation(
    unit_count: int = 500,
) -> tuple[Simulation, tuple[str, ...], PolylineTarget]:
    entity_ids = tuple(f"line_{index:04d}" for index in range(unit_count))
    simulation = Simulation(
        load_map_data(
            {
                "id": "large_line_automation",
                "name": "Large Line Automation",
                "width": 520,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "light_tank",
                        "owner": "player",
                        "position": [index + 10.5, 3.5],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        )
    )
    return (
        simulation,
        entity_ids,
        PolylineTarget((Point(10.5, 10.5), Point(509.5, 10.5))),
    )


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
    unit_radius = collision_radius(EntityKind.LIGHT_TANK)
    packing_efficiency = pi / (2 * sqrt(3))
    area_estimated_radius = unit_radius * sqrt(len(entity_ids) / packing_efficiency)
    radial_distances = tuple(
        point.distance_to(Point(25, 25)) for point in parameters.deployment_slots
    )
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
    assert all(first <= second for first, second in pairwise(radial_distances))
    assert parameters.assembly_radius > radius_at_500
    assert parameters.assembly_radius <= area_estimated_radius + unit_radius * 2
    assert (
        min(
            first.distance_to(second)
            for index, first in enumerate(parameters.deployment_slots)
            for second in parameters.deployment_slots[index + 1 :]
        )
        >= unit_radius * 2 - 1e-9
    )
    assert command_elapsed < 1.0
    assert tick_elapsed < 2.0


def test_thousand_unit_gathering_radius_contracts_after_half_are_reassigned() -> None:
    simulation, entity_ids, target = _assembled_simulation(1_000)
    created = simulation.execute(CreateDefendCommand(entity_ids, target, gathering_point=True))
    original = simulation.automations[created.automation_id or ""]
    assert isinstance(original.parameters, DefendParameters)
    original_radius = original.parameters.assembly_radius

    reassigned = entity_ids[::2]
    replacement = simulation.execute(
        CreateDefendCommand(
            reassigned,
            rectangle_region(Point(4, 4), Point(6, 6)),
            gathering_point=True,
        )
    )

    assert replacement.accepted
    assert len(original.entity_ids) == 500
    assert len(original.parameters.stations) == 500
    assert len(original.parameters.deployment_slots) == 500
    assert original.parameters.assembly_radius < original_radius * 0.8
    assert set(original.parameters.stations) == set(original.entity_ids)


def test_five_hundred_unit_line_defense_uses_the_whole_line_evenly() -> None:
    simulation, entity_ids, target = _large_line_simulation()
    unit_count = len(entity_ids)

    result = simulation.execute(CreateDefendCommand(entity_ids, target))

    assert result.accepted
    automation = simulation.automations[result.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    stations = tuple(automation.parameters.stations[entity_id] for entity_id in entity_ids)
    gaps = tuple(second.x - first.x for first, second in pairwise(stations))
    assert stations[0] == target.points[0]
    assert stations[-1] == target.points[-1]
    assert len(set(stations)) == unit_count
    assert max(gaps) - min(gaps) < 1e-9
    assert sum(1 for station in stations if 200 <= station.x <= 320) >= 120

    simulation.advance()

    assert simulation.automation_route_count == Simulation.AUTOMATION_ROUTE_BUDGET
    assert sum(bool(simulation.entities[entity_id].path) for entity_id in entity_ids) == (
        Simulation.AUTOMATION_ROUTE_BUDGET
    )


def test_five_hundred_unit_line_patrol_uses_shared_bounded_route_dispatch() -> None:
    simulation, entity_ids, target = _large_line_simulation()
    created = simulation.execute(CreatePatrolCommand(entity_ids, target))
    fields_before = simulation.navigation_field_build_count

    simulation.advance()

    assert created.accepted
    assert simulation.automation_route_count == Simulation.AUTOMATION_ROUTE_BUDGET
    assert sum(bool(simulation.entities[entity_id].path) for entity_id in entity_ids) == (
        Simulation.AUTOMATION_ROUTE_BUDGET
    )
    assert (
        simulation.navigation_field_build_count - fields_before
        <= Simulation.AUTOMATION_ROUTE_BUDGET
    )


def test_five_hundred_unit_repair_travel_uses_shared_bounded_route_dispatch() -> None:
    simulation = _large_simulation(500, with_repair_hub=True)
    entity_ids = tuple(f"unit_{index:04d}" for index in range(500))
    for entity_id in entity_ids:
        simulation.entities[entity_id].health = 17
    created = simulation.execute(CreateRepairAndReturnCommand(entity_ids))
    fields_before = simulation.navigation_field_build_count

    simulation.advance()

    assert created.accepted
    assert simulation.automation_route_count == Simulation.AUTOMATION_ROUTE_BUDGET
    assert sum(bool(simulation.entities[entity_id].path) for entity_id in entity_ids) == (
        Simulation.AUTOMATION_ROUTE_BUDGET
    )
    assert simulation.navigation_field_build_count - fields_before <= 1


def test_delayed_unit_in_five_hundred_unit_army_repaths_and_keeps_progress() -> None:
    blockers = [
        {
            "id": f"blocker_{index:03d}",
            "kind": "heavy_tank",
            "owner": "player",
            "position": [20.5 + index % 25, 5.5 + index // 25],
        }
        for index in range(499)
    ]
    simulation = Simulation(
        load_map_data(
            {
                "id": "large_stalled_repath",
                "name": "Large Stalled Repath",
                "width": 60,
                "height": 32,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "mover",
                        "kind": "scout",
                        "owner": "player",
                        "position": [2.5, 15.5],
                    },
                    *blockers,
                ],
            }
        )
    )
    destination = Point(55.5, 15.5)
    assert simulation.execute(MoveCommand(("mover",), destination)).accepted

    simulation.advance(150)

    repaths = simulation.events.query(
        event_types=frozenset({EventType.PATH_COMPUTED}), subject_id="mover"
    )
    assert any(event.details.get("reason") == "DESTINATION_DELAY_REPATH" for event in repaths), [
        event.to_dict() for event in repaths
    ]
    assert simulation.entities["mover"].position.x > 20
    assert not simulation.entities["mover"].congestion_stopped


def test_thousand_unit_enemy_focus_click_has_generous_hit_area_and_stays_responsive() -> None:
    simulation = _large_simulation(1_000)
    enemy = simulation.entities["unit_0999"]
    enemy.owner_id = "enemy"
    selected = tuple(f"unit_{index:04d}" for index in range(999))
    app = AirtsApp(simulation)
    app.selected_entities = set(selected)
    click = Point(enemy.position.x + 2.0, enemy.position.y)
    pixel = (round(click.x * app.tile_size), round(click.y * app.tile_size))

    started = perf_counter()
    app._handle_mouse_down(3, pixel)
    command_elapsed = perf_counter() - started
    tick_started = perf_counter()
    simulation.advance()
    tick_elapsed = perf_counter() - tick_started

    assert isinstance(simulation.command_history[-1]["command"], dict)
    assert simulation.command_history[-1]["command"]["type"] == "attack"
    assert all(
        simulation.entities[entity_id].attack_target_id == enemy.entity_id for entity_id in selected
    )
    assert command_elapsed < 1.0
    assert tick_elapsed < 2.0


def test_five_hundred_unit_choke_preserves_throughput_with_bounded_work() -> None:
    simulation, entity_ids = _choke_simulation()
    assert simulation.execute(MoveCommand(entity_ids, Point(70.5, 20.5))).accepted
    movement_attempts: list[int] = []
    collision_checks: list[int] = []
    maximum_stopped = 0

    started = perf_counter()
    for _ in range(100):
        simulation.advance()
        movement_attempts.append(simulation.movement_step_attempt_count)
        collision_checks.append(simulation.collision_pair_check_count)
        maximum_stopped = max(
            maximum_stopped,
            sum(simulation.entities[entity_id].congestion_stopped for entity_id in entity_ids),
        )
    elapsed = perf_counter() - started

    density_waits = tuple(
        event
        for event in simulation.events.query(event_types=frozenset({EventType.MOVEMENT_YIELDED}))
        if event.details.get("reason") == "DENSITY_BACKPRESSURE"
    )
    assert not density_waits
    assert maximum_stopped < 100
    assert sum(simulation.entities[entity_id].position.x >= 50 for entity_id in entity_ids) >= 50
    assert min(movement_attempts[10:]) >= 350
    assert max(collision_checks) <= 6_000
    final_index = SpatialIndex(
        {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids}
    )
    close_pairs = final_index.candidate_pairs(collision_radius(EntityKind.LIGHT_TANK) * 2)
    # Active pressure may create transient overlap, but unit centers must never collapse together.
    assert (
        min(
            simulation.entities[first_id].position.distance_to(
                simulation.entities[second_id].position
            )
            for first_id, second_id in close_pairs
        )
        >= collision_radius(EntityKind.LIGHT_TANK) * 0.65
    )
    assert elapsed < 7.0


def test_large_choke_is_deterministic_without_density_waiting() -> None:
    first, first_ids = _choke_simulation(100)
    second, second_ids = _choke_simulation(100)
    assert first_ids == second_ids
    for simulation in (first, second):
        assert simulation.execute(MoveCommand(first_ids, Point(70.5, 20.5))).accepted
        simulation.advance(60)

    assert not any(
        event.details.get("reason") == "DENSITY_BACKPRESSURE" for event in first.events.events
    )
    assert first.snapshot() == second.snapshot()
    assert [event.to_dict() for event in first.events.events] == [
        event.to_dict() for event in second.events.events
    ]


def test_thousand_unit_focus_click_targets_the_enemy_building_footprint_quickly() -> None:
    simulation, entity_ids = _large_enemy_building_simulation()
    app = AirtsApp(simulation)
    app.selected_entities = set(entity_ids)
    click = Point(70.2, 45.2)
    pixel = (round(click.x * app.tile_size), round(click.y * app.tile_size))

    started = perf_counter()
    app._handle_mouse_down(3, pixel)
    command_elapsed = perf_counter() - started
    tick_started = perf_counter()
    simulation.advance()
    tick_elapsed = perf_counter() - tick_started

    assert simulation.command_history[-1]["command"]["type"] == "attack"
    assert all(
        simulation.entities[entity_id].attack_target_id == "enemy_factory"
        for entity_id in entity_ids
    )
    assert simulation.navigation_field_build_count == 1
    assert command_elapsed < 1.0
    assert tick_elapsed < 2.0


def test_two_large_head_on_armies_pass_without_a_global_freeze() -> None:
    simulation, eastbound, westbound = _head_on_armies()
    assert simulation.execute(MoveCommand(eastbound, Point(70.5, 20.5))).accepted
    assert simulation.execute(MoveCommand(westbound, Point(9.5, 20.5))).accepted

    started = perf_counter()
    simulation.advance(180)
    elapsed = perf_counter() - started

    all_ids = eastbound + westbound
    assert not any(
        event.details.get("reason") == "DENSITY_BACKPRESSURE" for event in simulation.events.events
    )
    assert sum(simulation.entities[entity_id].position.x > 40 for entity_id in eastbound) >= 75
    assert sum(simulation.entities[entity_id].position.x < 40 for entity_id in westbound) >= 75
    assert sum(simulation.entities[entity_id].congestion_stopped for entity_id in all_ids) < 30
    assert elapsed < 6.0
