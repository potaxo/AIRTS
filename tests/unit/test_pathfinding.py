"""Routing correctness, cache independence, and geometric clearance."""

from collections.abc import Callable
from heapq import heappop, heappush
from random import Random

import numpy as np
import pytest

from airts.contracts.spatial import Point
from airts.navigation.collision.geometry import constrained_step
from airts.navigation.pathfinding import PathfindingError, RoutingService, passable_segment_cells
from airts.world.map import GameMap, load_map_data


def test_batch_terrain_costs_sample_centers_and_reject_invalid_coordinates() -> None:
    game_map = load_map_data(
        {
            "id": "terrain_cost_sampling",
            "name": "Terrain cost sampling",
            "width": 4,
            "height": 2,
            "terrain": {"rows": [["grass", "forest", "road", "bridge"], ["water"] * 4]},
            "entities": [{"id": "unit", "kind": "scout", "position": [0.5, 0.5]}],
        }
    )
    router = RoutingService(game_map)
    positions = np.array([[0.1, 0.8], [1.9, 0.1], [2.0, 0.5], [3.5, 0.5], [0.5, 1.5]])
    np.testing.assert_array_equal(
        router.movement_costs_at(positions), [1.0, 1.5, 0.75, 0.8, float("inf")]
    )
    for point in [(-0.1, 0.5), (4.0, 0.5), (0.5, -0.1), (0.5, 2.0), (float("nan"), 0.5)]:
        with pytest.raises(ValueError, match="movement position outside map"):
            router.movement_costs_at(np.array([point]))


def test_weighted_routes_match_independent_forward_search() -> None:
    rng = Random(41)
    game_map = load_map_data(
        {
            "id": "routing",
            "name": "Routing",
            "width": 12,
            "height": 12,
            "terrain": {
                "rows": [
                    [
                        rng.choice(("grass", "road", "forest"))
                        if x == 0 and y == 0
                        else rng.choice(("grass", "road", "forest", "water"))
                        for x in range(12)
                    ]
                    for y in range(12)
                ]
            },
            "entities": [{"id": "unit", "kind": "scout", "position": [0.5, 0.5]}],
        }
    )
    router = RoutingService(game_map)
    passable = [(x, y) for y in range(12) for x in range(12) if game_map.is_cell_passable((x, y))]
    for _ in range(50):
        start, end = rng.sample(passable, 2)
        costs = {start: 0.0}
        queue = [(0.0, start)]
        while queue:
            cost, cell = heappop(queue)
            if cost != costs[cell]:
                continue
            x, y = cell
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if not game_map.is_cell_passable(neighbor):
                    continue
                candidate = cost + game_map.terrain_at_cell(neighbor).movement_cost
                if candidate < costs.get(neighbor, float("inf")):
                    costs[neighbor] = candidate
                    heappush(queue, (candidate, neighbor))
        a, b = Point(start[0] + 0.5, start[1] + 0.5), Point(end[0] + 0.5, end[1] + 0.5)
        segment = passable_segment_cells(game_map, a, b)
        direct = router.segment_path(a, b, frozenset(), maximum_cost=float("inf"))
        assert (direct.cells if direct is not None else None) == segment
        if direct is not None:
            assert direct.cost == sum(
                game_map.terrain_at_cell(c).movement_cost for c in segment[1:]
            )
        assert router.can_reach_any(a, (b,), frozenset()) == (end in costs)
        assert router.clear_segment(a, b, frozenset()) == (
            segment is not None
            and all(game_map.terrain_at_cell(c).movement_cost <= 1 for c in segment)
        )
        if end not in costs:
            with pytest.raises(PathfindingError, match="NO_PATH"):
                router.shared_path(a, b)
            continue
        result = router.shared_path(a, b)
        assert result.cost == pytest.approx(costs[end])
        assert result.cells[0] == start and result.cells[-1] == end
        assert all(
            abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1
            for a, b in zip(result.cells, result.cells[1:], strict=False)
        )
        assert result.waypoints[-1] == b


def test_connectivity_tracks_obstacles_and_does_not_depend_on_cached_searches(
    make_map: Callable[[int], GameMap],
) -> None:
    router = RoutingService(make_map(1), maximum_fields=1)
    start, goal = Point(1.5, 1.5), Point(10.5, 10.5)
    wall = frozenset((5, y) for y in range(12))
    assert not router.can_reach_any(start, (goal,), wall)
    assert router.can_reach_any(start, (goal,), wall - {(5, 6)})
    assert router.can_reach_any(start, (goal, start), wall)
    assert not router.can_reach_any(start, (Point(5.5, 5.5),), wall)
    assert not router.can_reach_any(Point(5.5, 5.5), (goal,), wall)
    router.clear()
    assert not router.can_reach_any(start, (goal,), wall)
    assert router.field_build_count == 0


def test_branch_cost_continues_python_compensated_sum_without_changing_rounding() -> None:
    game_map = load_map_data(
        {
            "id": "branch_cost",
            "name": "Branch cost",
            "width": 160,
            "height": 4,
            "terrain": {"rows": [["bridge", "grass", "forest", "road"] * 40 for _ in range(4)]},
            "entities": [{"id": "unit", "kind": "scout", "position": [0.5, 0.5]}],
        }
    )
    router = RoutingService(game_map)
    path = router.shared_path(Point(0.5, 0.5), Point(159.5, 0.5))
    assert path.prefix_costs is not None
    for index in range(1, len(path.cells), 11):
        x, y = path.cells[index]
        branch = router.segment_path(
            Point(x + 0.5, y + 0.5),
            Point(158.5, 3.5),
            frozenset(),
            maximum_cost=float("inf"),
            initial_cost=float(path.prefix_costs[index, 0]),
            initial_correction=float(path.prefix_costs[index, 1]),
        )
        assert branch is not None
        cells = path.cells[: index + 1] + branch.cells[1:]
        assert branch.cost == sum(game_map.terrain_at_cell(c).movement_cost for c in cells[1:])


def test_thousand_routes_share_one_field_and_cache_eviction_preserves_routes(
    make_map: Callable[[int], GameMap],
) -> None:
    router = RoutingService(make_map(1), maximum_fields=1)
    goal = Point(10.5, 10.5)
    expected = router.shared_path(Point(1.5, 1.5), goal)
    for i in range(1000):
        router.shared_path(Point(i % 10 + 0.5, i // 10 % 10 + 0.5), goal)
    assert router.field_build_count == 1
    router.shared_path(Point(1.5, 1.5), Point(9.5, 9.5))
    assert router.cached_field_count == 1
    assert router.shared_path(Point(1.5, 1.5), goal) == expected
    wall = frozenset((5, y) for y in range(12))
    with pytest.raises(PathfindingError, match="NO_PATH"):
        router.shared_path(Point(1.5, 1.5), goal, wall)
    assert router.shared_path(Point(1.5, 1.5), goal) == expected


def test_multi_goal_route_chooses_reachable_goal_independent_of_input_order(
    make_map: Callable[[int], GameMap],
) -> None:
    router = RoutingService(make_map(1))
    start = Point(1.5, 1.5)
    goals = (Point(10.5, 10.5), Point(2.5, 1.5))
    assert router.shared_path_to_any(start, goals) == router.shared_path_to_any(start, goals[::-1])
    assert router.shared_path_to_any(start, goals)[0] == goals[1]
    assert router.field_build_count == 1
    assert router.shared_path(start, start).waypoints == ()


@pytest.mark.parametrize("goal_count", [1, 2])
@pytest.mark.parametrize("with_wall", [False, True], ids=["open", "blocked"])
def test_compiled_fields_preserve_exact_reverse_dijkstra_routes(
    goal_count: int, with_wall: bool
) -> None:
    """Cost ties must retain the pre-optimization path, not merely its length."""
    rng = Random(83)
    game_map = load_map_data(
        {
            "id": "field_order",
            "name": "Field order",
            "width": 8,
            "height": 8,
            "terrain": {
                "rows": [
                    [rng.choice(("grass", "road", "forest")) for _ in range(8)] for _ in range(8)
                ]
            },
            "entities": [{"id": "unit", "kind": "scout", "position": [0.5, 0.5]}],
        }
    )
    goals = (Point(7.5, 7.5), Point(0.5, 7.5))[:goal_count]
    blocked = frozenset((4, y) for y in range(8)) if with_wall else frozenset()
    costs = [float("inf")] * 64
    following = [-1] * 64
    queue = []
    for goal in sorted(goals, key=lambda p: (p.y, p.x)):
        index = int(goal.y) * 8 + int(goal.x)
        costs[index] = 0.0
        heappush(queue, (0.0, index))
    while queue:
        distance, current = heappop(queue)
        if distance != costs[current]:
            continue
        x, y = current % 8, current // 8
        candidate = distance + game_map.terrain_at_cell((x, y)).movement_cost
        for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)):
            if not game_map.contains_cell((nx, ny)) or (nx, ny) in blocked:
                continue
            neighbor = ny * 8 + nx
            if candidate < costs[neighbor]:
                costs[neighbor] = candidate
                following[neighbor] = current
                heappush(queue, (candidate, neighbor))
    router = RoutingService(game_map)
    for index in range(64):
        start = Point(index % 8 + 0.5, index // 8 + 0.5)
        if game_map.cell_for(start) in blocked:
            continue
        if costs[index] == float("inf"):
            with pytest.raises(PathfindingError, match="NO_PATH"):
                router.shared_path_to_any(start, goals, blocked)
            continue
        expected = [index]
        current = index
        while following[current] != -1:
            current = following[current]
            expected.append(current)
        _, path = router.shared_path_to_any(start, goals, blocked)
        assert path.cells == tuple((i % 8, i // 8) for i in expected)
        assert path.cost == costs[index]


@pytest.mark.parametrize(
    "start,end", [(Point(0.5, 0.5), Point(2.5, 2.5)), (Point(2.5, 2.5), Point(0.5, 0.5))]
)
def test_segment_rejects_corner_cutting(
    make_map: Callable[[int], GameMap], start: Point, end: Point
) -> None:
    assert passable_segment_cells(make_map(1), start, end, frozenset({(1, 0)})) is None


def test_route_budgets_are_shared_and_reset_per_tick(make_map: Callable[[int], GameMap]) -> None:
    router = RoutingService(make_map(1), automation_budget=3, combat_budget=1)
    first, second = router.automation_allowance(2), router.automation_allowance(2)
    assert first.claim() and first.claim() and not first.claim()
    assert second.claim() and not second.claim()
    assert router.claim_combat_route() and not router.claim_combat_route()
    router.begin_tick()
    assert router.automation_allowance(1).claim() and router.claim_combat_route()


def test_clearance_solver_respects_all_planes_and_speed() -> None:
    planes = [(-1.0, 0.0, -0.2), (0.0, 1.0, 0.1), (1.0, 0.0, -0.1)]
    step = constrained_step(Point(1, 0), 0.5, planes)
    assert step is not None
    assert step.x == pytest.approx(0.2) and step.y == pytest.approx(0.1)
    assert step.distance_to(Point(0, 0)) <= 0.5
    assert all(x * step.x + y * step.y >= bound - 1e-9 for x, y, bound in planes)
    assert constrained_step(Point(0, 0), 0.5, [(1.0, 0.0, 1.0)]) is None


@pytest.mark.parametrize(
    "start,end",
    [
        (Point(0.5, 2.5), Point(4, 2)),
        (Point(4, 2), Point(0.5, 2.5)),
        (Point(2.5, 0.5), Point(2, 4)),
        (Point(2, 4), Point(2.5, 0.5)),
    ],
)
def test_segment_stops_at_integer_boundary_endpoints(
    make_map: Callable[[int], GameMap], start: Point, end: Point
) -> None:
    game_map = make_map(1)
    cells = passable_segment_cells(game_map, start, end)
    assert cells is not None
    assert cells[0] == game_map.cell_for(start)
    assert cells[-1] == game_map.cell_for(end)
    assert all(game_map.contains_cell(cell) for cell in cells)


def test_shared_field_stops_nearby_and_resumes_without_changing_routes(
    make_map: Callable[[int], GameMap],
) -> None:
    router = RoutingService(make_map(1))
    goal = Point(10.5, 10.5)
    router.shared_path(goal, goal)
    field = next(iter(router._fields.values()))
    assert sum(field.positions == -2) == 1
    assert field.size > 0
    starts = (Point(9.5, 10.5), Point(0.5, 0.5), Point(4.5, 8.5))
    for start in starts:
        expected = RoutingService(make_map(1)).shared_path(start, goal)
        assert router.shared_path(start, goal) == expected
    assert router.field_build_count == 1
    router.clear()
    for start in starts[::-1]:
        expected = RoutingService(make_map(1)).shared_path(start, goal)
        assert router.shared_path(start, goal) == expected


def test_batched_lookahead_matches_individual_checks_on_weighted_corners(
    make_map: Callable[[int], GameMap],
) -> None:
    game_map = make_map(1)
    router = RoutingService(game_map)
    starts = [Point(0.5, 0.5), Point(10.5, 9.5)]
    paths = [
        [Point(0.5, 0.5), Point(1.5, 0.5), Point(2.5, 2.5), Point(10.5, 10.5)],
        [Point(10.5, 9.5), Point(9.5, 9.5), Point(4.5, 3.5)],
    ]
    blocked = frozenset({(1, 0), (5, 5)})
    expected = []
    for start, path in zip(starts, paths, strict=True):
        expected.append(
            next(
                (
                    i
                    for i in range(min(len(path) - 1, 16), 0, -1)
                    if router.clear_segment(start, path[i], blocked)
                ),
                0,
            )
        )
    assert router.visible_waypoints(starts, paths, blocked).tolist() == expected
