"""Compatibility exports for :mod:`airts.navigation.pathfinding`."""

import sys as _sys

from airts.navigation import pathfinding as _implementation
from airts.navigation.pathfinding import *  # noqa: F403

_sys.modules[__name__] = _implementation
