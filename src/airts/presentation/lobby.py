"""Opening cover and four-slot setup; configuration cannot change during a match."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from airts.presentation.ui import theme
from airts.world.skirmish import DEFAULT_SETUP

if TYPE_CHECKING:
    from airts.presentation.app import AirtsApp


class Lobby:
    def __init__(self) -> None:
        self.page = "cover"
        self.setup = list(DEFAULT_SETUP)
        self.buttons: list[tuple[pygame.Rect, str, int]] = []

    def draw(self, app: AirtsApp, screen: pygame.Surface) -> None:
        screen.fill(theme.BACKGROUND)
        center_x, center_y = screen.get_rect().center
        x, y = center_x - 290, max(28, center_y - 220)
        theme.panel(screen, pygame.Rect(x - 24, y - 24, 628, 456))
        app.ui.text(screen, "AIRTS", (x, y), theme.ACCENT)
        app.ui.text(screen, "Four Corners River", (x, y + 40), theme.TEXT)
        self.buttons.clear()
        if self.page == "cover":
            app.ui.small_text(
                screen, "Develop your base. Expand. Fight as a team.", (x, y + 94), theme.MUTED
            )
            rectangle = pygame.Rect(x, y + 152, 580, 48)
            app.ui.button(screen, rectangle, "Start game")
            self.buttons.append((rectangle, "configure", 0))
            rectangle = pygame.Rect(x, y + 212, 580, 40)
            app.ui.button(screen, rectangle, "Load saved game")
            self.buttons.append((rectangle, "saves", 0))
            return
        app.ui.small_text(
            screen, "320 x 320 | Fixed symmetric map | No fog of war", (x, y + 78), theme.MUTED
        )
        app.ui.small_text(
            screen,
            "Click controller / team to cycle. One human player required.",
            (x, y + 102),
            theme.MUTED,
        )
        for index, ((role, team), location) in enumerate(
            zip(
                self.setup,
                ("A: bottom left", "B: top right", "C: top left", "D: bottom right"),
                strict=True,
            )
        ):
            row_y = y + 140 + index * 48
            app.ui.small_text(screen, location, (x, row_y + 10), theme.TEXT)
            controller = pygame.Rect(x + 180, row_y, 180, 34)
            alliance = pygame.Rect(x + 380, row_y, 200, 34)
            app.ui.button(screen, controller, role.title())
            app.ui.button(screen, alliance, f"Team {team}")
            self.buttons.extend(((controller, "controller", index), (alliance, "team", index)))
        for rectangle, action, label in (
            (pygame.Rect(x, y + 340, 180, 38), "back", "Back"),
            (pygame.Rect(x + 200, y + 340, 380, 38), "start", "Begin match"),
        ):
            app.ui.button(screen, rectangle, label)
            self.buttons.append((rectangle, action, 0))
        app.ui.small_text(screen, app.notice, (x, y + 392), theme.ACCENT)

    def click(self, app: AirtsApp, position: tuple[int, int]) -> None:
        for rectangle, action, index in self.buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "configure":
                self.page = "setup"
                app.notice = "Configure players and teams before starting."
            elif action == "back":
                self.page = "cover"
            elif action == "saves":
                app._open_save_manager()
            elif action == "start":
                app.start_match(tuple(self.setup))
            elif action == "team":
                role, team = self.setup[index]
                self.setup[index] = role, team % 4 + 1
            elif action == "controller":
                role, team = self.setup[index]
                roles = ("closed", "ai", "player")
                role = roles[(roles.index(role) + 1) % len(roles)]
                if role == "player":
                    self.setup = [
                        ("ai" if old_role == "player" else old_role, old_team)
                        for old_role, old_team in self.setup
                    ]
                self.setup[index] = role, team
            return
