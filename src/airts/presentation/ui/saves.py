"""Modal save browser; drawing emits actions for the application to dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

import pygame

from airts.presentation.ui import theme
from airts.presentation.ui.drawing import UiPainter


@dataclass(frozen=True, slots=True)
class SaveEntry:
    path: Path
    modified: float
    size: int


@dataclass(slots=True)
class SavePanelState:
    open: bool = False
    entries: list[SaveEntry] = field(default_factory=list)
    selected: Path | None = None
    scroll: int = 0
    visible_rows: int = 1
    confirmation: str | None = None
    error: str = ""
    buttons: list[tuple[pygame.Rect, str]] = field(default_factory=list)

    def refresh(self, directory: Path, quick_save: Path) -> None:
        self.error = ""
        entries: list[SaveEntry] = []
        try:
            paths = list(directory.glob("*.json")) if directory.exists() else []
            if quick_save.exists():
                paths.append(quick_save)
            for path in paths:
                if path.is_file() and not path.is_symlink():
                    stat = path.stat()
                    entries.append(SaveEntry(path, stat.st_mtime, stat.st_size))
        except OSError as error:
            self.error = f"Cannot list saves: {error}"
        self.entries = sorted(entries, key=lambda entry: (-entry.modified, entry.path.name))
        if self.selected not in {entry.path for entry in entries}:
            self.selected = self.entries[0].path if self.entries else None
        self.scroll = max(0, min(self.scroll, len(self.entries) - self.visible_rows))

    def scroll_by(self, amount: int) -> None:
        self.scroll = max(0, min(self.scroll + amount, len(self.entries) - self.visible_rows))

    def cache_key(self) -> tuple[object, ...]:
        return (
            self.open,
            tuple(self.entries),
            self.selected,
            self.scroll,
            self.confirmation,
            self.error,
        )


class SavesView(Protocol):
    save_panel: SavePanelState
    notice: str
    ui: UiPainter
    window_size: tuple[int, int]

    @property
    def save_busy(self) -> bool: ...


def draw(view: SavesView, screen: pygame.Surface) -> None:
    state = view.save_panel
    width = min(700, view.window_size[0] - 40)
    height = min(550, view.window_size[1] - 40)
    panel = pygame.Rect(0, 0, width, height)
    panel.center = (view.window_size[0] // 2, view.window_size[1] // 2)
    shade = pygame.Surface(view.window_size, pygame.SRCALPHA)
    shade.fill((0, 0, 0, 165))
    screen.blit(shade, (0, 0))
    theme.panel(screen, panel)
    state.buttons.clear()
    view.ui.text(screen, "Save manager", (panel.x + 20, panel.y + 18), theme.TEXT)
    view.ui.small_text(
        screen,
        "Select a save to load, overwrite or delete. New saves use the current time.",
        (panel.x + 20, panel.y + 50),
        theme.MUTED,
    )

    def button(rectangle: pygame.Rect, action: str, label: str, enabled: bool = True) -> None:
        view.ui.button(screen, rectangle, label, enabled=enabled)
        if enabled:
            state.buttons.append((rectangle, action))
        else:
            dim = pygame.Surface(rectangle.size, pygame.SRCALPHA)
            dim.fill((20, 26, 34, 160))
            screen.blit(dim, rectangle)

    button(pygame.Rect(panel.right - 94, panel.y + 12, 76, 28), "close", "Close")
    row_top = panel.y + 84
    state.visible_rows = max(1, (height - 240) // 48)
    state.scroll_by(0)
    for index, entry in enumerate(state.entries[state.scroll : state.scroll + state.visible_rows]):
        row = pygame.Rect(panel.x + 20, row_top + index * 48, width - 40, 44)
        theme.frame(
            screen, row, theme.BUTTON_ACTIVE if entry.path == state.selected else theme.TRACK
        )
        if entry.path == state.selected:
            theme.active_border(screen, row)
        name = "Quicksave" if entry.path.name == "airts-quicksave.json" else entry.path.stem
        view.ui.small_text(
            screen,
            view.ui.fit_small_text(name, width - 60),
            (row.x + 10, row.y + 5),
            theme.TEXT,
        )
        date = datetime.fromtimestamp(entry.modified).strftime("%Y-%m-%d %H:%M:%S")
        view.ui.small_text(
            screen,
            f"{date}   {entry.size / 1024:,.0f} KB",
            (row.x + 10, row.y + 24),
            theme.MUTED,
        )
        state.buttons.append((row, f"select:{state.scroll + index}"))
    if not state.entries:
        view.ui.small_text(
            screen,
            "No saves yet. Choose New save to save this game.",
            (panel.x + 30, row_top + 24),
            theme.MUTED,
        )
    bottom = panel.bottom - 142
    button(pygame.Rect(panel.x + 20, bottom, 70, 26), "previous", "Up", state.scroll > 0)
    button(
        pygame.Rect(panel.x + 98, bottom, 70, 26),
        "next",
        "Down",
        state.scroll + state.visible_rows < len(state.entries),
    )
    view.ui.small_text(
        screen,
        f"{len(state.entries)} saves | Game paused while this page is open",
        (panel.x + 182, bottom + 6),
        theme.MUTED,
    )
    actions = (
        ("new_save", "New save"),
        ("overwrite", "Overwrite"),
        ("load", "Load"),
        ("delete", "Delete"),
        ("refresh", "Refresh"),
    )
    button_width = (width - 56) // len(actions)
    for index, (action, label) in enumerate(actions):
        button(
            pygame.Rect(panel.x + 20 + index * (button_width + 4), bottom + 36, button_width, 32),
            action,
            label,
            not view.save_busy
            and (action in {"new_save", "refresh"} or state.selected is not None),
        )
    status = state.error or view.notice
    if state.confirmation:
        status = f"{state.confirmation.title()} {state.selected.name if state.selected else ''}?"
        button(
            pygame.Rect(panel.x + 20, panel.bottom - 38, 110, 26),
            "confirm",
            "Confirm",
            not view.save_busy,
        )
        button(pygame.Rect(panel.x + 140, panel.bottom - 38, 110, 26), "cancel", "Cancel")
    view.ui.small_text(
        screen,
        view.ui.fit_small_text(status, width - 40),
        (panel.x + 20, bottom + 82),
        (240, 202, 133),
    )
