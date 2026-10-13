"""Invalidate compiled contact kernels together when any contact source changes."""

from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from airts.world._numeric import compile_numeric

_sources = sorted(Path(__file__).parent.glob("*.py"))
_fingerprint = sha256()
for _source in _sources:
    _fingerprint.update(_source.name.encode("utf-8"))
    _fingerprint.update(_source.read_bytes())
DEPENDENCY_FINGERPRINT = _fingerprint.hexdigest()
# Frozen builds use Numba's executable stamp; source checkouts additionally
# fingerprint all contact modules, including helpers inlined into the solver.


def compile_collision[F: Callable[..., Any]](function: F) -> F:
    return compile_numeric(function, dependency_fingerprint=DEPENDENCY_FINGERPRINT)
