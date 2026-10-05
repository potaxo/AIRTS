"""Wall constraints preserve real corridor release and tangential progress."""

from __future__ import annotations

from math import floor, hypot

import numpy as np
import pytest

from airts.contracts.commands import MoveCommand
from airts.contracts.spatial import Point
from airts.navigation.collision import collision_radius
from airts.simulation import Simulation
from airts.world.map import load_map_data


def _segment_box_distance(start: Point, end: Point, x: int, y: int) -> float:
    """Exact distance of the submitted center segment to one solid grid cell."""
    dx, dy = end.x - start.x, end.y - start.y
    enter, leave = 0.0, 1.0
    for value, velocity, lower, upper in (
        (start.x, dx, x, x + 1),
        (start.y, dy, y, y + 1),
    ):
        if abs(velocity) <= 1e-15:
            if not lower <= value <= upper:
                enter, leave = 1.0, 0.0
                break
        else:
            first, last = (lower - value) / velocity, (upper - value) / velocity
            enter, leave = max(enter, min(first, last)), min(leave, max(first, last))
    if enter <= leave:
        return 0.0
    distance = min(
        hypot(max(x - point.x, 0.0, point.x - x - 1), max(y - point.y, 0.0, point.y - y - 1))
        for point in (start, end)
    )
    length = dx * dx + dy * dy
    for cx, cy in ((x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)):
        time = min(1.0, max(0.0, ((cx - start.x) * dx + (cy - start.y) * dy) / max(length, 1e-30)))
        distance = min(distance, hypot(start.x + dx * time - cx, start.y + dy * time - cy))
    return distance


def _assert_submitted_tick(simulation: Simulation, starts: dict[str, Point]) -> float:
    ids = tuple(starts)
    before = np.asarray([(starts[key].x, starts[key].y) for key in ids])
    after = np.asarray(
        [(simulation.entities[key].position.x, simulation.entities[key].position.y) for key in ids]
    )
    radii = np.asarray([collision_radius(simulation.entities[key].kind) for key in ids])
    total_depth = 0.0
    for index, key in enumerate(ids):
        entity = simulation.entities[key]
        start, end = starts[key], entity.position
        radius = float(radii[index])
        assert start.distance_to(end) <= entity.speed * Simulation.TICK_SECONDS + 1e-8
        assert radius <= end.x <= simulation.game_map.width - radius
        assert radius <= end.y <= simulation.game_map.height - radius
        for y in range(
            floor(min(start.y, end.y) - radius), floor(max(start.y, end.y) + radius) + 1
        ):
            for x in range(
                floor(min(start.x, end.x) - radius), floor(max(start.x, end.x) + radius) + 1
            ):
                if (x, y) in simulation._terrain_obstacles:
                    assert _segment_box_distance(start, end, x, y) >= radius - 1e-8
        assert simulation.occupancy.cells_for(key) == entity.occupied_cells
        # One neighbor row at a time: no N-by-N distance matrix, including in QA.
        offset = before[index] - before[index + 1 :]
        velocity = (after[index] - before[index]) - (after[index + 1 :] - before[index + 1 :])
        time = np.clip(
            -np.sum(offset * velocity, axis=1)
            / np.maximum(np.sum(velocity * velocity, axis=1), 1e-30),
            0,
            1,
        )
        contact = radii[index] + radii[index + 1 :]
        initial = np.linalg.norm(offset, axis=1)
        minimum = np.linalg.norm(offset + time[:, None] * velocity, axis=1)
        assert np.all(minimum >= np.minimum(contact, initial) - 1e-8), (
            f"new/deeper swept penetration at tick{simulation.tick}, unit={key}"
        )
        total_depth += float(
            np.maximum(
                0.0, contact - np.linalg.norm(after[index] - after[index + 1 :], axis=1)
            ).sum()
        )
    return total_depth


@pytest.mark.parametrize("reverse_ids", [False, True], ids=["ascending-ids", "reversed-ids"])
def test_partly_coincident_mixed_discs_release_through_corridor_into_free_area(
    reverse_ids: bool,
) -> None:
    count = 36
    ids = tuple(
        f"unit_{(count - index - 1) if reverse_ids else index:03d}" for index in range(count)
    )
    simulation = Simulation(
        load_map_data(
            {
                "id": "partial_coincidence_corridor_opening",
                "name": "Partial coincidence corridor opening",
                "width": 36,
                "height": 24,
                "terrain": {
                    "default": "grass",
                    "rectangles": [[0, 0, 36, 10, "rock"], [0, 11, 14, 13, "rock"]],
                },
                "entities": [
                    {
                        "id": key,
                        "kind": ("scout", "light_tank", "heavy_tank", "builder")[index % 4],
                        "position": [20.5 + index % 9, 12.5 + index // 9],
                    }
                    for index, key in enumerate(ids)
                ],
            }
        ),
        random_seed=211,
    )
    # Artificial recovery preparation only; the immutable map retains legal starts.
    for index, key in enumerate(ids):
        entity = simulation.entities[key]
        entity.position = Point(7.0 if 10 <= index < 16 else 5.0 + index * 0.16, 10.5)
        simulation.occupancy.move(key, entity.occupied_cells, frozenset(ids))
    selected = tuple(ids[index] for index in (10, 14, 18))
    unselected = tuple(key for key in ids if key not in selected)
    assert simulation.execute(MoveCommand(selected, Point(30, 14))).accepted
    destinations = {key: simulation.entities[key].move_target for key in selected}
    depths = []
    reached: set[str] = set()
    for _ in range(800):
        starts = {key: simulation.entities[key].position for key in ids}
        simulation.advance()
        depths.append(_assert_submitted_tick(simulation, starts))
        for key, target in destinations.items():
            assert target is not None
            if key not in reached and not simulation.entities[key].path:
                assert simulation.entities[key].position.distance_to(target) <= 1e-8
                reached.add(key)
        if (
            len(depths) >= 100
            and depths[-1] <= 1e-6
            and all(not simulation.entities[key].path for key in selected)
        ):
            break
    assert depths[-1] <= 1e-6, f"remaining penetration depth={depths[-1]} at tick{simulation.tick}"
    assert depths[99] < depths[0] * 0.5
    assert reached == set(selected)
    for key, target in destinations.items():
        assert target is not None
        assert not simulation.entities[key].path
        assert simulation.entities[key].position.x > 20
    assert all(
        not simulation.entities[key].path and simulation.entities[key].move_target is None
        for key in unselected
    )
    stable = {key: simulation.entities[key].position for key in ids}
    simulation.advance(30)
    assert all(simulation.entities[key].position.distance_to(stable[key]) <= 1e-8 for key in ids)


def test_scout_advances_tangent_to_wall_across_interior_grid_seams() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "tangent_wall_grid_seams",
                "name": "Tangent wall grid seams",
                "width": 24,
                "height": 20,
                "terrain": {"default": "grass", "rectangles": [[0, 0, 24, 10, "rock"]]},
                "entities": [{"id": "scout", "kind": "scout", "position": [5.999999, 10.3]}],
            }
        )
    )
    target = Point(19.5, 10.3)
    assert simulation.execute(MoveCommand(("scout",), target)).accepted
    for _ in range(30):
        before = simulation.entities["scout"].position
        simulation.advance()
        _assert_submitted_tick(simulation, {"scout": before})
        assert (
            simulation.entities["scout"].position.distance_to(before)
            >= min(0.5, before.distance_to(target)) - 1e-8
        )
    assert simulation.entities["scout"].position.distance_to(target) <= 1e-8
    assert not simulation.entities["scout"].path
