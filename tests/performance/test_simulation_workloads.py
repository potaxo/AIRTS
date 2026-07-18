"""Small, headless performance smoke tests for representative RTS workloads."""

from __future__ import annotations

from statistics import fmean
from time import perf_counter

from airts.commands import CreateDefendCommand, MoveCommand
from airts.geometry import Point, rectangle_region
from airts.simulation import Simulation
from airts.world.map_model import load_map_data

MAX_WORKLOAD_SECONDS = 30.0


def _open_map_simulation(
    name: str,
    *,
    width: int,
    height: int,
    entities: list[dict[str, object]],
    seed: int,
) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": name,
                "name": name.replace("_", " ").title(),
                "width": width,
                "height": height,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": entities,
            }
        ),
        random_seed=seed,
        ambient_enemy_spawns=False,
    )


def test_thousand_scout_head_on_orders_advance_in_a_generous_budget() -> None:
    """A large command should be accepted and make progress without renderer involvement."""

    group_size = 500
    eastbound = tuple(f"east_{index:03d}" for index in range(group_size))
    westbound = tuple(f"west_{index:03d}" for index in range(group_size))
    simulation = _open_map_simulation(
        "thousand_scout_head_on",
        width=80,
        height=60,
        entities=[
            {
                "id": entity_id,
                "kind": "scout",
                "owner": "player",
                "position": [3.5 + index % 25, 20.5 + index // 25],
            }
            for index, entity_id in enumerate(eastbound)
        ]
        + [
            {
                "id": entity_id,
                "kind": "scout",
                "owner": "player",
                "position": [52.5 + index % 25, 20.5 + index // 25],
            }
            for index, entity_id in enumerate(westbound)
        ],
        seed=97,
    )
    east_start = fmean(simulation.entities[item].position.x for item in eastbound)
    west_start = fmean(simulation.entities[item].position.x for item in westbound)

    started = perf_counter()
    east_result = simulation.execute(MoveCommand(eastbound, Point(72.5, 30.5)))
    west_result = simulation.execute(MoveCommand(westbound, Point(7.5, 30.5)))
    simulation.advance(Simulation.TICKS_PER_SECOND)
    elapsed = perf_counter() - started

    east_finish = fmean(simulation.entities[item].position.x for item in eastbound)
    west_finish = fmean(simulation.entities[item].position.x for item in westbound)
    assert east_result.accepted and west_result.accepted
    assert east_finish > east_start
    assert west_finish < west_start
    assert elapsed < MAX_WORKLOAD_SECONDS


def test_moderate_defense_converges_near_its_region_in_a_generous_budget() -> None:
    """A moderate formation should visibly converge without asserting solver internals."""

    unit_ids = tuple(f"defender_{index:03d}" for index in range(96))
    simulation = _open_map_simulation(
        "moderate_defense_convergence",
        width=52,
        height=44,
        entities=[
            {
                "id": entity_id,
                "kind": "scout",
                "owner": "player",
                "position": [3.5 + index % 12, 10.5 + index // 12],
            }
            for index, entity_id in enumerate(unit_ids)
        ],
        seed=131,
    )
    target = rectangle_region(Point(34, 16), Point(44, 28))
    center = Point(39, 22)
    initial_distances = tuple(
        simulation.entities[item].position.distance_to(center) for item in unit_ids
    )

    started = perf_counter()
    result = simulation.execute(CreateDefendCommand(unit_ids, target))
    simulation.advance(300)
    elapsed = perf_counter() - started

    final_distances = tuple(
        simulation.entities[item].position.distance_to(center) for item in unit_ids
    )
    assert result.accepted
    assert fmean(final_distances) < fmean(initial_distances) / 3
    assert max(final_distances) < 12.0
    assert elapsed < MAX_WORKLOAD_SECONDS
