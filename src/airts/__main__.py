"""Command-line entry point for the AIRTS vertical slice."""

from __future__ import annotations

import argparse
from pathlib import Path

from airts.app import AirtsApp
from airts.map_model import load_example_map, load_map
from airts.simulation import Simulation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AIRTS Phase 1 vertical slice.")
    parser.add_argument("--map", type=Path, help="Load a map JSON file instead of the example.")
    parser.add_argument(
        "--event-log", type=Path, help="Write structured events as JSON Lines on exit."
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        help="Exit after this many rendered frames (useful for smoke tests).",
    )
    arguments = parser.parse_args()
    if arguments.max_frames is not None and arguments.max_frames < 0:
        parser.error("--max-frames cannot be negative")
    game_map = load_map(arguments.map) if arguments.map is not None else load_example_map()
    simulation = Simulation(game_map)
    AirtsApp(simulation).run(arguments.max_frames)
    if arguments.event_log is not None:
        simulation.events.write_jsonl(arguments.event_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
