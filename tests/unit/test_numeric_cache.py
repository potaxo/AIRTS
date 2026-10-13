"""Source dependencies invalidate Numba cache entries across native Python processes."""

import os
import subprocess
import sys
from pathlib import Path


def test_imported_kernel_edits_invalidate_cached_callers_without_timestamp_changes(
    tmp_path: Path,
) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text(
        "from hashlib import sha256\n"
        "from pathlib import Path\n"
        "from airts.world._numeric import compile_numeric\n"
        "FINGERPRINT = sha256(Path(__file__).read_bytes()).hexdigest()\n"
        "def kernel(value):\n"
        "    return value + 2\n"
        "kernel = compile_numeric(kernel, dependency_fingerprint=FINGERPRINT)\n",
        encoding="utf-8",
    )
    caller = tmp_path / "caller.py"
    caller.write_text(
        "from airts.world._numeric import compile_numeric\n"
        "from helper import kernel, FINGERPRINT\n"
        "def calculate(value):\n"
        "    return kernel(value)\n"
        "calculate = compile_numeric(calculate, dependency_fingerprint=FINGERPRINT)\n"
        "print(calculate(10))\n",
        encoding="utf-8",
    )

    def run() -> str:
        result = subprocess.run(
            [sys.executable, "-B", str(caller)],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    assert run() == "12"
    assert run() == "12"  # Unchanged processes may reuse the same cache.
    stamp = helper.stat()
    helper.write_text(
        helper.read_text(encoding="utf-8").replace("value + 2", "value + 3"), encoding="utf-8"
    )
    os.utime(helper, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert helper.stat().st_size == stamp.st_size
    assert run() == "13"  # Byte content wins over identical mtime and file size.
