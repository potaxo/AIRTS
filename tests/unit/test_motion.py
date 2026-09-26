"""Swept-disc safety and coherent formation motion."""

from airts.geometry import Point
from airts.navigation.motion import constrain_motion


def test_common_translation_preserves_every_formation_member_without_pair_work() -> None:
    positions = {str(index): Point(index % 20, index // 20) for index in range(400)}
    proposals = {key: Point(point.x + 0.25, point.y + 0.125) for key, point in positions.items()}

    safe, checks = constrain_motion(positions, proposals, dict.fromkeys(positions, 0.45))

    assert safe == proposals
    assert checks == 0


def test_opposing_moves_cannot_exchange_safe_endpoints_through_each_other() -> None:
    positions = {"a": Point(0, 0), "b": Point(2, 0)}
    proposals = {"a": positions["b"], "b": positions["a"]}

    safe, checks = constrain_motion(positions, proposals, {"a": 0.5, "b": 0.5})

    assert checks > 0
    assert 0 < safe["a"].x < safe["b"].x < 2
    assert safe["b"].x - safe["a"].x >= 1 - 1e-8


def test_clipping_propagates_back_through_followers_of_a_stationary_anchor() -> None:
    positions = {str(index): Point(index, 0) for index in range(4)}
    proposals = {str(index): Point(index + 0.8, 0) for index in range(3)}

    safe, checks = constrain_motion(positions, proposals, dict.fromkeys(positions, 0.45))

    assert checks > 0
    assert set(safe) == set(proposals)
    final = positions | safe
    for index in range(3):
        assert positions[str(index)].x <= final[str(index)].x <= proposals[str(index)].x
        assert final[str(index + 1)].x - final[str(index)].x >= 0.9 - 1e-8


def test_faster_follower_does_not_stop_a_leader_that_is_opening_the_gap() -> None:
    for follower, leader in (("a", "b"), ("b", "a")):
        positions = {follower: Point(0, 0), leader: Point(1, 0)}
        for _ in range(20):
            proposals = {
                follower: Point(positions[follower].x + 0.4, 0),
                leader: Point(positions[leader].x + 0.1, 0),
            }
            safe, _ = constrain_motion(positions, proposals, dict.fromkeys(positions, 0.5))
            assert safe[leader] == proposals[leader]
            assert safe[follower].x > positions[follower].x
            assert safe[leader].x - safe[follower].x >= 1 - 1e-8
            positions = safe


def test_stops_propagate_through_a_long_queue_with_linear_pair_work() -> None:
    count = 256
    positions = {f"unit_{index:04d}": Point(index, 0) for index in range(count)}
    proposals = {
        f"unit_{index:04d}": Point(index + 0.2 * (index % 3 + 1), 0) for index in range(count - 1)
    }

    safe, checks = constrain_motion(positions, proposals, dict.fromkeys(positions, 0.5))

    assert checks <= 6 * count
    assert set(safe) == set(proposals)
    final = positions | safe
    for index in range(count - 1):
        assert positions[f"unit_{index:04d}"].x <= final[f"unit_{index:04d}"].x
        assert final[f"unit_{index + 1:04d}"].x - final[f"unit_{index:04d}"].x >= 1 - 1e-8
