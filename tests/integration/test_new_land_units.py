"""New land units reuse production, combat, persistence and presentation contracts."""

from pathlib import Path

import pygame
import pytest
from tests.integration.opponent_scenarios import opening_army, opponent_scenario
from tests.integration.scenarios import builder_factory_simulation

from airts.adapters.persistence import load_simulation, save_simulation
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import ProductionParameters
from airts.contracts.commands import AttackCommand, CreateProductionBatchCommand, MoveCommand
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.ui.selection import draw_selection
from airts.simulation import Simulation
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


def weapon_simulation(kind: EntityKind) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "new-land-weapons",
                "name": "New land weapons",
                "width": 32,
                "height": 24,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "gun", "kind": kind.value, "position": [2.5, 10.5]},
                    {"id": "target", "kind": "builder", "owner": "enemy", "position": [11.5, 10.5]},
                    {"id": "near", "kind": "builder", "owner": "enemy", "position": [10.5, 10.5]},
                    {
                        "id": "outside",
                        "kind": "builder",
                        "owner": "enemy",
                        "position": [13.5, 10.5],
                    },
                    {"id": "friend", "kind": "builder", "position": [11.5, 11.5]},
                ],
            }
        )
    )


def test_light_artillery_small_fixed_blast_survives_save_and_replay(tmp_path: Path) -> None:
    simulation = weapon_simulation(EntityKind.LIGHT_ARTILLERY)
    assert simulation.execute(AttackCommand(("gun",), "target")).accepted
    simulation.advance(2)
    shell = next(iter(simulation.projectiles.values()))
    assert shell.destination == Point(11.5, 10.5)
    assert shell.speed == 14
    path = tmp_path / "light-artillery.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    for current in (simulation, restored):
        current.advance(6)
        assert current.entities["target"].health == 60
        assert current.entities["near"].health == 73
        assert current.entities["outside"].health == 80
        assert current.entities["friend"].health == 80
        assert current.projectile_traces[-1].weapon_kind is EntityKind.LIGHT_ARTILLERY
        assert current.projectile_traces[-1].points[-1] == Point(11.5, 10.5)
    assert restored.export_state() == simulation.export_state()
    run_replay(capture_replay(simulation))


def test_tank_destroyer_tracks_one_target_and_obeys_long_reload(tmp_path: Path) -> None:
    simulation = weapon_simulation(EntityKind.TANK_DESTROYER)
    assert simulation.execute(AttackCommand(("gun",), "target")).accepted
    simulation.advance()
    # Move a target through its ordinary command boundary after the shot launches.
    assert simulation.execute(MoveCommand(("target",), Point(11.5, 6.5), owner_id="enemy")).accepted
    simulation.advance()
    shell = next(iter(simulation.projectiles.values()))
    assert shell.destination == simulation.entities["target"].selection_position
    assert shell.destination != Point(11.5, 10.5)
    path = tmp_path / "tank-destroyer.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    for current in (simulation, restored):
        current.advance(4)
        assert current.entities["target"].health == 35
        for key in ("near", "outside", "friend"):
            assert current.entities[key].health == 80
        # Return the target to range without granting another early shot.
        assert current.execute(
            MoveCommand(("target",), Point(11.5, 10.5), owner_id="enemy")
        ).accepted
        current.advance(19)
        launches = current.events.query(event_types=frozenset({EventType.PROJECTILE_LAUNCHED}))
        assert len([event for event in launches if event.subject_id == "gun"]) == 1
        current.advance()
        launches = current.events.query(event_types=frozenset({EventType.PROJECTILE_LAUNCHED}))
        assert len([event for event in launches if event.subject_id == "gun"]) == 2
    assert restored.export_state() == simulation.export_state()
    run_replay(capture_replay(simulation))


def test_new_units_finish_mixed_paid_queue_after_save_and_replay(tmp_path: Path) -> None:
    simulation = Simulation(builder_factory_simulation().game_map)
    kinds = (EntityKind.LIGHT_ARTILLERY, EntityKind.TANK_DESTROYER)
    created = simulation.execute(
        CreateProductionBatchCommand("factory", tuple((kind, 1) for kind in kinds))
    )
    assert created.accepted
    simulation.advance(20)
    path = tmp_path / "mixed-land-queue.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    for current in (simulation, restored):
        current.advance(sum(kind.profile.production_ticks for kind in kinds) - 20)
        parameters = current.automations[created.automation_id or ""].parameters
        assert isinstance(parameters, ProductionParameters)
        assert [current.entities[key].kind for key in parameters.produced_entity_ids] == list(kinds)
        assert all(
            current.entities[key].health == current.entities[key].kind.profile.max_health
            for key in parameters.produced_entity_ids
        )
    assert restored.export_state() == simulation.export_state()
    run_replay(capture_replay(simulation))


@pytest.mark.parametrize("kind", [EntityKind.LIGHT_ARTILLERY, EntityKind.TANK_DESTROYER])
def test_factory_ui_exposes_new_units_and_click_produces_them(kind: EntityKind) -> None:
    app = AirtsApp(builder_factory_simulation())
    app.selected_entities = {"factory"}
    app._selection_changed()
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 24)
    app.ui.small_font = pygame.font.Font(None, 18)
    surface = pygame.Surface(app.WINDOW_SIZE)
    draw_selection(app, surface)
    for action in (f"queue:{kind.value}", f"unit_details:{kind.value}", f"loop:{kind.value}"):
        button = next(rectangle for rectangle, name in app._command_buttons if name == action)
        assert app.right_panel_rect.contains(button)
    button = next(
        rectangle for rectangle, name in app._command_buttons if name == f"queue:{kind.value}"
    )
    app._handle_command_click(button.center)
    app.simulation.advance(kind.profile.production_ticks)
    assert any(entity.kind is kind for entity in app.simulation.entities.values())


@pytest.mark.parametrize(
    ("kind", "enemies"),
    [
        (EntityKind.LIGHT_ARTILLERY, ()),
        (
            EntityKind.TANK_DESTROYER,
            tuple((EntityKind.HEAVY_TANK, 25.5 + i, 30.5) for i in range(4)),
        ),
    ],
)
@pytest.mark.usefixtures("established_economy")
def test_ai_orders_new_support_units_for_its_composition(
    kind: EntityKind, enemies: tuple[tuple[EntityKind, float, float], ...]
) -> None:
    army = opening_army(8) + (
        (EntityKind.SCOUT, 52.5, 55.5),
        (EntityKind.HEAVY_TANK, 54.5, 55.5),
        (EntityKind.HEAVY_TANK, 56.5, 55.5),
    )
    simulation = opponent_scenario(army, enemies)
    simulation.advance(300)
    assert any(
        isinstance(job.parameters, ProductionParameters) and job.parameters.unit_kind is kind
        for job in simulation.automations.values()
    )
    run_replay(capture_replay(simulation))
