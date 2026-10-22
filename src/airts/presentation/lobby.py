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
        x, y = center_x - 290, max(28, center_y - 242)
        theme.panel(screen, pygame.Rect(x - 24, y - 24, 628, 500))
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
            self._draw_debug_option(app, screen, x, y + 280)
            app.ui.small_text(
                screen, "Save/load works with either option.", (x, y + 320), theme.MUTED
            )
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
        self._draw_debug_option(app, screen, x, y + 330)
        for rectangle, action, label in (
            (pygame.Rect(x, y + 374, 180, 38), "back", "Back"),
            (pygame.Rect(x + 200, y + 374, 380, 38), "start", "Begin match"),
        ):
            app.ui.button(screen, rectangle, label)
            self.buttons.append((rectangle, action, 0))
        app.ui.small_text(screen, app.notice, (x, y + 436), theme.ACCENT)

    def _draw_debug_option(self, app: AirtsApp, screen: pygame.Surface, x: int, y: int) -> None:
        rectangle = pygame.Rect(x, y, 580, 30)
        box = pygame.Rect(x, y + 3, 24, 24)
        theme.frame(screen, box, theme.BUTTON)
        if app.debug_mode:
            pygame.draw.lines(
                screen,
                theme.SUCCESS,
                False,
                [(x + 5, y + 14), (x + 10, y + 20), (x + 20, y + 7)],
                3,
            )
        app.ui.small_text(screen, "Debug mode (record full history)", (x + 36, y + 7), theme.TEXT)
        self.buttons.append((rectangle, "debug", 0))

    def click(self, app: AirtsApp, position: tuple[int, int]) -> None:
        for rectangle, action, index in self.buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "debug":
                app.debug_mode = not app.debug_mode
            elif action == "configure":
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
