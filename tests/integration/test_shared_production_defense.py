"""Several independent producers feed one inspectable defense controller."""

from array import array
from unittest.mock import patch

import pygame
import pytest
from tests.integration.scenarios import builder_factory_simulation

from airts.adapters.persistence import SAVE_SCHEMA, PersistenceError, load_simulation_data
from airts.adapters.replay import capture_replay, run_replay
from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    ProductionParameters,
)
from airts.contracts.commands import (
    CancelAutomationCommand,
    CreateDefendCommand,
    CreateProductionCommand,
    ModifyAutomationCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
    SetDeveloperModeCommand,
)
from airts.contracts.spatial import Point, PolylineTarget, rectangle_region
from airts.presentation.app import AirtsApp
from airts.presentation.gpu_shapes import SHAPE_FLOATS
from airts.presentation.opengl_renderer import OpenGLFrameBuilder
from airts.presentation.ui.automations import panel_rows
from airts.simulation import Simulation
from airts.world.map import load_map_data
from airts.world.units.catalog import EntityKind


def save_document(world: Simulation) -> dict[str, object]:
    return {"schema": SAVE_SCHEMA, "map": world.game_map.to_dict(), "state": world.export_state()}


def start_sources(
    *, line: bool = False, continuous: bool = True
) -> tuple[Simulation, tuple[Automation, ...], Automation]:
    world = Simulation(builder_factory_simulation().game_map)
    assert world.execute(SetDeveloperModeCommand(True)).accepted
    target = (
        PolylineTarget((Point(7, 15), Point(19, 15)))
        if line
        else rectangle_region(Point(7, 12), Point(19, 19))
    )
    sources = []
    for factory, kind in (("factory", EntityKind.SCOUT), ("factory_2", EntityKind.HEAVY_TANK)):
        result = world.execute(
            CreateProductionCommand(factory, kind, 1, continuous=continuous, defend_target=target)
        )
        assert result.accepted
        sources.append(world.automations[result.automation_id or ""])
    world.advance(EntityKind.HEAVY_TANK.profile.production_ticks)
    parameters = sources[0].parameters
    assert isinstance(parameters, ProductionParameters)
    return world, tuple(sources), world.automations[parameters.defend_automation_id or ""]


@pytest.mark.parametrize("line", [False, True], ids=["area", "line"])
@pytest.mark.parametrize("continuous", [False, True], ids=["finite", "loop"])
def test_mixed_producers_share_membership_panel_entry_and_world_circle(
    line: bool, continuous: bool
) -> None:
    world, sources, defense = start_sources(line=line, continuous=continuous)
    ids = set()
    for source in sources:
        parameters = source.parameters
        assert isinstance(parameters, ProductionParameters)
        assert parameters.defend_automation_id == defense.automation_id
        ids.update(parameters.produced_entity_ids)
    assert set(defense.entity_ids) == ids
    assert all(world.assignments[key] == defense.automation_id for key in ids)
    assert isinstance(defense.parameters, DefendParameters)
    assert defense.parameters.production_defense
    assert len(set(defense.parameters.stations.values())) == len(ids)
    assert [job for job in world.live_automations if job.kind is AutomationKind.DEFEND] == [defense]
    app = AirtsApp(world)
    app.automation_panel.group = None
    assert panel_rows(app) == (defense,)
    app.automation_panel.group = AutomationKind.PRODUCTION
    assert len(panel_rows(app)) == (2 if continuous else 0)
    with patch("pygame.draw.circle", wraps=pygame.draw.circle) as circle:
        app._draw_assembly_glows(pygame.Surface(app.window_size))
    assert circle.call_count == (0 if line else 3)  # Three layers of one assembly glow.
    packed, *_ = OpenGLFrameBuilder._build_entities(app, (0, 0), 1)
    values = array("f")
    values.frombytes(packed)
    glows = sum(
        tuple(values[i + 14 : i + 18]) == pytest.approx((92 / 255, 177 / 255, 1.0, 20 / 255))
        for i in range(0, len(values), SHAPE_FLOATS)
    )
    assert glows == (0 if line else 1)


def test_shared_controls_update_all_sources_and_cancel_leaves_factories_running() -> None:
    world, sources, defense = start_sources()
    assert world.execute(ModifyAutomationCommand(defense.automation_id, auto_repair=True)).accepted
    assert all(
        isinstance(job.parameters, ProductionParameters) and job.parameters.auto_repair
        for job in sources
    )
    assert world.execute(
        ModifyAutomationCommand(sources[0].automation_id, auto_repair=False)
    ).accepted
    assert all(
        isinstance(job.parameters, ProductionParameters) and not job.parameters.auto_repair
        for job in sources
    )
    old_members = len(defense.entity_ids)
    assert world.execute(PauseAutomationCommand(defense.automation_id)).accepted
    world.advance(EntityKind.HEAVY_TANK.profile.production_ticks)
    assert defense.status is AutomationStatus.PAUSED
    assert len(defense.entity_ids) > old_members
    assert all(not world.entities[key].path for key in defense.entity_ids)
    assert world.execute(ResumeAutomationCommand(defense.automation_id)).accepted
    target = rectangle_region(Point(6, 16), Point(12, 22))
    assert world.execute(ModifyAutomationCommand(defense.automation_id, target=target)).accepted
    assert all(
        isinstance(job.parameters, ProductionParameters) and job.parameters.defend_target == target
        for job in sources
    )
    restored = load_simulation_data(save_document(world))
    assert restored.export_state() == world.export_state()
    for instance in (world, restored):
        instance.advance(EntityKind.SCOUT.profile.production_ticks)
    assert restored.export_state() == world.export_state()
    assert world.execute(CancelAutomationCommand(defense.automation_id)).accepted
    for job in sources:
        assert isinstance(job.parameters, ProductionParameters)
        assert job.parameters.defend_target is None
        assert world.assignments[job.parameters.factory_id] == job.automation_id
    world.advance(EntityKind.SCOUT.profile.production_ticks)
    assert all(job.kind is not AutomationKind.DEFEND for job in world.live_automations)
    assert run_replay(capture_replay(world)).snapshot() == world.snapshot()


def test_shared_target_edit_includes_a_factory_whose_first_unit_is_still_building() -> None:
    world = builder_factory_simulation()
    target = rectangle_region(Point(7, 12), Point(19, 19))
    sources = []
    for factory, kind in (("factory", EntityKind.SCOUT), ("factory_2", EntityKind.HEAVY_TANK)):
        result = world.execute(
            CreateProductionCommand(factory, kind, 1, continuous=True, defend_target=target)
        )
        assert result.accepted
        sources.append(world.automations[result.automation_id or ""].parameters)
    world.advance(EntityKind.SCOUT.profile.production_ticks)
    first, second = sources
    assert isinstance(first, ProductionParameters) and isinstance(second, ProductionParameters)
    assert not second.produced_entity_ids
    assert first.defend_automation_id == second.defend_automation_id
    new_target = rectangle_region(Point(6, 16), Point(12, 22))
    assert world.execute(
        ModifyAutomationCommand(first.defend_automation_id or "", target=new_target)
    ).accepted
    world.advance(
        EntityKind.HEAVY_TANK.profile.production_ticks - EntityKind.SCOUT.profile.production_ticks
    )
    assert second.produced_entity_ids
    assert second.defend_target == new_target
    assert second.defend_automation_id == first.defend_automation_id
    assert len([job for job in world.live_automations if job.kind is AutomationKind.DEFEND]) == 1


def test_same_area_reassignment_and_new_requests_join_the_existing_controller() -> None:
    world, sources, defense = start_sources()
    assert isinstance(defense.parameters, DefendParameters)
    target = defense.parameters.target
    assert world.execute(
        ModifyAutomationCommand(sources[0].automation_id, target=target, auto_repair=True)
    ).accepted
    for source in sources:
        assert isinstance(source.parameters, ProductionParameters)
        assert source.parameters.defend_automation_id == defense.automation_id
        assert source.parameters.auto_repair
    result = world.execute(
        CreateProductionCommand("factory", EntityKind.BUILDER, 1, defend_target=target)
    )
    assert result.accepted
    arrival = world.automations[result.automation_id or ""].parameters
    assert isinstance(arrival, ProductionParameters)
    assert arrival.defend_automation_id == defense.automation_id and arrival.auto_repair
    world.advance(EntityKind.BUILDER.profile.production_ticks)
    assert set(arrival.produced_entity_ids) <= set(defense.entity_ids)
    assert [job for job in world.live_automations if job.kind is AutomationKind.DEFEND] == [defense]


def test_retargeting_or_canceling_one_factory_keeps_existing_shared_defenders() -> None:
    world, sources, defense = start_sources()
    members = tuple(defense.entity_ids)
    target = rectangle_region(Point(4, 16), Point(10, 22))
    assert world.execute(ModifyAutomationCommand(sources[0].automation_id, target=target)).accepted
    assert tuple(defense.entity_ids) == members
    assert world.execute(CancelAutomationCommand(sources[1].automation_id)).accepted
    assert defense.status is AutomationStatus.ACTIVE
    world.advance(EntityKind.SCOUT.profile.production_ticks)
    source = sources[0].parameters
    assert isinstance(source, ProductionParameters)
    second = world.automations[source.defend_automation_id or ""]
    assert second is not defense
    assert isinstance(second.parameters, DefendParameters) and second.parameters.target == target
    before = world.export_state()
    result = world.execute(
        ModifyAutomationCommand(second.automation_id, target=defense.parameters.target)
    )
    assert not result.accepted and result.reason == "PRODUCTION_DEFENSE_TARGET_IN_USE"
    # Rejection may record its diagnostic but never changes groups, links or resources.
    assert world.export_state()["automations"] == before["automations"]
    assert load_simulation_data(save_document(world)).snapshot() == world.snapshot()


def test_same_target_does_not_merge_opposing_owners() -> None:
    data = builder_factory_simulation().game_map.to_dict()
    data["entities"][1]["owner"] = "enemy"  # type: ignore[index]
    world = Simulation(load_map_data(data))
    target = rectangle_region(Point(7, 12), Point(19, 19))
    for owner, factory in (("player", "factory"), ("enemy", "factory_2")):
        world.resources[owner] = 10_000
        assert world.execute(
            CreateProductionCommand(
                factory, EntityKind.SCOUT, 1, owner_id=owner, defend_target=target
            )
        ).accepted
    world.advance(EntityKind.SCOUT.profile.production_ticks)
    defenses = [job for job in world.live_automations if job.kind is AutomationKind.DEFEND]
    assert len(defenses) == 2
    assert {job.owner_id for job in defenses} == {"player", "enemy"}


@pytest.mark.parametrize("corruption", ["missing", "nonbool", "ordinary", "duplicate"])
def test_saves_reject_incoherent_production_defense_groups(corruption: str) -> None:
    world, _, defense = start_sources()
    if corruption == "duplicate":
        assert world.execute(CreateDefendCommand(("scout",), defense.parameters.target)).accepted
    document = save_document(world)
    state = document["state"]
    assert isinstance(state, dict)
    jobs = state["automations"]
    assert isinstance(jobs, dict)
    record = jobs[defense.automation_id]
    if corruption == "missing":
        del record["parameters"]["production_defense"]
        message = "defend.production_defense"
    elif corruption == "nonbool":
        record["parameters"]["production_defense"] = 1
        message = "defend.production_defense"
    elif corruption == "ordinary":
        record["parameters"]["production_defense"] = False
        message = "invalid defend automation"
    else:
        other = next(
            job
            for job in jobs.values()
            if job["template"] == "defend" and job["id"] != defense.automation_id
        )
        other["parameters"]["production_defense"] = True
        message = "duplicate production defense"
    with pytest.raises(PersistenceError, match=message):
        load_simulation_data(document)
