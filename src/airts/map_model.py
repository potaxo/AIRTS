"""Compatibility exports for :mod:`airts.world.map_model`."""

import sys as _sys

from airts.world import map_model as _implementation
from airts.world.map_model import *  # noqa: F403

_sys.modules[__name__] = _implementation
