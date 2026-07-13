"""Compatibility exports for :mod:`airts.navigation.movement`."""

import sys as _sys

from airts.navigation import movement as _implementation
from airts.navigation.movement import *  # noqa: F403

_sys.modules[__name__] = _implementation
