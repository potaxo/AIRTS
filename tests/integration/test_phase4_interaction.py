"""Integration contracts for phase 4 interaction behavior."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from airts.adapters.persistence import load_simulation, save_simulation
from airts.adapters.replay import load_replay, run_replay, save_replay
from airts.commands import (
    CreatePatrolCommand,
    CreateSpatialReferenceCommand,
    EditSpatialReferenceCommand,
    ModifyAutomationCommand,
    RenameRegionCommand,
    SetSelectionCommand,
    command_from_dict,
    command_to_dict,
)
from airts.events import EventLog, EventType
from airts.geometry import Point, PointTarget, PolylineTarget, rectangle_region
from airts.presentation.app import AirtsApp
from airts.simulation import Simulation
from airts.world.map_model import GameMap


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


def test_phase4_commands_round_trip_and_save_restores_spatial_state(
    make_map: Callable[[int], GameMap], tmp_path: Path
) -> None:
    command = ModifyAutomationCommand(
        "automation_001", title="Watch bridge", priority=3, minimum_units=4
    )
    assert command_from_dict(command_to_dict(command)) == command

    simulation = Simulation(make_map())
    region = simulation.execute(
        CreateSpatialReferenceCommand(rectangle_region(Point(2, 2), Point(6, 6)), "North")
    )
    simulation.execute(SetSelectionCommand(region_ids=(region.reference_id or "",)))
    destination = tmp_path / "phase4-save.json"
    save_simulation(simulation, destination)
    restored = load_simulation(destination)
    assert restored.snapshot() == simulation.snapshot()
    assert restored.spatial.references[region.reference_id or ""].persistent


def test_event_query_filters_newest_first_and_validates_limit() -> None:
    events = EventLog()
    events.record(1, EventType.COMMAND_ACCEPTED, "one")
    events.record(2, EventType.AUTOMATION_MODIFIED, "two", reason="EDITED")
    events.record(3, EventType.AUTOMATION_MODIFIED, "two", reason="EDITED_AGAIN")

    result = events.query(
        event_types=frozenset({EventType.AUTOMATION_MODIFIED}), subject_id="two", limit=1
    )
    assert [event.tick for event in result] == [3]
    with pytest.raises(ValueError, match="negative"):
        events.query(limit=-1)
