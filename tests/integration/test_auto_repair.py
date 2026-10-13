"""Player choices for area repair across commands, execution, saves and UI."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pygame
import pytest
from tests.integration.scenarios import economy_combat_simulation, runtime_map

from airts.adapters.persistence import (
    SAVE_SCHEMA,
    PersistenceError,
    load_simulation,
    load_simulation_data,
    save_simulation,
)
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import (
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    PatrolParameters,
    ProductionParameters,
    RepairParameters,
)
from airts.contracts.commands import (
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PointTarget, rectangle_region
from airts.presentation.app import AirtsApp
from airts.presentation.ui import automations
from airts.simulation import Simulation
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


@pytest.mark.parametrize("kind", ["patrol", "defend"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("health", [49, 50, 51])
def test_area_auto_repair_uses_r_threshold_and_only_its_controlled_members(
    kind: str, enabled: bool, health: int
) -> None:
    simulation = Simulation(runtime_map())
    create = CreatePatrolCommand if kind == "patrol" else CreateDefendCommand
    result = simulation.execute(
        create(("unit_02",), PointTarget(Point(3.5, 4.5)), auto_repair=enabled)
    )
    assert result.accepted
    simulation.entities["unit_02"].health = health
    simulation.entities["unit_01"].health = 1  # Unassigned wounded unit stays untouched.
    simulation.advance()
    assert "unit_01" not in simulation.assignments
    if enabled and health < 50:
        repair = simulation.automations[simulation.assignments["unit_02"]]
        assert isinstance(repair.parameters, RepairParameters)
        assert repair.parameters.health_threshold == 0.5
        assert simulation.suspended_assignments["unit_02"] == result.automation_id
        simulation.advance(10)
        assert (
            len(
                [
                    a
                    for a in simulation.automations.values()
                    if a.kind is AutomationKind.REPAIR_AND_RETURN
                ]
            )
            == 1
        )
        assert simulation.command_count == 1  # Generated emergency commands aren't external inputs.
    else:
        assert simulation.assignments["unit_02"] == result.automation_id


@pytest.mark.parametrize("kind", ["patrol", "defend"])
def test_auto_repair_survives_save_and_returns_after_disabling(tmp_path: Path, kind: str) -> None:
    simulation = Simulation(runtime_map())
    create = CreatePatrolCommand if kind == "patrol" else CreateDefendCommand
    result = simulation.execute(
        create(("unit_02",), PointTarget(Point(3.5, 4.5)), auto_repair=True)
    )
    automation_id = result.automation_id or ""
    simulation.entities["unit_02"].health = 10
    simulation.advance(3)
    repair_id = simulation.assignments["unit_02"]
    path = tmp_path / "auto-repair.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    parameters = restored.automations[automation_id].parameters
    assert isinstance(parameters, PatrolParameters | DefendParameters)
    assert parameters.auto_repair
    assert restored.suspended_assignments["unit_02"] == automation_id
    assert restored.execute(ModifyAutomationCommand(automation_id, auto_repair=False)).accepted
    assert restored.assignments["unit_02"] == repair_id
    restored.advance(200)
    assert restored.entities["unit_02"].health == EntityKind.LIGHT_TANK.profile.max_health
    assert restored.assignments["unit_02"] == automation_id
    assert restored.automations[repair_id].status is AutomationStatus.COMPLETED
    restored.entities["unit_02"].health = 10
    restored.advance(20)
    assert restored.assignments["unit_02"] == automation_id


@pytest.mark.parametrize("override", ["pause", "move"])
def test_auto_repair_respects_pause_and_manual_override(override: str) -> None:
    simulation = Simulation(runtime_map())
    result = simulation.execute(
        CreateDefendCommand(("unit_02",), PointTarget(Point(3.5, 4.5)), auto_repair=True)
    )
    simulation.entities["unit_02"].health = 10
    command = (
        PauseAutomationCommand(result.automation_id or "")
        if override == "pause"
        else MoveCommand(("unit_02",), Point(3.5, 4.5))
    )
    assert simulation.execute(command).accepted
    simulation.advance(20)
    assert all(
        a.kind is not AutomationKind.REPAIR_AND_RETURN for a in simulation.automations.values()
    )


def test_auto_repair_without_facilities_retains_work_and_reports_failure() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "auto-repair-no-facility",
                "name": "No facility",
                "width": 8,
                "height": 8,
                "terrain": {"default": "grass"},
                "entities": [{"id": "unit", "kind": "scout", "position": [1.5, 1.5]}],
            }
        )
    )
    result = simulation.execute(
        CreateDefendCommand(("unit",), PointTarget(Point(3.5, 3.5)), auto_repair=True)
    )
    simulation.entities["unit"].health = 1
    simulation.advance(20)
    assert simulation.assignments["unit"] == result.automation_id
    failures = [e for e in simulation.events.events if e.event_type is EventType.COMMAND_REJECTED]
    assert len(failures) == 2
    assert all(e.details["reason"] == "NO_REPAIR_DESTINATION" for e in failures)


def test_auto_repair_edits_are_atomic_and_retargeting_preserves_choice() -> None:
    simulation = Simulation(runtime_map())
    result = simulation.execute(CreatePatrolCommand(("unit_02",), PointTarget(Point(3.5, 4.5))))
    automation_id = result.automation_id or ""
    before = simulation.automations[automation_id].to_dict()
    rejected = simulation.execute(
        ModifyAutomationCommand(automation_id, auto_repair=True, target_count=1)
    )
    assert not rejected.accepted
    assert simulation.automations[automation_id].to_dict() == before
    assert simulation.execute(ModifyAutomationCommand(automation_id, auto_repair=True)).accepted
    assert simulation.execute(
        ModifyAutomationCommand(automation_id, target=PointTarget(Point(4.5, 4.5)))
    ).accepted
    parameters = simulation.automations[automation_id].parameters
    assert isinstance(parameters, PatrolParameters)
    assert parameters.auto_repair


@pytest.mark.parametrize("enabled", [False, True])
def test_production_defense_inherits_and_updates_auto_repair(enabled: bool, tmp_path: Path) -> None:
    simulation = Simulation(runtime_map())
    result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.SCOUT, 1, continuous=True)
    )
    automation_id = result.automation_id or ""
    assert simulation.execute(
        ModifyAutomationCommand(
            automation_id, target=rectangle_region(Point(11, 2), Point(16, 6)), auto_repair=enabled
        )
    ).accepted
    # Save the choice even before the first produced unit creates the linked defense.
    path = tmp_path / "production-choice.json"
    save_simulation(simulation, path)
    simulation = load_simulation(path)
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS + 1)
    production = simulation.automations[automation_id].parameters
    assert isinstance(production, ProductionParameters)
    defense = simulation.automations[production.defend_automation_id or ""]
    assert isinstance(defense.parameters, DefendParameters)
    assert defense.parameters.auto_repair is enabled
    assert simulation.execute(
        ModifyAutomationCommand(automation_id, auto_repair=not enabled)
    ).accepted
    assert defense.parameters.auto_repair is not enabled
    assert simulation.execute(
        ModifyAutomationCommand(automation_id, target=rectangle_region(Point(12, 2), Point(17, 6)))
    ).accepted
    assert isinstance(defense.parameters, DefendParameters)
    assert defense.parameters.auto_repair is not enabled


def test_auto_repair_generated_commands_replay_once() -> None:
    game_map = economy_combat_simulation().game_map
    game_map = replace(
        game_map,
        entities=tuple(
            replace(entity, kind=EntityKind.SCOUT)
            if entity.entity_id == "tank"
            else replace(entity, kind=EntityKind.HEAVY_TANK)
            if entity.entity_id == "enemy"
            else entity
            for entity in game_map.entities
        ),
    )
    simulation = Simulation(game_map)
    assert simulation.execute(
        CreateDefendCommand(("tank",), PointTarget(Point(2.5, 2.5)), auto_repair=True)
    ).accepted
    simulation.advance(100)
    assert any(a.kind is AutomationKind.REPAIR_AND_RETURN for a in simulation.automations.values())
    replayed = run_replay(capture_replay(simulation))
    assert replayed.export_state() == simulation.export_state()


@pytest.mark.parametrize("value", [None, "false", 0, 1])
def test_saves_reject_missing_or_non_boolean_auto_repair(value: object) -> None:
    simulation = Simulation(runtime_map())
    simulation.execute(CreateDefendCommand(("unit_02",), PointTarget(Point(3.5, 4.5))))
    state = simulation.export_state()
    parameters = next(iter(state["automations"].values()))["parameters"]
    if value is None:
        parameters.pop("auto_repair")
    else:
        parameters["auto_repair"] = value
    with pytest.raises(PersistenceError, match="defend.auto_repair"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


@pytest.mark.parametrize("size", [(760, 520), AirtsApp.WINDOW_SIZE, (3840, 2160)])
def test_units_auto_repair_row_and_assignment_button_are_clickable(size: tuple[int, int]) -> None:
    app = AirtsApp(Simulation(runtime_map()))
    app.resize_layout(size)
    app.automation_panel.group = None
    app.selected_entities = {"unit_02"}
    app.active_target = PointTarget(Point(3.5, 4.5))
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 24)
    app.ui.small_font = pygame.font.Font(None, 19)
    surface = pygame.Surface(size)
    try:
        app._draw_interface(surface)
        row = next(
            rect for rect, action in app._command_buttons if action == "toggle_new_area_auto_repair"
        )
        assert app.left_panel_rect.contains(row)
        assert row.bottom < app.automation_panel.list_top
        assert not app.automation_panel.new_area_auto_repair
        previous = app._opengl_overlay_key()
        app._handle_mouse_down(1, row.center)
        assert app.automation_panel.new_area_auto_repair
        assert app._opengl_overlay_key() != previous
        app._handle_key(pygame.K_y)
        automation_id = app.automation_panel.selected_id or ""
        parameters = app.simulation.automations[automation_id].parameters
        assert isinstance(parameters, DefendParameters)
        assert parameters.auto_repair
        app._draw_interface(surface)
        inspect = next(
            rect for rect, action, _ in app.automation_panel.buttons if action == "inspect"
        )
        app._handle_mouse_down(1, inspect.center)
        app._draw_interface(surface)
        button = next(
            rect
            for rect, action in app._command_buttons
            if action == f"toggle_auto_repair:{automation_id}"
        )
        flyout = automations.details_rect(app)
        assert flyout is not None and flyout.contains(button)
        assert app.canvas_rect.contains(flyout)
        app._handle_mouse_down(1, button.center)
        assert not parameters.auto_repair
        # New-area preferences do not retroactively edit older assignments.
        app._handle_key(pygame.K_t)
        newer = app.simulation.automations[app.automation_panel.selected_id or ""].parameters
        assert isinstance(newer, PatrolParameters) and newer.auto_repair
    finally:
        pygame.font.quit()
