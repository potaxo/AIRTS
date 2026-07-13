"""Compatibility exports for :mod:`airts.world.projectiles`."""

import sys as _sys

from airts.world import projectiles as _implementation
from airts.world.projectiles import *  # noqa: F403

_sys.modules[__name__] = _implementation
