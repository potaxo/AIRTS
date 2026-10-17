"""Developer income controls and aggregate income lifecycle through real commands."""

from unittest.mock import patch

import pygame
import pytest

from airts.adapters.persistence import SAVE_SCHEMA, PersistenceError, load_simulation_data
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import ConstructionParameters
from airts.contracts.commands import (
    CreateConstructionCommand,
    SetAIIncomeMultiplierCommand,
    SetDeveloperModeCommand,
)
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.systems.economy import income_per_second
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


@pytest.fixture
def world() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "aggregate_income",
                "name": "Aggregate income",
                "width": 40,
                "height": 24,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "extractor", "kind": "resource_generator", "position": [2, 2]},
                    {"id": "synthesizer", "kind": "matter_synthesizer", "position": [8, 2]},
                    {"id": "builder", "kind": "builder", "position": [10.5, 10.5]},
                    {
                        "id": "enemy",
                        "kind": "resource_generator",
                        "position": [30, 2],
                        "owner": "enemy",
                    },
                    {
                        "id": "ally",
                        "kind": "resource_generator",
                        "position": [30, 16],
                        "owner": "ai_ally",
                    },
                ],
            }
        )
    )


def test_income_is_settled_every_second_and_ai_boost_is_reversible(world: Simulation) -> None:
    ai_income = EntityKind.RESOURCE_GENERATOR.profile.income_per_second
    player_income = ai_income + EntityKind.MATTER_SYNTHESIZER.profile.income_per_second
    world.advance(9)
    assert set(world.resources.values()) == {500}
    world.advance()
    assert world.resources == {
        "player": 500 + player_income,
        "enemy": 500 + ai_income,
        "ai_ally": 500 + ai_income,
    }
    assert world.execute(SetAIIncomeMultiplierCommand(5)).accepted
    assert income_per_second(world, "player") == player_income
    assert income_per_second(world, "enemy") == income_per_second(world, "ai_ally") == 5 * ai_income
    world.advance(20)
    assert world.resources == {
        "player": 500 + 3 * player_income,
        "enemy": 500 + 11 * ai_income,
        "ai_ally": 500 + 11 * ai_income,
    }
    assert world.execute(SetAIIncomeMultiplierCommand(1)).accepted
    world.advance(10)
    assert world.resources == {
        "player": 500 + 4 * player_income,
        "enemy": 500 + 12 * ai_income,
        "ai_ally": 500 + 12 * ai_income,
    }
    assert run_replay(capture_replay(world)).export_state() == world.export_state()


def test_completion_and_removal_update_income_without_recounting(world: Simulation) -> None:
    extractor_income = EntityKind.RESOURCE_GENERATOR.profile.income_per_second
    synthesizer_income = EntityKind.MATTER_SYNTHESIZER.profile.income_per_second
    world.advance(3)
    result = world.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(8, 12))
    )
    assert result.accepted
    job = world.automations[result.automation_id or ""]
    assert isinstance(job.parameters, ConstructionParameters)
    world.advance()
    assert income_per_second(world, "player") == extractor_income + synthesizer_income
    balance = world.resources["player"]
    world.advance(6)  # Settle at tick 10 while the physical scaffold is still unfinished.
    assert job.parameters.site_entity_id is not None
    assert not world.entities[job.parameters.site_entity_id].is_complete
    assert world.resources["player"] == balance + extractor_income + synthesizer_income
    for _ in range(40):
        if job.status.terminal:
            break
        world.advance()
    entity_id = job.parameters.constructed_entity_id
    assert entity_id is not None
    assert world.entities[entity_id].is_complete
    completed_income = 2 * extractor_income + synthesizer_income
    assert income_per_second(world, "player") == completed_income
    balance = world.resources["player"]
    remaining = 10 - world.tick % 10
    world.advance(remaining - 1)
    assert world.resources["player"] == balance
    world.advance()
    assert world.resources["player"] == balance + completed_income
    assert world.remove_entity(entity_id).accepted
    assert world.remove_entity("extractor").accepted
    assert income_per_second(world, "player") == synthesizer_income
    assert not world.remove_entity(entity_id).accepted
    balance = world.resources["player"]
    world.advance(10)
    assert world.resources["player"] == balance + synthesizer_income


@pytest.mark.parametrize("multiplier", [0, 2, 25, True])
def test_ai_income_command_rejects_invalid_multipliers(world: Simulation, multiplier: int) -> None:
    result = world.execute(SetAIIncomeMultiplierCommand(multiplier))
    assert not result.accepted
    assert world.ai_income_multiplier == 1


def test_only_the_player_can_change_ai_income(world: Simulation) -> None:
    assert not world.execute(SetAIIncomeMultiplierCommand(5, "enemy")).accepted
    assert world.ai_income_multiplier == 1


def test_load_rebuilds_totals_and_preserves_both_income_phases(world: Simulation) -> None:
    ai_income = EntityKind.RESOURCE_GENERATOR.profile.income_per_second
    player_income = ai_income + EntityKind.MATTER_SYNTHESIZER.profile.income_per_second
    world.advance(3)
    assert world.execute(SetAIIncomeMultiplierCommand(5)).accepted
    assert world.execute(SetDeveloperModeCommand(True)).accepted
    world.advance(4)
    state = world.export_state()
    assert "_income_per_second" not in state
    restored = load_simulation_data(
        {"schema": SAVE_SCHEMA, "map": world.game_map.to_dict(), "state": state}
    )
    assert income_per_second(restored, "player") == 2000 + player_income
    assert income_per_second(restored, "enemy") == 5 * ai_income
    for simulation in (world, restored):
        simulation.advance(2)
        assert simulation.resources["player"] == 500
        simulation.advance()
        assert simulation.resources == {
            "player": 500 + player_income,
            "enemy": 500 + 5 * ai_income,
            "ai_ally": 500 + 5 * ai_income,
        }
        simulation.advance(3)
        assert simulation.resources["player"] == 2500 + player_income
    assert restored.export_state() == world.export_state()
    assert run_replay(capture_replay(world)).export_state() == world.export_state()


@pytest.mark.parametrize("value", [None, True, 0, 2, 25, "5"])
def test_save_rejects_missing_or_invalid_ai_income(world: Simulation, value: object) -> None:
    state = world.export_state()
    if value is None:
        del state["ai_income_multiplier"]
    else:
        state["ai_income_multiplier"] = value
    with pytest.raises(PersistenceError, match="ai_income_multiplier"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": world.game_map.to_dict(), "state": state}
        )


def test_resource_header_advertises_total_income_including_bonus(world: Simulation) -> None:
    base_income = (
        EntityKind.RESOURCE_GENERATOR.profile.income_per_second
        + EntityKind.MATTER_SYNTHESIZER.profile.income_per_second
    )
    app = AirtsApp(world)
    pygame.font.init()
    try:
        app.ui.font = pygame.font.Font(None, 22)
        app.ui.small_font = pygame.font.Font(None, 18)
        screen = pygame.Surface(app.window_size)
        for enabled, rate in (
            (False, base_income),
            (True, 2000 + base_income),
            (False, base_income),
        ):
            assert world.execute(SetDeveloperModeCommand(enabled)).accepted
            with patch.object(app.ui, "small_text", wraps=app.ui.small_text) as text:
                app._draw_interface(screen)
            assert any(call.args[1] == f"Resources: 500 (+{rate})" for call in text.call_args_list)
    finally:
        pygame.font.quit()
