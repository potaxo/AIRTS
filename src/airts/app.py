"""Compatibility exports for :mod:`airts.presentation.app`."""

import sys as _sys

from airts.presentation import app as _implementation
from airts.presentation.app import *  # noqa: F403

_sys.modules[__name__] = _implementation
