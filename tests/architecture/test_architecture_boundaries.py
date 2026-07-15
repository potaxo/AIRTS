"""Executable dependency rules for AIRTS's modular monolith."""

from __future__ import annotations

import ast
from pathlib import Path

import airts
from airts.simulation import Simulation

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "airts"
PERFORMANCE_TESTS = ROOT / "tests" / "performance"
PACKAGE_RULES = {
    "world": {
        "airts.adapters",
        "airts.navigation",
        "airts.presentation",
        "airts.simulation",
        "airts.systems",
    },
    "navigation": {
        "airts.adapters",
        "airts.presentation",
        "airts.simulation",
        "airts.systems",
    },
    "systems": {
        "airts.adapters",
        "airts.presentation",
        "airts.simulation",
    },
}
REMOVED_TOP_LEVEL_MODULES = frozenset(
    {
        "app",
        "entities",
        "map_model",
        "movement",
        "occupancy",
        "opengl_renderer",
        "pathfinding",
        "persistence",
        "projectiles",
        "replay",
        "spatial_index",
        "visibility",
    }
)


def test_simulation_remains_the_public_package_facade() -> None:
    assert airts.Simulation is Simulation


def test_package_dependencies_point_toward_domain_code() -> None:
    violations: list[str] = []
    for package, forbidden_imports in PACKAGE_RULES.items():
        for path in sorted((SOURCE / package).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module_name, line in _runtime_imports(tree.body):
                if _matches_any(module_name, forbidden_imports):
                    violations.append(f"{path.relative_to(SOURCE)}:{line} imports {module_name}")
    assert not violations, "\n".join(violations)


def test_internal_source_uses_canonical_package_imports() -> None:
    violations: list[str] = []
    removed_imports = {f"airts.{module}" for module in REMOVED_TOP_LEVEL_MODULES}
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module_name, line in _runtime_imports(tree.body):
            if _matches_any(module_name, removed_imports):
                violations.append(f"{path.relative_to(SOURCE)}:{line} imports {module_name}")
    assert not violations, "\n".join(violations)


def test_removed_top_level_modules_do_not_return() -> None:
    assert not [
        module for module in sorted(REMOVED_TOP_LEVEL_MODULES) if (SOURCE / f"{module}.py").exists()
    ]


def test_source_module_names_are_unambiguous() -> None:
    paths_by_name: dict[str, list[Path]] = {}
    for path in SOURCE.rglob("*.py"):
        if path.name != "__init__.py":
            paths_by_name.setdefault(path.name, []).append(path.relative_to(SOURCE))
    duplicates = {name: paths for name, paths in sorted(paths_by_name.items()) if len(paths) > 1}
    assert not duplicates


def test_repository_root_has_no_python_scripts() -> None:
    assert not sorted(ROOT.glob("*.py"))


def test_every_fps_performance_contract_uses_the_real_fps_rule() -> None:
    violations: list[str] = []
    for path in sorted(PERFORMANCE_TESTS.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if "TARGET_FPS" not in source:
            continue
        if "assert_real_fps(" not in source:
            violations.append(f"{path.name} does not assert the shared Real FPS metric")
        for forbidden in ("achieved_fps", "MEASURED_FRAMES /", "/ elapsed"):
            if forbidden in source:
                violations.append(f"{path.name} uses forbidden average-FPS logic: {forbidden}")
    assert not violations, "\n".join(violations)


def _matches_any(module_name: str, forbidden_imports: set[str]) -> bool:
    return any(
        module_name == forbidden or module_name.startswith(f"{forbidden}.")
        for forbidden in forbidden_imports
    )


def _runtime_imports(statements: list[ast.stmt]) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    for statement in statements:
        if isinstance(statement, ast.If) and _is_type_checking_guard(statement.test):
            imports.extend(_runtime_imports(statement.orelse))
            continue
        if isinstance(statement, ast.Import):
            imports.extend((alias.name, statement.lineno) for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.module is not None:
            imports.append((statement.module, statement.lineno))
        elif isinstance(statement, (ast.If, ast.Try, ast.With)):
            nested = list(statement.body)
            nested.extend(getattr(statement, "orelse", ()))
            nested.extend(getattr(statement, "finalbody", ()))
            for handler in getattr(statement, "handlers", ()):
                nested.extend(handler.body)
            imports.extend(_runtime_imports(nested))
    return imports


def _is_type_checking_guard(expression: ast.expr) -> bool:
    return isinstance(expression, ast.Name) and expression.id == "TYPE_CHECKING"
