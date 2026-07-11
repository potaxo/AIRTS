"""Structured deterministic validation failures and pipeline phases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ValidationPhase(StrEnum):
    SCHEMA = "schema"
    REFERENCE = "reference"
    OWNERSHIP = "ownership"
    CAPABILITY = "capability"
    SPATIAL = "spatial"
    PATH = "path"
    RESOURCE = "resource"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    phase: ValidationPhase
    code: str
    field: str | None = None
    evidence: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "code": self.code,
            "field": self.field,
            "evidence": self.evidence or {},
        }


def validate_priority(priority: int) -> ValidationFailure | None:
    if not -100 <= priority <= 100:
        return ValidationFailure(
            ValidationPhase.SCHEMA,
            "PRIORITY_OUT_OF_RANGE",
            "priority",
            {"minimum": -100, "maximum": 100, "actual": priority},
        )
    return None


def validate_positive(value: int, field: str) -> ValidationFailure | None:
    if value <= 0:
        return ValidationFailure(
            ValidationPhase.SCHEMA,
            "VALUE_MUST_BE_POSITIVE",
            field,
            {"actual": value},
        )
    return None
