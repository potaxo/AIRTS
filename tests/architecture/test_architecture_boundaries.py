"""Executable dependency rules for AIRTS's modular monolith."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

import airts
from airts.simulation import Simulation
from airts.world.map import load_example_map

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "airts"
PACKAGE_RULES = {
    "contracts": {
        "airts.adapters",
        "airts.navigation",
        "airts.presentation",
        "airts.simulation",
        "airts.systems",
    },
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
    assert airts.load_example_map is load_example_map


def test_package_dependencies_point_toward_domain_code() -> None:
    violations: list[str] = []
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(SOURCE)
        if len(relative.parts) == 1:
            assert path.name in {"__init__.py", "__main__.py", "simulation.py"}, (
                f"unclassified root module: {relative}"
            )
        else:
            assert relative.parts[0] in {*PACKAGE_RULES, "adapters", "presentation"}, (
                f"unclassified source package: {relative}"
            )
            if relative.parts[0] in {"adapters", "presentation"}:
                continue
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


def test_runtime_module_imports_have_no_cycles() -> None:
    paths = {
        ".".join(("airts", *path.relative_to(SOURCE).with_suffix("").parts)).removesuffix(
            ".__init__"
        ): path
        for path in SOURCE.rglob("*.py")
    }
    graph: dict[str, set[str]] = {}
    for module, path in paths.items():
        graph[module] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for imported, _ in _runtime_imports(tree, _package_name(path)):
            # Imported attributes belong to their nearest source module. Implicit package
            # initialization and type-only references are not runtime dependency edges.
            while imported and imported not in paths:
                imported = imported.rpartition(".")[0]
            if imported in paths:
                graph[module].add(imported)
    visited: set[str] = set()
    active: list[str] = []

    def visit(module: str) -> None:
        assert module not in active, "runtime import cycle: " + " -> ".join((*active, module))
        if module in visited:
            return
        active.append(module)
        for imported in sorted(graph[module]):
            visit(imported)
        active.pop()
        visited.add(module)

    for module in sorted(graph):
        visit(module)


def test_system_modules_do_not_import_another_systems_private_symbols() -> None:
    violations: list[str] = []
    systems = SOURCE / "systems"
    for path in systems.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, _package_name(path))
            if not module.startswith("airts.systems."):
                continue
            for alias in node.names:
                if alias.name.startswith("_"):
                    violations.append(
                        f"{path.relative_to(SOURCE)}:{node.lineno} imports {alias.name}"
                    )
    assert not violations, "\n".join(violations)


def test_systems_call_automation_owner_without_simulation_forwarders() -> None:
    retired = {
        "_activate",
        "_assign",
        "_cancel",
        "_claim_wins",
        "_new_automation",
        "_release_automation",
        "_resume_suspended_assignment",
        "_state_for_assignment",
        "_transition",
        "_validate_claims",
        "_validate_entities",
    }
    violations: list[str] = []
    for path in (SOURCE / "systems").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in retired:
                violations.append(f"{path.relative_to(SOURCE)}:{node.lineno} uses {node.attr}")
    simulation_path = SOURCE / "simulation.py"
    tree = ast.parse(simulation_path.read_text(encoding="utf-8"), filename=str(simulation_path))
    simulation_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Simulation"
    )
    methods = {
        node.name
        for node in simulation_class.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for name in sorted(methods.intersection(retired)):
        violations.append(f"simulation.py retains retired forwarder {name}")
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
