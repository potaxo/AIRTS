"""Spatial-reference CRUD, geometry validation, and grounding selection commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.automations import DefendParameters, PatrolParameters, ProductionParameters
from airts.commands import (
    CommandResult,
    CreateSpatialReferenceCommand,
    DeleteRegionCommand,
    DeleteSpatialReferenceCommand,
    EditSpatialReferenceCommand,
    RenameRegionCommand,
    SetSelectionCommand,
)
from airts.events import EventType
from airts.geometry import PointTarget, SpatialTarget
from airts.spatial import GroundingSelection, SpatialKind
from airts.validation import ValidationFailure, ValidationPhase

if TYPE_CHECKING:
    from airts.simulation import Simulation


def validate_geometry(simulation: Simulation, target: SpatialTarget) -> ValidationFailure | None:
    points = (target.point,) if isinstance(target, PointTarget) else target.points
    if any(not simulation.game_map.contains(point) for point in points):
        return ValidationFailure(ValidationPhase.SPATIAL, "TARGET_OUTSIDE_MAP", "target")
    return None


def create_spatial_reference(
    simulation: Simulation, command: CreateSpatialReferenceCommand
) -> CommandResult:
    failure = simulation._validate_geometry(command.target)
    if failure is not None:
        return simulation._reject_validation("create_spatial_reference", failure)
    try:
        reference = simulation.spatial.create(command.target, simulation.tick, command.name)
    except ValueError as error:
        return simulation._reject_validation(
            "create_spatial_reference",
            ValidationFailure(ValidationPhase.SCHEMA, str(error), "name"),
        )
    simulation.events.record(
        simulation.tick,
        EventType.SPATIAL_REFERENCE_CREATED,
        reference.reference_id,
        kind=reference.kind.value,
        name=reference.name,
    )
    return simulation._accept("create_spatial_reference", reference_id=reference.reference_id)


def edit_spatial_reference(
    simulation: Simulation, command: EditSpatialReferenceCommand
) -> CommandResult:
    failure = simulation._validate_geometry(command.target)
    if failure is not None:
        return simulation._reject_validation("edit_spatial_reference", failure)
    try:
        reference = simulation.spatial.edit(command.reference_id, command.target, simulation.tick)
    except ValueError as error:
        return simulation._reject_validation(
            "edit_spatial_reference",
            ValidationFailure(ValidationPhase.REFERENCE, str(error), "reference_id"),
        )
    simulation.events.record(
        simulation.tick,
        EventType.SPATIAL_REFERENCE_EDITED,
        reference.reference_id,
        kind=reference.kind.value,
    )
    return simulation._accept("edit_spatial_reference", reference_id=reference.reference_id)


def rename_region(simulation: Simulation, command: RenameRegionCommand) -> CommandResult:
    try:
        reference = simulation.spatial.rename_region(
            command.reference_id, command.name, simulation.tick
        )
    except ValueError as error:
        return simulation._reject_validation(
            "rename_region", ValidationFailure(ValidationPhase.SCHEMA, str(error), "name")
        )
    simulation.events.record(
        simulation.tick,
        EventType.SPATIAL_REFERENCE_NAMED,
        reference.reference_id,
        name=reference.name,
    )
    return simulation._accept("rename_region", reference_id=reference.reference_id)


def delete_spatial_reference(
    simulation: Simulation, command: DeleteRegionCommand | DeleteSpatialReferenceCommand
) -> CommandResult:
    reference = simulation.spatial.references.get(command.reference_id)
    if reference is None:
        return simulation._reject_validation(
            "delete_spatial_reference",
            ValidationFailure(
                ValidationPhase.REFERENCE, "UNKNOWN_SPATIAL_REFERENCE", "reference_id"
            ),
        )
    if isinstance(command, DeleteRegionCommand) and reference.kind is not SpatialKind.REGION:
        return simulation._reject_validation(
            "delete_region",
            ValidationFailure(
                ValidationPhase.CAPABILITY, "ONLY_REGIONS_CAN_BE_DELETED", "reference_id"
            ),
        )
    affected = [
        automation
        for automation in simulation.automations.values()
        if not automation.status.terminal
        and (
            (
                isinstance(automation.parameters, PatrolParameters | DefendParameters)
                and automation.parameters.target == reference.geometry
            )
            or (
                isinstance(automation.parameters, ProductionParameters)
                and (
                    automation.parameters.patrol_target == reference.geometry
                    or automation.parameters.defend_target == reference.geometry
                )
            )
        )
    ]
    for automation in affected:
        simulation._cancel(automation.automation_id, command.owner_id)
    simulation.spatial.delete(command.reference_id)
    simulation.selection = GroundingSelection(
        simulation.selection.entity_ids,
        simulation.selection.point_ids,
        tuple(item for item in simulation.selection.route_ids if item != command.reference_id),
        tuple(item for item in simulation.selection.region_ids if item != command.reference_id),
    )
    simulation.events.record(
        simulation.tick,
        EventType.SPATIAL_REFERENCE_DELETED,
        command.reference_id,
        affected_automation_ids=[item.automation_id for item in affected],
    )
    reason = f"{reference.kind.value.upper()}_DELETED"
    if affected:
        reason += ":CANCELED:" + ",".join(item.automation_id for item in affected)
    return CommandResult(True, reason, reference_id=command.reference_id)


def set_selection(simulation: Simulation, command: SetSelectionCommand) -> CommandResult:
    if len(set(command.entity_ids)) != len(command.entity_ids):
        return simulation._reject_validation(
            "set_selection",
            ValidationFailure(ValidationPhase.SCHEMA, "DUPLICATE_SELECTION", "entity_ids"),
        )
    for entity_id in command.entity_ids:
        entity = simulation.entities.get(entity_id)
        if entity is None:
            return simulation._reject_validation(
                "set_selection",
                ValidationFailure(ValidationPhase.REFERENCE, "UNKNOWN_ENTITY", "entity_ids"),
            )
        if entity.owner_id != command.owner_id:
            return simulation._reject_validation(
                "set_selection",
                ValidationFailure(ValidationPhase.OWNERSHIP, "ENTITY_NOT_OWNED", "entity_ids"),
            )
    groups = (
        (command.point_ids, SpatialKind.POINT),
        (command.route_ids, SpatialKind.ROUTE),
        (command.region_ids, SpatialKind.REGION),
    )
    for reference_ids, kind in groups:
        if len(set(reference_ids)) != len(reference_ids):
            return simulation._reject_validation(
                "set_selection",
                ValidationFailure(
                    ValidationPhase.SCHEMA, "DUPLICATE_SELECTION", f"{kind.value}_ids"
                ),
            )
        for reference_id in reference_ids:
            reference = simulation.spatial.references.get(reference_id)
            if reference is None or reference.kind is not kind:
                return simulation._reject_validation(
                    "set_selection",
                    ValidationFailure(
                        ValidationPhase.REFERENCE,
                        "INVALID_SPATIAL_SELECTION",
                        f"{kind.value}_ids",
                    ),
                )
    simulation.selection = GroundingSelection(
        command.entity_ids, command.point_ids, command.route_ids, command.region_ids
    )
    simulation.events.record(
        simulation.tick,
        EventType.SELECTION_CHANGED,
        command.owner_id,
        **simulation.selection.to_dict(),
    )
    return simulation._accept("set_selection")
