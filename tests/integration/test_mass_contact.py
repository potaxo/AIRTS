"""Mass-weighted contact through ordinary commands, Hold, saves, and current replay."""

from pathlib import Path

import numpy as np
import pytest
from tests.integration.recovery_checks import RecoveryChecks

from airts.adapters.persistence import load_simulation, save_simulation
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.commands import HoldPositionCommand, MoveCommand
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.world.entities import UnitState
from airts.world.map import load_map_data


def test_heavy_traffic_moves_a_light_holder_more_than_light_traffic_moves_a_heavy_holder(
    tmp_path: Path,
) -> None:
    displacements = []
    for mover, holder in (("heavy_tank", "scout"), ("scout", "heavy_tank")):
        simulation = Simulation(
            load_map_data(
                {
                    "id": "mass-contact",
                    "name": "Mass contact",
                    "width": 16,
                    "height": 12,
                    "terrain": {"default": "grass"},
                    "entities": [
                        {"id": "mover", "kind": mover, "position": [5.25, 5.0]},
                        {"id": "holder", "kind": holder, "position": [6.0, 5.0]},
                    ],
                }
            )
        )
        anchor = Point(6.0, 5.0)
        assert simulation.execute(HoldPositionCommand(("holder",))).accepted
        assert simulation.execute(MoveCommand(("mover",), Point(11, 5))).accepted
        oracle = RecoveryChecks(simulation, ("mover", "holder"))
        before = np.array([[5.25, 5.0], [6.0, 5.0]])
        maximum = 0.0
        restored: Simulation | None = None
        for tick in range(15):
            old = before
            before, _ = oracle.advance(before)
            oracle.assert_passive_half_steps(("holder",), old, before)
            maximum = max(maximum, simulation.entities["holder"].position.distance_to(anchor))
            if restored is not None:
                restored.advance()
                assert restored.export_state() == simulation.export_state()
            if tick == 2:
                path = tmp_path / f"{mover}-contact.json"
                save_simulation(simulation, path)
                restored = load_simulation(path)
                assert restored.export_state() == simulation.export_state()
            assert simulation.entities["holder"].hold_anchor == anchor
            assert simulation.entities["holder"].state is UnitState.HOLDING
        assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()
        assert maximum > 0.01
        displacements.append(maximum)
    assert displacements[0] > displacements[1] * 2


@pytest.mark.parametrize("schema", ["airts-replay-v15", "airts-match-replay-v8"])
def test_previous_replay_physics_is_rejected_with_a_mass_diagnostic(schema: str) -> None:
    from airts.adapters.replay import ReplayError, load_replay_data

    with pytest.raises(ReplayError, match="mass-weighted contact response"):
        load_replay_data({"schema": schema})
