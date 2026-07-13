"""Integrity checks for the AIRTS documentation architecture."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
NUMBERED_SECTION = re.compile(r"^# (\d+)\. ", re.MULTILINE)
LOCAL_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def test_design_sections_exist_exactly_once() -> None:
    design_files = [DOCS / "design.md", DOCS / "roadmap.md"]
    design_files.extend(sorted((DOCS / "architecture").glob("*.md")))
    design_files.extend(sorted((DOCS / "milestones").glob("*.md")))
    counts = Counter(
        int(section)
        for path in design_files
        for section in NUMBERED_SECTION.findall(path.read_text(encoding="utf-8"))
    )
    assert counts == Counter(range(1, 42))


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
