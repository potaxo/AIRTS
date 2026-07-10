"""Command-line entry point for the AIRTS vertical slice."""

from __future__ import annotations

import argparse
from pathlib import Path

from airts.app import AirtsApp
from airts.map_model import load_example_map, load_map
from airts.persistence import load_simulation, save_simulation
from airts.replay import load_replay, run_replay, save_replay
from airts.simulation import Simulation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AIRTS Phase 2 simulation foundation.")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--map", type=Path, help="Load a map JSON file instead of the example.")
    sources.add_argument("--load-state", type=Path, help="Continue a saved simulation state.")
    sources.add_argument("--replay", type=Path, help="Verify and open a recorded replay result.")
    parser.add_argument(
        "--event-log", type=Path, help="Write structured events as JSON Lines on exit."
    )
    parser.add_argument("--save-state", type=Path, help="Write complete simulation state on exit.")
    parser.add_argument("--write-replay", type=Path, help="Write a deterministic replay on exit.")
    parser.add_argument(
        "--max-frames",
        type=int,
        help="Exit after this many rendered frames (useful for smoke tests).",
    )
    arguments = parser.parse_args()
    if arguments.max_frames is not None and arguments.max_frames < 0:
        parser.error("--max-frames cannot be negative")
    if arguments.load_state is not None:
        simulation = load_simulation(arguments.load_state)
    elif arguments.replay is not None:
        simulation = run_replay(load_replay(arguments.replay))
    else:
        game_map = load_map(arguments.map) if arguments.map is not None else load_example_map()
        simulation = Simulation(game_map)
    AirtsApp(simulation).run(arguments.max_frames)
    if arguments.event_log is not None:
        simulation.events.write_jsonl(arguments.event_log)
    if arguments.save_state is not None:
        save_simulation(simulation, arguments.save_state)
    if arguments.write_replay is not None:
        save_replay(simulation, arguments.write_replay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
