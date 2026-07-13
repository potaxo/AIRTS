"""Compatibility exports for :mod:`airts.adapters.persistence`."""

import sys as _sys

from airts.adapters import persistence as _implementation
from airts.adapters.persistence import *  # noqa: F403

_sys.modules[__name__] = _implementation
