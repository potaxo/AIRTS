"""Full severe-contact workload; optional timing retains every recovery body/tick."""

from __future__ import annotations

import json
from collections.abc import Callable
from statistics import quantiles
from time import perf_counter

import pytest
from tests.integration.scenarios import injected_overlap_simulation

from airts.contracts.commands import MoveCommand
from airts.contracts.spatial import Point
from airts.navigation.spatial_index import SpatialIndex


@pytest.mark.parametrize("spacing_ratio", [0.3, 0.0], ids=["30-percent", "coincident"])
def test_thousand_body_recovery_counts_full_workload_and_meets_tick_budget(
    spacing_ratio: float,
    check_timing: bool,
    record_property: Callable[[str, object], None],
) -> None:
    simulation, ids, selected = injected_overlap_simulation(1000, spacing_ratio)
    durations = []
    checks = []
    candidates = []
    coordination = []
    replans = []
    started = perf_counter()
    assert simulation.execute(MoveCommand(selected, Point(80, 48))).accepted
    command_ms = (perf_counter() - started) * 1000
    for _ in range(300):
        started = perf_counter()
        simulation.advance()
        durations.append((perf_counter() - started) * 1000)
        checks.append(simulation.collision_pair_check_count)
        candidates.append(simulation.contact_candidate_count)
        coordination.append(simulation.contact_coordination_work)
        replans.append(simulation.movement_replan_count)
    assert len(simulation.entities) == len(ids) == 1000
    assert all(
        not simulation.entities[k].path and simulation.entities[k].position.x > 75 for k in selected
    )
    index = SpatialIndex({k: simulation.entities[k].position for k in ids})
    for first, second in index.candidate_pairs(0.6):
        assert (
            simulation.entities[first].position.distance_to(simulation.entities[second].position)
            >= 0.6 - 1e-7
        )
    assert max(checks) > 0 and max(candidates) >= max(checks)
    assert max(coordination) > 0
    p95 = quantiles(durations, n=100, method="inclusive")[94]
    record_property("command_ms", command_ms)
    record_property("tick_p95_ms", p95)
    record_property("tick_samples_ms", json.dumps(durations))
    record_property("collision_checks", json.dumps(checks))
    record_property("contact_candidates", json.dumps(candidates))
    record_property("coordination_work", json.dumps(coordination))
    record_property("movement_replans", json.dumps(replans))
    if check_timing:
        assert p95 < 100, (
            f"1,000-body recovery tick p95 {p95:.3f} ms exceeds the existing 100 ms gate"
        )
