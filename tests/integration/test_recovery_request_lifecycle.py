"""Accepted control/placement changes own the saved weak-return phase."""

from __future__ import annotations

import pytest
from tests.integration.scenarios import (
    economy_combat_simulation,
    hold_contact_simulation,
    runtime_map,
)

from airts.contracts.automations import DefendParameters, ProductionParameters
from airts.contracts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    Command,
    CreateDefendCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    StopCommand,
)
from airts.contracts.spatial import (
    Point,
    PointTarget,
    PolylineTarget,
    SpatialTarget,
    rectangle_region,
)
from airts.simulation import Simulation
from airts.systems.movement import start_path
from airts.world.entities import UnitState
from airts.world.units.catalog import EntityKind


@pytest.mark.parametrize(
    "command",
    [
        MoveCommand(("holder",), Point(12.5, 5.5)),
        StopCommand(("holder",)),
        HoldPositionCommand(("holder",)),
    ],
    ids=["move", "stop", "hold-reissued"],
)
def test_accepted_direct_control_ends_the_previous_return_phase(command: Command) -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    holder = simulation.entities["holder"]
    # A saved return phase is prepared independently of the numerical solver.
    holder.recovery_request_tick = 0
    holder.recovery_released = True
    assert simulation.execute(command).accepted
    assert holder.recovery_request_tick is None
    assert not holder.recovery_released


def test_rejected_control_preserves_the_anchor_and_return_phase() -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    holder = simulation.entities["holder"]
    holder.recovery_request_tick = 0
    holder.recovery_released = True
    before = simulation.snapshot()
    assert not simulation.execute(MoveCommand(("holder",), Point(-1, 5.5))).accepted
    assert simulation.snapshot() == before


def test_attack_and_repair_assignment_changes_end_the_previous_return_phase() -> None:
    simulation = economy_combat_simulation()
    entity = simulation.entities["tank"]
    assert simulation.execute(HoldPositionCommand(("tank",))).accepted
    entity.recovery_request_tick = 0
    entity.recovery_released = True
    assert simulation.execute(AttackCommand(("tank",), "enemy")).accepted
    assert entity.recovery_request_tick is None
    assert not entity.recovery_released

    defended = simulation.execute(CreateDefendCommand(("tank",), PointTarget(Point(6.5, 2.5))))
    assert defended.accepted
    entity.recovery_request_tick = 0
    entity.recovery_released = True
    entity.health = 17
    repaired = simulation.execute(CreateRepairAndReturnCommand(("tank",)))
    assert repaired.accepted
    assert simulation.assignments["tank"] == repaired.automation_id
    assert entity.recovery_request_tick is None
    assert not entity.recovery_released

    entity.recovery_request_tick = 0
    entity.recovery_released = True
    assert simulation.execute(CancelAutomationCommand(repaired.automation_id or "")).accepted
    assert simulation.assignments["tank"] == defended.automation_id
    assert entity.recovery_request_tick is None
    assert not entity.recovery_released


def test_weak_defense_route_and_unchanged_station_preserve_the_return_phase() -> None:
    simulation = hold_contact_simulation()
    target = PointTarget(Point(8.5, 5.5))
    created = simulation.execute(CreateDefendCommand(("holder",), target))
    assert created.accepted
    automation = simulation.automations[created.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    holder = simulation.entities["holder"]
    holder.defend_arrived = True
    holder.recovery_request_tick = 0
    holder.recovery_released = True
    station = automation.parameters.stations["holder"]
    route = simulation._routes.shared_path(holder.position, station, simulation._building_cells())
    start_path(simulation, holder, station, route, automation.automation_id, UnitState.DEFENDING)
    simulation._assign("holder", automation)
    assert simulation.execute(
        ModifyAutomationCommand(automation.automation_id, title="Retained station", target=target)
    ).accepted
    assert holder.recovery_request_tick == 0
    assert holder.recovery_released
    assert holder.defend_arrived
    assert holder.path

    assert simulation.execute(
        ModifyAutomationCommand(automation.automation_id, target=PointTarget(Point(9.5, 5.5)))
    ).accepted
    assert holder.recovery_request_tick is None
    assert not holder.recovery_released
    assert not holder.defend_arrived
    assert not holder.path


@pytest.mark.parametrize(
    ("target", "replacement"),
    [
        (
            PolylineTarget((Point(14.5, 5.5), Point(18.5, 5.5))),
            PolylineTarget((Point(14.5, 8.5), Point(18.5, 8.5))),
        ),
        (
            rectangle_region(Point(14, 3), Point(18, 7)),
            rectangle_region(Point(14, 8), Point(18, 12)),
        ),
    ],
    ids=["line", "gathering-area"],
)
def test_repeated_production_binding_retains_unchanged_member_return_phases(
    target: SpatialTarget, replacement: SpatialTarget
) -> None:
    simulation = Simulation(runtime_map())
    created = simulation.execute(
        CreateProductionCommand(
            "factory", EntityKind.SCOUT, 1, continuous=True, defend_target=target
        )
    )
    assert created.accepted
    production = simulation.automations[created.automation_id or ""]
    assert isinstance(production.parameters, ProductionParameters)
    while len(production.parameters.produced_entity_ids) < 3 and simulation.tick < 30:
        simulation.advance()
    ids = tuple(production.parameters.produced_entity_ids)
    assert len(ids) == 3
    defend = simulation.automations[production.parameters.defend_automation_id or ""]
    assert isinstance(defend.parameters, DefendParameters)
    stations = dict(defend.parameters.stations)
    for key in ids:
        entity = simulation.entities[key]
        entity.recovery_request_tick = simulation.tick
        entity.recovery_released = True
    assert simulation.execute(
        ModifyAutomationCommand(production.automation_id, target=target)
    ).accepted
    assert isinstance(defend.parameters, DefendParameters)
    assert defend.parameters.stations == stations
    for key in ids:
        entity = simulation.entities[key]
        assert entity.recovery_request_tick == simulation.tick
        assert entity.recovery_released
        assert simulation.assignments[key] == defend.automation_id

    assert simulation.execute(
        ModifyAutomationCommand(production.automation_id, target=replacement)
    ).accepted
    for key in ids:
        entity = simulation.entities[key]
        assert entity.recovery_request_tick is None
        assert not entity.recovery_released
        assert simulation.assignments[key] == defend.automation_id
