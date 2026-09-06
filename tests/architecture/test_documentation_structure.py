"""Integrity checks for the AIRTS documentation architecture."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LOCAL_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def test_design_index_links_to_owning_documents() -> None:
    owners = set(DOCS.rglob("*.md")) - {DOCS / "design.md"}
    linked = {
        (DOCS / target.split("#", 1)[0]).resolve()
        for target in LOCAL_LINK.findall((DOCS / "design.md").read_text(encoding="utf-8"))
        if not target.startswith(("#", "http://", "https://"))
    }
    assert not {path.resolve() for path in owners} - linked


def test_local_markdown_links_resolve() -> None:
    markdown_files = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted(DOCS.rglob("*.md"))]
    anchors = {}
    for path in markdown_files:
        headings = re.findall(r"^#{1,6}\s+(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
        anchors[path.resolve()] = {
            re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
            for heading in headings
        }
    missing: list[str] = []
    for path in markdown_files:
        for target in LOCAL_LINK.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://")):
                continue
            target_path, _, fragment = unquote(target).partition("#")
            resolved = (path.parent / target_path).resolve() if target_path else path.resolve()
            if not resolved.exists() or (
                fragment and resolved in anchors and fragment not in anchors[resolved]
            ):
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not missing, "\n".join(missing)
