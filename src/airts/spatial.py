"""Authoritative, serializable player-created spatial references."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from airts.geometry import PointTarget, PolygonRegion, PolylineTarget, SpatialTarget, target_to_dict


class SpatialKind(StrEnum):
    POINT = "point"
    ROUTE = "route"
    REGION = "region"


def spatial_kind(target: SpatialTarget) -> SpatialKind:
    if isinstance(target, PointTarget):
        return SpatialKind.POINT
    if isinstance(target, PolylineTarget):
        return SpatialKind.ROUTE
    if isinstance(target, PolygonRegion):
        return SpatialKind.REGION
    raise TypeError(f"unsupported spatial target: {type(target).__name__}")


@dataclass(slots=True)
class SpatialReference:
    reference_id: str
    kind: SpatialKind
    geometry: SpatialTarget
    created_tick: int
    modified_tick: int
    name: str | None = None

    @property
    def persistent(self) -> bool:
        return self.kind is SpatialKind.REGION and self.name is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.reference_id,
            "kind": self.kind.value,
            "geometry": target_to_dict(self.geometry),
            "name": self.name,
            "persistent": self.persistent,
            "created_tick": self.created_tick,
            "modified_tick": self.modified_tick,
        }


@dataclass(slots=True)
class SpatialStore:
    references: dict[str, SpatialReference] = field(default_factory=dict)
    next_numbers: dict[SpatialKind, int] = field(
        default_factory=lambda: {kind: 1 for kind in SpatialKind}
    )

    def create(
        self, geometry: SpatialTarget, tick: int, name: str | None = None
    ) -> SpatialReference:
        kind = spatial_kind(geometry)
        normalized_name = self._validated_name(kind, name, None)
        number = self.next_numbers[kind]
        self.next_numbers[kind] += 1
        reference_id = f"{kind.value}_{number:03d}"
        reference = SpatialReference(reference_id, kind, geometry, tick, tick, normalized_name)
        self.references[reference_id] = reference
        return reference

    def edit(self, reference_id: str, geometry: SpatialTarget, tick: int) -> SpatialReference:
        reference = self.get(reference_id)
        if spatial_kind(geometry) is not reference.kind:
            raise ValueError("SPATIAL_KIND_CANNOT_CHANGE")
        reference.geometry = geometry
        reference.modified_tick = tick
        return reference

    def rename_region(self, reference_id: str, name: str, tick: int) -> SpatialReference:
        reference = self.get(reference_id)
        reference.name = self._validated_name(reference.kind, name, reference_id)
        reference.modified_tick = tick
        return reference

    def get(self, reference_id: str) -> SpatialReference:
        try:
            return self.references[reference_id]
        except KeyError as error:
            raise ValueError("UNKNOWN_SPATIAL_REFERENCE") from error

    def _validated_name(
        self, kind: SpatialKind, name: str | None, current_id: str | None
    ) -> str | None:
        if name is None:
            return None
        if kind is not SpatialKind.REGION:
            raise ValueError("ONLY_REGIONS_CAN_BE_NAMED")
        normalized = name.strip()
        if not normalized:
            raise ValueError("REGION_NAME_EMPTY")
        if any(
            reference.reference_id != current_id
            and reference.name is not None
            and reference.name.casefold() == normalized.casefold()
            for reference in self.references.values()
        ):
            raise ValueError("REGION_NAME_NOT_UNIQUE")
        return normalized

    def to_dict(self) -> dict[str, object]:
        return {
            "references": {
                reference_id: reference.to_dict()
                for reference_id, reference in sorted(self.references.items())
            },
            "next_numbers": {kind.value: self.next_numbers[kind] for kind in SpatialKind},
        }


@dataclass(frozen=True, slots=True)
class GroundingSelection:
    entity_ids: tuple[str, ...] = ()
    point_ids: tuple[str, ...] = ()
    route_ids: tuple[str, ...] = ()
    region_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_ids": list(self.entity_ids),
            "point_ids": list(self.point_ids),
            "route_ids": list(self.route_ids),
            "region_ids": list(self.region_ids),
        }
