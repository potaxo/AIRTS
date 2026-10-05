"""Keep workload behavior in daily validation and opt in to timing budgets."""

from __future__ import annotations

import pytest


@pytest.fixture(
    params=[False, pytest.param(True, marks=pytest.mark.performance)],
    ids=["behavior", "timing"],
)
def check_timing(request: pytest.FixtureRequest) -> bool:
    """Reuse the complete workload; only the timing variant enforces elapsed-time gates."""

    return bool(request.param)
