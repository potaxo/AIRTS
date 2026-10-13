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


def compile_numeric[F: Callable[..., Any]](
    function: F, *, dependency_fingerprint: str | None = None
) -> F:
    """Keep normal arithmetic and one execution order; propagate compilation errors."""
    dispatcher = njit(cache=True, fastmath=False)(function)
    if dependency_fingerprint is not None:
        # Numba's file stamp does not track inlined kernels in other modules.
        # Extend the index stamp of this dispatcher, retaining the locator's
        # native/user/frozen cache placement and unchanged on-disk cache reuse.
        # This small adapter targets the declared Numba 0.63.x dependency;
        # cross-process dependency invalidation is covered by a regression test.
        cache_file = dispatcher._cache._cache_file
        cache_file._source_stamp = (cache_file._source_stamp, dependency_fingerprint)
    return cast(F, dispatcher)
