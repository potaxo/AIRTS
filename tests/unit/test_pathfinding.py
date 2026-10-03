"""Routing correctness, cache independence, and geometric clearance."""

from collections.abc import Callable
from heapq import heappop, heappush
from random import Random

import pytest

from airts.contracts.spatial import Point
from airts.navigation.collision import constrained_step
from airts.navigation.pathfinding import PathfindingError, RoutingService, passable_segment_cells
from airts.world.map import GameMap, load_map_data


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
