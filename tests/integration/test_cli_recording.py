"""Player entry points choose recording independently of headless diagnostic defaults."""

from pathlib import Path
from unittest.mock import patch

import pytest

from airts.__main__ import main
from airts.world.map import load_example_map


@pytest.mark.parametrize("option", [None, "--debug", "--event-log", "--write-replay"])
def test_cli_defaults_to_normal_play_and_explicit_diagnostics_enable_history(
    tmp_path: Path, option: str | None
) -> None:
    arguments = ["airts"]
    if option is not None:
        arguments.append(option)
        if option != "--debug":
            arguments.append(str(tmp_path / "diagnostic.json"))
    with (
        patch("sys.argv", arguments),
        patch("airts.__main__.create_skirmish_map", return_value=load_example_map()),
        patch("airts.__main__.AirtsApp.run", autospec=True) as run,
    ):
        assert main() == 0
    app = run.call_args.args[0]
    assert app.lobby is not None
    assert app.debug_mode is (option is not None)
    assert app.simulation.events.enabled is app.debug_mode
