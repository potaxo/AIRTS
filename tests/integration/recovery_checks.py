"""Independent swept-disc checks and preparations shared by recovery integration tests."""

from __future__ import annotations

from math import hypot

import numpy as np
from tests.integration.scenarios import injected_overlap_simulation

from airts.contracts.automations import DefendParameters
from airts.contracts.commands import CreateDefendCommand, HoldPositionCommand
from airts.contracts.spatial import Point, rectangle_region
from airts.navigation.collision import collision_radius
from airts.simulation import Simulation
from airts.systems.movement import HOLD_TOLERANCE
from airts.world._numeric import FloatArray, compile_numeric
from airts.world.entities import UnitState


@compile_numeric
def _disc_metrics(
    before: FloatArray, after: FloatArray, radii: FloatArray, speeds: FloatArray
) -> tuple[float, float, float, float, int]:
    """Independent exact relative-segment distance; no N-by-N storage."""
    max_excess = max_deepening = max_depth = depth_sum = 0.0
    overlaps = 0
    for i in range(len(before)):
        vx, vy = after[i, 0] - before[i, 0], after[i, 1] - before[i, 1]
        max_excess = max(max_excess, hypot(vx, vy) - speeds[i])
        for j in range(i):
            px, py = before[i, 0] - before[j, 0], before[i, 1] - before[j, 1]
            rx = vx - after[j, 0] + before[j, 0]
            ry = vy - after[j, 1] + before[j, 1]
            vv = rx * rx + ry * ry
            t = min(1.0, max(0.0, -(px * rx + py * ry) / vv)) if vv else 0.0
            distance = hypot(px, py)
            contact = radii[i] + radii[j]
            closest = hypot(px + rx * t, py + ry * t)
            max_deepening = max(max_deepening, min(distance, contact) - closest)
            depth = max(0.0, contact - hypot(after[i, 0] - after[j, 0], after[i, 1] - after[j, 1]))
            max_depth = max(max_depth, depth)
            depth_sum += depth
            if depth > 1e-7:
                overlaps += 1
    return max_excess, max_deepening, max_depth, depth_sum, overlaps


def _positions(simulation: Simulation, ids: tuple[str, ...]) -> FloatArray:
    return np.asarray(
        [(simulation.entities[k].position.x, simulation.entities[k].position.y) for k in ids],
        dtype=np.float64,
    )


class RecoveryChecks:
    """Prepare fixed membership/profile limits once; check every pair and tick independently.

    Construct after scenario preparation. Members, kinds and map dimensions must stay fixed
    for this observation window. Positions, occupancy and recovery state are read each tick.
    """

    def __init__(self, simulation: Simulation, ids: tuple[str, ...]) -> None:
        self.simulation = simulation
        self.ids = ids
        self.radii = np.array([collision_radius(simulation.entities[k].kind) for k in ids])
        self.speeds = np.array(
            [simulation.entities[k].speed * simulation.TICK_SECONDS for k in ids]
        )
        self.half_speeds = self.speeds * 0.5
        self.upper_bounds = (
            np.array([simulation.game_map.width, simulation.game_map.height])
            - self.radii[:, None]
            + 1e-8
        )
        self.lower_bounds = self.radii[:, None] - 1e-8
        self.indices = {key: index for index, key in enumerate(ids)}
        self.passive_limits: dict[tuple[str, ...], tuple[np.ndarray, FloatArray]] = {}

    def advance(
        self, before: FloatArray
    ) -> tuple[FloatArray, tuple[float, float, float, float, int]]:
        self.simulation.advance()
        after = _positions(self.simulation, self.ids)
        metrics = _disc_metrics(before, after, self.radii, self.speeds)
        assert metrics[0] <= 1e-8, (
            f"tick {self.simulation.tick}: total displacement exceeded speed by {metrics[0]}"
        )
        assert metrics[1] <= 1e-7, (
            f"tick {self.simulation.tick}: swept contact deepened by {metrics[1]}"
        )
        assert np.all(after >= self.lower_bounds)
        assert np.all(after <= self.upper_bounds)
        for key in self.ids:
            assert (
                self.simulation.occupancy.cells_for(key)
                == self.simulation.entities[key].occupied_cells
            )
        return after, metrics

    def advance_weak(
        self, before: FloatArray
    ) -> tuple[FloatArray, tuple[float, float, float, float, int]]:
        after, metrics = self.advance(before)
        assert float((np.linalg.norm(after - before, axis=1) - self.half_speeds).max()) <= 1e-8
        assert metrics[2] <= 1e-7
        return after, metrics

    def assert_passive_half_steps(
        self, passive: tuple[str, ...], before: FloatArray, after: FloatArray
    ) -> None:
        limits = self.passive_limits.get(passive)
        if limits is None:
            rows = np.array([self.indices[key] for key in passive])
            limits = rows, self.half_speeds[rows]
            self.passive_limits[passive] = limits
        rows, half_speeds = limits
        assert (
            float((np.linalg.norm(after[rows] - before[rows], axis=1) - half_speeds).max()) <= 1e-8
        )


def _resting_formation(
    unit_count: int, behavior: str = "hold", selected_count: int = 1
) -> tuple[Simulation, tuple[str, ...], tuple[str, ...], dict[str, Point], str | None]:
    simulation, ids, selected = injected_overlap_simulation(
        unit_count, 1.0, selected_count=selected_count
    )
    anchors = {key: simulation.entities[key].position for key in ids}
    automation_id = None
    if behavior == "hold":
        assert simulation.execute(HoldPositionCommand(ids)).accepted
    else:
        result = simulation.execute(
            CreateDefendCommand(ids, rectangle_region(Point(32, 32), Point(64, 64)))
        )
        assert result.accepted and result.automation_id is not None
        automation_id = result.automation_id
        parameters = simulation.automations[automation_id].parameters
        assert isinstance(parameters, DefendParameters)
        # Preparation records an already-arrived, physically legal tight defense.
        # The business station identities stay fixed throughout recovery.
        parameters.stations = dict(anchors)
        for key in ids:
            entity = simulation.entities[key]
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.DEFENDING
            entity.defend_arrived = True
    return simulation, ids, selected, anchors, automation_id


def _displace_legally(
    simulation: Simulation, ids: tuple[str, ...], selected: tuple[str, ...], direction: Point
) -> None:
    axis = int(direction.y != 0)
    sign = direction.y if axis else direction.x
    points = [simulation.entities[key].position for key in ids]
    edge = max((point.y if axis else point.x) * sign for point in points) * sign
    for index, key in enumerate(selected):
        entity = simulation.entities[key]
        outside = edge + sign * (5.3 + index * 0.9)
        entity.position = (
            Point(entity.position.x + index * 0.9, outside)
            if axis
            else Point(outside, entity.position.y + index * 0.9)
        )
        simulation.occupancy.move(key, entity.occupied_cells, frozenset(ids))


def _assert_restored_and_stable(
    simulation: Simulation,
    ids: tuple[str, ...],
    anchors: dict[str, Point],
    behavior: str,
    automation_id: str | None = None,
) -> None:
    tolerance = HOLD_TOLERANCE if behavior == "hold" else simulation.DEFEND_STATION_TOLERANCE
    errors = {key: simulation.entities[key].position.distance_to(anchors[key]) for key in ids}
    assert max(errors.values()) <= tolerance + 1e-8, (
        "Compatible unoccupied resting destinations must settle after pressure ends: "
        f"{sum(error <= tolerance + 1e-8 for error in errors.values())}/{len(ids)} restored; "
        f"maximum error {max(errors.values()):.9f}"
    )
    all_mobile_ids = tuple(
        sorted(key for key, entity in simulation.entities.items() if entity.is_movable)
    )
    before = _positions(simulation, all_mobile_ids)
    settled = before.copy()
    checks = RecoveryChecks(simulation, all_mobile_ids)
    for _ in range(40):
        before, _ = checks.advance_weak(before)
        assert abs(before - settled).max() <= 1e-8
    for key in ids:
        entity = simulation.entities[key]
        assert entity.owner_id == "player"
        assert not entity.path and entity.move_target is None
        assert entity.recovery_request_tick is None and not entity.recovery_released
        if behavior == "hold":
            assert entity.hold_anchor == anchors[key] and entity.state is UnitState.HOLDING
        else:
            assert entity.state is UnitState.DEFENDING and entity.defend_arrived
            assert simulation.assignments[key] == automation_id
    if automation_id is not None:
        parameters = simulation.automations[automation_id].parameters
        assert isinstance(parameters, DefendParameters) and parameters.stations == anchors
