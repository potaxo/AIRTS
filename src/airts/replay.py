"""Compatibility exports for :mod:`airts.adapters.replay`."""

import sys as _sys

from airts.adapters import replay as _implementation
from airts.adapters.replay import *  # noqa: F403

_sys.modules[__name__] = _implementation
