"""Integrity checks for the AIRTS documentation architecture."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LOCAL_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def test_design_index_links_to_owning_documents() -> None:
    owners = {DOCS / "roadmap.md", DOCS / "decisions" / "README.md"}
    owners.update((DOCS / "architecture").glob("*.md"))
    owners.update((DOCS / "milestones").glob("*.md"))
    linked = {
        (DOCS / target.split("#", 1)[0]).resolve()
        for target in LOCAL_LINK.findall((DOCS / "design.md").read_text(encoding="utf-8"))
        if not target.startswith(("#", "http://", "https://"))
    }
    assert not {path.resolve() for path in owners} - linked


def test_local_markdown_links_resolve() -> None:
    markdown_files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted(DOCS.rglob("*.md"))]
    missing: list[str] = []
    for path in markdown_files:
        for target in LOCAL_LINK.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("#", "http://", "https://")):
                continue
            target_path = target.split("#", 1)[0]
            if not target_path:
                continue
            resolved = (path.parent / target_path).resolve()
            if not resolved.exists():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not missing, "\n".join(missing)
