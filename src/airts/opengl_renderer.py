"""Compatibility exports for :mod:`airts.presentation.opengl_renderer`."""

import sys as _sys

from airts.presentation import opengl_renderer as _implementation
from airts.presentation.opengl_renderer import *  # noqa: F403

_sys.modules[__name__] = _implementation
