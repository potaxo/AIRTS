"""Spatial references, grounded selections, and editing controls."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.integration.scenarios import interaction_simulation

from airts.adapters.replay import capture_replay, load_replay, run_replay, save_replay
from airts.contracts.automations import AutomationStatus
from airts.contracts.commands import (
    CreatePatrolCommand,
    CreateSpatialReferenceCommand,
    DeleteRegionCommand,
    EditSpatialReferenceCommand,
    ModifyAutomationCommand,
    RenameRegionCommand,
    SetSelectionCommand,
    command_to_dict,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PointTarget, PolylineTarget, rectangle_region
from airts.presentation.app import AirtsApp, InputMode
from airts.simulation import Simulation
from airts.world.map import GameMap, load_map_data
from airts.world.units.catalog import EntityKind


def test_spatial_references_have_stable_ids_unique_names_and_edit_in_place(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map())
    point = simulation.execute(CreateSpatialReferenceCommand(PointTarget(Point(2, 2))))
    first_region = simulation.execute(
        CreateSpatialReferenceCommand(rectangle_region(Point(3, 3), Point(6, 6)), "Bridge")
    )
    second_region = simulation.execute(
        CreateSpatialReferenceCommand(rectangle_region(Point(4, 4), Point(7, 7)))
    )

    assert point.reference_id == "point_001"
    assert first_region.reference_id == "region_001"
    assert second_region.reference_id == "region_002"
    assert not simulation.execute(
        RenameRegionCommand(second_region.reference_id or "", "bridge")
    ).accepted

    edited = PolylineTarget((Point(2, 2), Point(8, 8)))
    wrong_kind = simulation.execute(EditSpatialReferenceCommand(point.reference_id or "", edited))
    assert not wrong_kind.accepted
    assert simulation.spatial.references[point.reference_id or ""].geometry == PointTarget(
        Point(2, 2)
    )
    replacement = PointTarget(Point(3, 2))
    assert simulation.execute(
        EditSpatialReferenceCommand(point.reference_id or "", replacement)
    ).accepted
    assert simulation.spatial.references[point.reference_id or ""].geometry == replacement
    assert set(simulation.spatial.references) == {"point_001", "region_001", "region_002"}


def test_grounded_selection_is_typed_owned_and_replayable(
    make_map: Callable[[int], GameMap], tmp_path: Path
) -> None:
    simulation = Simulation(make_map(2), random_seed=12)
    route = simulation.execute(
        CreateSpatialReferenceCommand(PolylineTarget((Point(2, 2), Point(8, 8))))
    )
    selection = SetSelectionCommand(
        entity_ids=("unit_01", "unit_02"), route_ids=(route.reference_id or "",)
    )
    assert simulation.execute(selection).accepted
    assert not simulation.execute(
        SetSelectionCommand(point_ids=(route.reference_id or "",))
    ).accepted

    destination = tmp_path / "phase4-replay.json"
    save_replay(simulation, destination)
    replayed = run_replay(load_replay(destination))
    assert replayed.snapshot() == simulation.snapshot()


def test_destroyed_selected_entities_are_pruned_from_core_and_ui(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    simulation.execute(SetSelectionCommand(entity_ids=("unit_01", "unit_02")))
    app = AirtsApp(simulation)
    app.selected_entities = {"unit_01", "unit_02"}
    app.inspected_entity_id = "unit_01"

    simulation.remove_entity("unit_01", "COMBAT_DESTROYED")
    app._prune_removed_entities()

    assert simulation.selection.entity_ids == ("unit_02",)
    assert app.selected_entities == {"unit_02"}
    assert app.inspected_entity_id is None


def test_automation_modification_is_atomic_and_inspectable(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    created = simulation.execute(
        CreatePatrolCommand(("unit_01",), PointTarget(Point(5, 5)), title="Old")
    )
    automation = simulation.automations[created.automation_id or ""]
    original = automation.parameters.to_dict()

    rejected = simulation.execute(
        ModifyAutomationCommand(
            automation.automation_id,
            title="Should not apply",
            target=PointTarget(Point(99, 99)),
        )
    )
    assert not rejected.accepted
    assert automation.title == "Old"
    assert automation.parameters.to_dict() == original

    target = PolylineTarget((Point(3, 3), Point(8, 3), Point(8, 8)))
    assert simulation.execute(
        ModifyAutomationCommand(automation.automation_id, title="New", priority=4, target=target)
    ).accepted
    assert automation.title == "New"
    assert automation.priority == 4
    assert (
        automation.parameters.to_dict()["target"]
        == command_to_dict(CreateSpatialReferenceCommand(target))["target"]
    )
    assert simulation.events.query(
        event_types=frozenset({EventType.AUTOMATION_MODIFIED}),
        subject_id=automation.automation_id,
    )


def test_drag_selection_chooses_only_friendly_units_and_keeps_grounding() -> None:
    simulation = interaction_simulation()
    app = AirtsApp(simulation)
    region = simulation.execute(
        CreateSpatialReferenceCommand(rectangle_region(Point(1, 1), Point(8, 8)))
    )
    app.selected_regions = {region.reference_id or ""}

    app._select_entities(Point(1, 1), Point(8, 8))

    assert app.selected_entities == {"unit", "tank"}
    assert app.selected_regions == {region.reference_id}
    assert simulation.selection.entity_ids == ("tank", "unit")
    assert simulation.selection.region_ids == (region.reference_id,)


@pytest.mark.parametrize("owner", ["player", "enemy"])
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (EntityKind.SCOUT, {"other"}),
        (EntityKind.LIGHT_TANK, {"other"}),
        (EntityKind.HEAVY_TANK, {"other"}),
        (EntityKind.LIGHT_ARTILLERY, {"other"}),
        (EntityKind.ARTILLERY, {"other"}),
        (EntityKind.TANK_DESTROYER, {"other"}),
        (EntityKind.BUILDER, {"builder", "other"}),
        (EntityKind.TURRET, {"builder"}),
    ],
)
def test_box_selection_prefers_attack_capable_units_and_keeps_unarmed_fallback(
    owner: str, kind: EntityKind, expected: set[str]
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "combat_selection",
                "name": "Combat selection",
                "width": 24,
                "height": 16,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "builder", "kind": "builder", "owner": owner, "position": [2.5, 2.5]},
                    {"id": "other", "kind": kind.value, "owner": owner, "position": [5, 2]},
                    {"id": "factory", "kind": "factory", "owner": owner, "position": [10, 2]},
                ],
            }
        )
    )
    app = AirtsApp(simulation)
    app._select_entities(Point(1, 1), Point(16, 8))

    assert app.selected_entities == expected
    assert set(simulation.selection.entity_ids) == (expected if owner == "player" else set())


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("additive", [False, True])
def test_building_box_selection_replaces_or_toggles_and_keeps_grounding(
    reverse: bool, additive: bool
) -> None:
    simulation = interaction_simulation()
    app = AirtsApp(simulation)
    app.selected_entities = {"factory"}
    start, end = Point(3, 3), Point(12, 6)
    if reverse:
        start, end = end, start

    app._select_entities(start, end, additive=additive)

    expected = {"generator"} if additive else {"factory", "generator"}
    assert app.selected_entities == expected
    assert set(simulation.selection.entity_ids) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (Point(1, 1), Point(18, 8), {"building"}),
        (Point(9, 1), Point(18, 8), {"enemy_unit"}),
        (Point(13, 1), Point(18, 8), {"enemy_building"}),
    ],
)
def test_box_selection_keeps_owner_priority_and_enemy_buildings_read_only(
    start: Point, end: Point, expected: set[str]
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "building_selection",
                "name": "Building selection",
                "width": 24,
                "height": 16,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "building", "kind": "factory", "position": [2, 2]},
                    {
                        "id": "enemy_unit",
                        "kind": "scout",
                        "owner": "enemy",
                        "position": [10.5, 2.5],
                    },
                    {
                        "id": "enemy_building",
                        "kind": "factory",
                        "owner": "enemy",
                        "position": [14, 2],
                    },
                ],
            }
        )
    )
    app = AirtsApp(simulation)
    app._select_entities(start, end)
    assert app.selected_entities == expected
    assert simulation.selection.entity_ids == (("building",) if "building" in expected else ())


def test_line_finishes_with_right_click_and_preserves_selected_units() -> None:
    simulation = interaction_simulation()
    app = AirtsApp(simulation)
    app.selected_entities = {"unit"}
    app.mode = InputMode.LINE
    app._handle_mouse_down(1, (80, 80))
    app._handle_mouse_down(1, (120, 120))

    app._handle_mouse_down(3, (140, 140))

    assert app.mode is InputMode.SELECT
    assert app.selected_entities == {"unit"}
    assert simulation.selection.entity_ids == ("unit",)
    assert simulation.selection.route_ids == ("route_001",)
    assert simulation.spatial.references["route_001"].geometry == PolylineTarget(
        (app._map_point((80, 80)), app._map_point((120, 120)))
    )


def test_line_enter_does_not_finish_and_escape_cancels() -> None:
    app = AirtsApp(interaction_simulation())
    app.mode = InputMode.LINE
    app._handle_mouse_down(1, (80, 80))
    app._handle_mouse_down(1, (120, 120))

    app._handle_key(13)

    assert app.mode is InputMode.LINE
    assert len(app.line_points) == 2

    app._handle_key(27)

    assert app.mode is InputMode.SELECT
    assert not app.line_points


def test_region_deletion_is_replayable_and_cancels_affected_automation() -> None:
    simulation = interaction_simulation()
    target = rectangle_region(Point(7, 7), Point(10, 10))
    region = simulation.execute(CreateSpatialReferenceCommand(target, "Defense"))
    patrol = simulation.execute(CreatePatrolCommand(("unit",), target))
    simulation.execute(SetSelectionCommand(("unit",), region_ids=(region.reference_id or "",)))
    command = DeleteRegionCommand(region.reference_id or "")

    result = simulation.execute(command)

    assert result.accepted
    assert "CANCELED" in result.reason
    assert region.reference_id not in simulation.spatial.references
    assert simulation.automations[patrol.automation_id or ""].status is AutomationStatus.CANCELED
    assert simulation.selection.entity_ids == ("unit",)
    assert not simulation.selection.region_ids
    replayed = run_replay(capture_replay(simulation))
    assert replayed.export_state() == simulation.export_state()


def test_delete_control_removes_selected_route_and_cancels_its_patrol() -> None:
    simulation = interaction_simulation()
    app = AirtsApp(simulation)
    target = PolylineTarget((Point(7, 7), Point(12, 7)))
    route = simulation.execute(CreateSpatialReferenceCommand(target))
    patrol = simulation.execute(CreatePatrolCommand(("unit",), target))
    app._select_reference(route.reference_id)

    app._delete_selected_reference()

    assert route.reference_id not in simulation.spatial.references
    assert simulation.automations[patrol.automation_id or ""].status is AutomationStatus.CANCELED
    assert not app.selected_routes
