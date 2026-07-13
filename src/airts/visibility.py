"""Compatibility exports for :mod:`airts.world.visibility`."""

import sys as _sys

from airts.world import visibility as _implementation
from airts.world.visibility import *  # noqa: F403

_sys.modules[__name__] = _implementation
