"""Compatibility exports for :mod:`airts.navigation.spatial_index`."""

import sys as _sys

from airts.navigation import spatial_index as _implementation
from airts.navigation.spatial_index import *  # noqa: F403

_sys.modules[__name__] = _implementation
