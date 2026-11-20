"""Typed entry points for CPU kernels shared by domain owners."""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from functools import wraps
from operator import attrgetter
from typing import Any, cast

import numpy as np
from numba import njit  # type: ignore[import-untyped]
from numpy.typing import NDArray

from airts.contracts.spatial import Point

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]
WordArray = NDArray[np.uint64]


def point_coordinates(points: Sequence[Point], out: FloatArray | None = None) -> FloatArray:
    """Pack point columns without allocating a Python coordinate tuple per body."""
    if out is None:
        out = np.empty((len(points), 2), dtype=np.float64)
    out[:, 0] = np.fromiter(map(attrgetter("x"), points), np.float64, len(points))
    out[:, 1] = np.fromiter(map(attrgetter("y"), points), np.float64, len(points))
    return out


# Three sleeping workers plus the calling thread. The executor starts lazily;
# unlike a second native parallel runtime it does not spin between query batches.
_query_workers = ThreadPoolExecutor(max_workers=3, thread_name_prefix="airts-query")


def compile_numeric[F: Callable[..., Any]](
    function: F, *, dependency_fingerprint: str | None = None
) -> F:
    """Keep normal arithmetic and one execution order; propagate compilation errors."""
    dispatcher = njit(cache=True, fastmath=False, nogil=True)(function)
    if dependency_fingerprint is not None:
        # Numba's file stamp does not track inlined kernels in other modules.
        # Extend the index stamp of this dispatcher, retaining the locator's
        # native/user/frozen cache placement and unchanged on-disk cache reuse.
        # This small adapter targets the declared Numba 0.63.x dependency;
        # cross-process dependency invalidation is covered by a regression test.
        cache_file = dispatcher._cache._cache_file
        cache_file._source_stamp = (cache_file._source_stamp, dependency_fingerprint)
    return cast(F, dispatcher)


def compile_parallel_numeric[F: Callable[..., Any]](function: F) -> F:
    """Partition independent query rows over four GIL-free native calls.

    The first argument is an immutable query array; each call returns its own
    row array. Join every reader before returning, including when a call fails.
    """
    dispatcher = njit(cache=True, fastmath=False, nogil=True)(function)

    @wraps(function)
    def bounded(queries: FloatArray, *args: Any, **kwargs: Any) -> Any:
        if len(queries) < 1_024:
            return dispatcher(queries, *args, **kwargs)
        edges = [len(queries) * part // 4 for part in range(5)]
        futures = [
            _query_workers.submit(dispatcher, queries[edges[i] : edges[i + 1]], *args, **kwargs)
            for i in range(3)
        ]
        try:
            tail = dispatcher(queries[edges[3] :], *args, **kwargs)
            return np.concatenate([*(future.result() for future in futures), tail])
        finally:
            wait(futures)

    return cast(F, bounded)
