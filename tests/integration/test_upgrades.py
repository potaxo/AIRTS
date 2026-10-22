"""Paid upgrades, production cadence, command authority and continuation."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pygame
import pytest

from airts.adapters.persistence import (
    SAVE_SCHEMA,
    PersistenceError,
    load_simulation,
    load_simulation_data,
    save_simulation,
)
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.commands import (
    CancelUpgradeCommand,
    MoveCommand,
    UpgradeEntityCommand,
    command_from_dict,
    command_to_dict,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.ui import selection
from airts.simulation import Simulation
from airts.systems import economy
from airts.world.map import load_map_data
from airts.world.units import catalog
from airts.world.units.catalog import EntityKind
from airts.world.units.schema import UpgradeDefinition


def upgrade_world(*, match: bool = False) -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "upgrades",
                "name": "Upgrades",
                "width": 40,
                "height": 24,
                "terrain": {
                    "default": "grass",
                    "rectangles": [[20, 0, 1, 24, "rock"]] if match else [],
                },
                "resource_sites": [[2, 2], [32, 16]],
                "entities": [
                    {"id": "player_center", "kind": "command_center", "position": [2, 10]},
                    {
                        "id": "enemy_center",
                        "kind": "command_center",
                        "position": [30, 2],
                        "owner": "enemy",
                    },
                    {"id": "extractor", "kind": "resource_generator", "position": [2, 2]},
                    {"id": "synthesizer", "kind": "matter_synthesizer", "position": [8, 2]},
                    {
                        "id": "enemy",
                        "kind": "resource_generator",
                        "position": [32, 16],
                        "owner": "enemy",
                    },
                ],
                **(
                    {
                        "match_slots": [
                            {"owner": "player", "controller": "player", "team": 1},
                            {"owner": "enemy", "controller": "ai", "team": 2},
                        ]
                    }
                    if match
                    else {}
                ),
            }
        )
    )


@pytest.mark.parametrize("entity_id", ["extractor", "synthesizer"])
@pytest.mark.parametrize("tier,start_tick", [(1, 7), (2, 0)])
def test_each_income_upgrade_charges_once_and_preserves_global_settlement(
    entity_id: str, tier: int, start_tick: int
) -> None:
    world = upgrade_world()
    world.resources["player"] = 10000
    world.advance(start_tick)
    entity = world.entities[entity_id]
    for _ in range(tier - 1):
        assert world.execute(UpgradeEntityCommand(entity_id)).accepted
        world.advance(entity.upgrade.remaining_ticks)
    definition = entity.next_upgrade
    assert definition is not None
    initial = world.resources["player"]
    old_income = entity.profile.income_per_second
    old_total = sum(
        unit.profile.income_per_second
        for unit in world.entities.values()
        if unit.owner_id == "player" and unit.is_complete
    )
    start_tick = world.tick
    assert world.execute(UpgradeEntityCommand(entity_id)).accepted
    assert world.resources["player"] == initial - definition.cost
    assert entity.upgrade.remaining_ticks == definition.duration_ticks
    assert not world.execute(UpgradeEntityCommand(entity_id)).accepted
    assert world.resources["player"] == initial - definition.cost
    world.advance(definition.duration_ticks - 1)
    assert entity.upgrade.tier == tier and entity.upgrade.remaining_ticks == 1
    assert entity.profile.income_per_second == old_income
    settlements = (
        world.tick // Simulation.TICKS_PER_SECOND - start_tick // Simulation.TICKS_PER_SECOND
    )
    assert world.resources["player"] == initial - definition.cost + settlements * old_total
    world.advance()
    assert entity.upgrade.tier == tier + 1 and entity.upgrade.remaining_ticks == 0
    assert entity.profile.income_per_second == definition.profile.income_per_second
    assert economy.income_per_second(world, "player") == sum(
        unit.profile.income_per_second
        for unit in world.entities.values()
        if unit.owner_id == "player"
    )
    completed = world.events.query(
        event_types=frozenset({EventType.UPGRADE_COMPLETED}), subject_id=entity_id
    )
    assert len(completed) == tier
    new_total = old_total - old_income + definition.profile.income_per_second
    expected = initial - definition.cost + settlements * old_total
    if world.tick % Simulation.TICKS_PER_SECOND == 0:
        expected += new_total
    assert world.resources["player"] == expected
    until_settlement = Simulation.TICKS_PER_SECOND - world.tick % Simulation.TICKS_PER_SECOND
    world.advance(until_settlement - 1)
    assert world.resources["player"] == expected
    world.advance()
    assert world.resources["player"] == expected + new_total
    if tier == len(entity.kind.upgrades):
        assert entity.next_upgrade is None
        balance = world.resources["player"]
        assert not world.execute(UpgradeEntityCommand(entity_id)).accepted
        assert not world.execute(CancelUpgradeCommand(entity_id)).accepted
        assert world.resources["player"] == balance


def test_cancellation_refunds_once_and_preserves_global_settlement() -> None:
    world = upgrade_world()
    definition = EntityKind.RESOURCE_GENERATOR.upgrades[0]
    base_income = (
        EntityKind.RESOURCE_GENERATOR.profile.income_per_second
        + EntityKind.MATTER_SYNTHESIZER.profile.income_per_second
    )
    assert world.execute(UpgradeEntityCommand("extractor")).accepted
    world.advance(13)
    entity = world.entities["extractor"]
    balance = world.resources["player"]
    assert world.execute(CancelUpgradeCommand("extractor")).accepted
    assert world.resources["player"] == balance + definition.cost
    assert world.tick % Simulation.TICKS_PER_SECOND == 3 and entity.upgrade.tier == 1
    assert not world.execute(CancelUpgradeCommand("extractor")).accepted
    assert world.resources["player"] == balance + definition.cost
    assert world.execute(UpgradeEntityCommand("extractor")).accepted
    assert entity.upgrade.remaining_ticks == definition.duration_ticks
    world.advance(7)
    assert world.tick % Simulation.TICKS_PER_SECOND == 0
    assert world.resources["player"] == balance + base_income


def test_destruction_does_not_refund_or_finish_an_upgrade() -> None:
    world = upgrade_world()
    assert world.execute(UpgradeEntityCommand("extractor")).accepted
    balance = world.resources["player"]
    assert world.remove_entity("extractor").accepted
    assert world.resources["player"] == balance
    world.advance(300)
    assert not world.events.query(
        event_types=frozenset({EventType.UPGRADE_COMPLETED}), subject_id="extractor"
    )
    assert not world.execute(CancelUpgradeCommand("extractor")).accepted


@pytest.mark.parametrize("invalid", ["missing", "enemy", "unfinished", "poor", "unsupported"])
def test_rejections_preserve_resources_and_existing_progress(invalid: str) -> None:
    world = upgrade_world()
    entity_id = "extractor"
    if invalid in {"missing", "enemy"}:
        entity_id = invalid
    elif invalid == "unfinished":
        world.entities[entity_id].construction_progress = 0.5
    elif invalid == "poor":
        world.resources["player"] = 119
    else:
        world.entities[entity_id].kind = EntityKind.FACTORY
    balance = world.resources["player"]
    assert not world.execute(UpgradeEntityCommand(entity_id)).accepted
    assert not world.execute(CancelUpgradeCommand(entity_id)).accepted
    assert world.resources["player"] == balance
    assert world.entities["extractor"].upgrade.remaining_ticks == 0


@pytest.mark.parametrize("match", [False, True])
def test_partial_cycle_and_paid_upgrades_save_continue_cancel_and_replay(
    match: bool,
    tmp_path: Path,
) -> None:
    world = upgrade_world(match=match)
    world.advance(7)
    assert world.execute(UpgradeEntityCommand("extractor")).accepted
    assert world.execute(UpgradeEntityCommand("synthesizer")).accepted
    world.advance(143)
    path = tmp_path / "upgrades.json"
    save_simulation(world, path)
    restored = load_simulation(path)
    assert restored.export_state() == world.export_state()
    for sim in (world, restored):
        assert sim.tick % Simulation.TICKS_PER_SECOND == 0
        assert sim.entities["extractor"].upgrade.remaining_ticks == 157
        assert sim.execute(CancelUpgradeCommand("synthesizer")).accepted
        assert sim.execute(UpgradeEntityCommand("synthesizer")).accepted
        sim.advance(450)
        assert sim.entities["extractor"].upgrade.tier == 2
        assert sim.entities["synthesizer"].upgrade.tier == 2
    assert restored.export_state() == world.export_state()
    assert run_replay(capture_replay(world)).export_state() == world.export_state()


@pytest.mark.parametrize(
    "field,value",
    [
        ("tier", 0),
        ("tier", 4),
        ("tier", True),
        ("remaining_ticks", -1),
        ("remaining_ticks", 301),
        ("remaining_ticks", True),
    ],
)
def test_saves_reject_invalid_upgrade_state(field: str, value: object) -> None:
    world = upgrade_world()
    state = world.export_state()
    state["entities"]["extractor"]["upgrade"][field] = value  # type: ignore[index]
    with pytest.raises(PersistenceError, match="upgrade"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": world.game_map.to_dict(), "state": state}
        )


@pytest.mark.parametrize("case", ["max_tier", "unfinished", "missing"])
def test_saves_reject_incoherent_upgrade_state(case: str) -> None:
    world = upgrade_world()
    entity = world.entities["extractor"]
    if case == "max_tier":
        entity.upgrade.tier = 3
        entity.upgrade.remaining_ticks = 1
    elif case == "unfinished":
        entity.construction_progress = 0.5
        entity.upgrade.remaining_ticks = 1
    state = world.export_state()
    if case == "missing":
        del state["entities"]["extractor"]["upgrade"]  # type: ignore[index,attr-defined]
    with pytest.raises(PersistenceError):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": world.game_map.to_dict(), "state": state}
        )


@pytest.mark.parametrize(
    "command", [UpgradeEntityCommand("extractor"), CancelUpgradeCommand("extractor", "enemy")]
)
def test_upgrade_command_codec(command: UpgradeEntityCommand | CancelUpgradeCommand) -> None:
    assert command_from_dict(command_to_dict(command)) == command


def test_selection_buttons_start_cancel_disable_and_display_max_tier() -> None:
    world = upgrade_world()
    app = AirtsApp(world)
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 18)
    app.selected_entities = {"extractor"}
    app._selection_changed()
    screen = pygame.Surface(app.window_size)

    def draw() -> list[tuple[pygame.Rect, str]]:
        app._command_buttons.clear()
        selection.draw_selection(app, screen)
        return app._command_buttons

    button = next(rect for rect, action in draw() if action.startswith("upgrade:"))
    app._handle_command_click(button.center)
    assert world.entities["extractor"].upgrade.remaining_ticks == 300
    world.advance(7)
    button = next(rect for rect, action in draw() if action.startswith("cancel_upgrade:"))
    app._handle_command_click(button.center)
    assert world.entities["extractor"].upgrade.remaining_ticks == 0
    world.resources["player"] = 119
    assert not any(action.startswith("upgrade:") for _, action in draw())
    world.entities["extractor"].upgrade.tier = 3
    with patch.object(app.ui, "small_text", wraps=app.ui.small_text) as text:
        assert not any(action.startswith(("upgrade:", "cancel_upgrade:")) for _, action in draw())
        assert any(call.args[1] == "Maximum tier" for call in text.call_args_list)


def test_same_upgrade_execution_supports_a_mobile_kind_without_replacing_its_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = UpgradeDefinition(cost=40, duration_ticks=20, profile=EntityKind.SCOUT.profile)
    monkeypatch.setitem(catalog._ENTITY_UPGRADES, EntityKind.SCOUT, (definition,))
    data = upgrade_world().game_map.to_dict()
    data["entities"].append(  # type: ignore[attr-defined]
        {"id": "scout", "kind": "scout", "position": [15.5, 8.5]}
    )
    world = Simulation(load_map_data(data))
    assert world.execute(MoveCommand(("scout",), Point(35.5, 8.5))).accepted
    assert world.execute(UpgradeEntityCommand("scout")).accepted
    world.advance(20)
    entity = world.entities["scout"]
    assert entity.upgrade.tier == 2 and entity.profile is definition.profile
    assert entity.position.x > 15.5
    assert entity.move_target == Point(35.5, 8.5)


@pytest.mark.parametrize("kind", [EntityKind.RESOURCE_GENERATOR, EntityKind.MATTER_SYNTHESIZER])
@pytest.mark.parametrize("tier", [1, 2])
@pytest.mark.parametrize("affordable", [0, 1, 2, 3])
def test_box_selected_buildings_upgrade_affordable_idle_members_while_peers_are_running(
    kind: EntityKind, tier: int, affordable: int
) -> None:
    world = Simulation(
        load_map_data(
            {
                "id": "batch_upgrades",
                "name": "Batch upgrades",
                "width": 24,
                "height": 16,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": f"income_{index}", "kind": kind.value, "position": [2 + index * 4, 2]}
                    for index in range(4)
                ],
            }
        )
    )
    definition = kind.upgrades[tier - 1]
    for entity in world.entities.values():
        entity.upgrade.tier = tier
    world.resources["player"] = definition.cost
    assert world.execute(UpgradeEntityCommand("income_0")).accepted
    running = world.entities["income_0"]
    running.upgrade.remaining_ticks -= 7
    world.resources["player"] = definition.cost * affordable + 17
    app = AirtsApp(world)
    app._select_entities(Point(1, 1), Point(18, 6))
    assert app.selected_entities == set(world.entities)
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 18)
    screen = pygame.Surface(app.window_size)

    def draw() -> list[pygame.Rect]:
        app._command_buttons.clear()
        selection.draw_selection(app, screen)
        return [rect for rect, action in app._command_buttons if action.startswith("upgrade:")]

    buttons = draw()
    assert len(buttons) == bool(affordable)
    assert any(action.startswith("cancel_upgrade:") for _, action in app._command_buttons)
    if affordable:
        app._handle_command_click(buttons[0].center)
        assert app.notice == f"Upgrade started: {affordable}."
    assert world.resources["player"] == 17
    assert running.upgrade.remaining_ticks == definition.duration_ticks - 7
    assert [world.entities[f"income_{index}"].upgrade.remaining_ticks for index in range(1, 4)] == [
        definition.duration_ticks if index <= affordable else 0 for index in range(1, 4)
    ]
    if affordable < 3:
        # A second click can start the remaining peers without canceling paid upgrades.
        world.resources["player"] = definition.cost * (3 - affordable)
        app._handle_command_click(draw()[0].center)
        assert world.resources["player"] == 0
        assert running.upgrade.remaining_ticks == definition.duration_ticks - 7
        assert all(entity.upgrade.remaining_ticks for entity in world.entities.values())


@pytest.mark.parametrize("balance_at_click", [0, 120, 240])
def test_batch_upgrade_uses_live_balance_after_buttons_were_drawn(balance_at_click: int) -> None:
    world = upgrade_world()
    second = replace(world.entities["extractor"], entity_id="second", position=Point(4, 2))
    second.upgrade = type(second.upgrade)()
    world.entities["second"] = second
    world.occupancy.place(second.entity_id, second.occupied_cells)
    world.resources["player"] = 120
    app = AirtsApp(world)
    app.selected_entities = {"extractor", "second"}
    app._selection_changed()
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 18)
    selection.draw_selection(app, pygame.Surface(app.window_size))
    button = next(rect for rect, action in app._command_buttons if action.startswith("upgrade:"))

    world.resources["player"] = balance_at_click
    app._handle_command_click(button.center)

    assert sum(entity.upgrade.remaining_ticks > 0 for entity in world.entities.values()) == (
        balance_at_click // 120
    )
    assert world.resources["player"] == 0
    assert app.notice == f"Upgrade started: {balance_at_click // 120}."


def test_mixed_tier_selection_controls_only_the_matching_group_and_enemies_are_read_only() -> None:
    world = upgrade_world()
    second = replace(world.entities["extractor"], entity_id="second", position=Point(4, 2))
    # Separate upgrade components, as for normally constructed buildings.
    second.upgrade = type(second.upgrade)(tier=2)
    world.entities["second"] = second
    world.occupancy.place(second.entity_id, second.occupied_cells)
    app = AirtsApp(world)
    pygame.font.init()
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 18)
    app.selected_entities = {"extractor", "second"}
    app._selection_changed()
    screen = pygame.Surface(app.window_size)
    selection.draw_selection(app, screen)
    buttons = [
        (rect, action) for rect, action in app._command_buttons if action.startswith("upgrade:")
    ]
    assert len(buttons) == 2
    rect, _ = next(item for item in buttons if '"second"' in item[1])
    app._handle_command_click(rect.center)
    assert second.upgrade.remaining_ticks == 600
    assert world.entities["extractor"].upgrade.remaining_ticks == 0
    app.selected_entities = {"enemy"}
    app._selection_changed()
    app._command_buttons.clear()
    selection.draw_selection(app, screen)
    assert not any(
        action.startswith(("upgrade:", "cancel_upgrade:")) for _, action in app._command_buttons
    )
