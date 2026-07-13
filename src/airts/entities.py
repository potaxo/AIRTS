"""Compatibility exports for :mod:`airts.world.entities`."""

import sys as _sys

from airts.world import entities as _implementation
from airts.world.entities import *  # noqa: F403

_sys.modules[__name__] = _implementation
