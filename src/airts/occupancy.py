"""Compatibility exports for :mod:`airts.world.occupancy`."""

import sys as _sys

from airts.world import occupancy as _implementation
from airts.world.occupancy import *  # noqa: F403

_sys.modules[__name__] = _implementation
