"""Executable dependency rules for AIRTS's modular monolith."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

import airts
from airts.simulation import Simulation

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "airts"
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


def test_simulation_remains_the_public_package_facade() -> None:
    assert airts.Simulation is Simulation


def test_package_dependencies_point_toward_domain_code() -> None:
    violations: list[str] = []
    paths = list(SOURCE.glob("*.py"))
    paths.extend(path for package in PACKAGE_RULES for path in (SOURCE / package).rglob("*.py"))
    for path in paths:
        if path.name == "__main__.py":
            continue
        package = path.relative_to(SOURCE).parts[0]
        forbidden = PACKAGE_RULES.get(package, set()) | {
            "pygame",
            "moderngl",
            "airts.presentation",
            "airts.adapters",
        }
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module_name, line in _runtime_imports(tree, _package_name(path)):
            if _matches_any(module_name, forbidden):
                violations.append(f"{path.relative_to(SOURCE)}:{line} imports {module_name}")
    assert not violations, "\n".join(violations)


def _matches_any(module_name: str, forbidden_imports: set[str]) -> bool:
    return any(
        module_name == forbidden or module_name.startswith(f"{forbidden}.")
        for forbidden in forbidden_imports
    )


def _package_name(path: Path) -> str:
    return ".".join(("airts", *path.relative_to(SOURCE).parent.parts))


def _runtime_imports(node: ast.AST, package: str) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
        for statement in node.orelse:
            imports.extend(_runtime_imports(statement, package))
        return imports
    if isinstance(node, ast.Import):
        imports.extend((alias.name, node.lineno) for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level:
            module = resolve_name("." * node.level + module, package)
        imports.append((module, node.lineno))
        imports.extend((f"{module}.{alias.name}", node.lineno) for alias in node.names)
    for child in ast.iter_child_nodes(node):
        imports.extend(_runtime_imports(child, package))
    return imports


def _is_type_checking_guard(expression: ast.expr) -> bool:
    return (isinstance(expression, ast.Name) and expression.id == "TYPE_CHECKING") or (
        isinstance(expression, ast.Attribute)
        and isinstance(expression.value, ast.Name)
        and expression.value.id == "typing"
        and expression.attr == "TYPE_CHECKING"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def render():\n    import pygame", {"pygame"}),
        ("from .. import simulation", {"airts", "airts.simulation"}),
        ("from ..presentation import app", {"airts.presentation", "airts.presentation.app"}),
        ("if TYPE_CHECKING:\n    from airts.simulation import Simulation", set()),
        ("if typing.TYPE_CHECKING:\n    import pygame\nelse:\n    import moderngl", {"moderngl"}),
        ("class View:\n    import pygame", {"pygame"}),
    ],
)
def test_dependency_scan_handles_nested_and_relative_imports(
    source: str, expected: set[str]
) -> None:
    assert {name for name, _ in _runtime_imports(ast.parse(source), "airts.systems")} == expected
