"""Typed entry points for serial CPU kernels shared by domain owners."""

from collections.abc import Callable
from typing import Any, cast

import numpy as np
from numba import njit  # type: ignore[import-untyped]
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]
WordArray = NDArray[np.uint64]


def compile_numeric[F: Callable[..., Any]](function: F) -> F:
    """Keep normal arithmetic and one execution order; propagate compilation errors."""
    return cast(F, njit(cache=False, fastmath=False)(function))
