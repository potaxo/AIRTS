"""Shared military pixel chrome; layout and input remain with each panel."""

import pygame

from airts.presentation.palette import Color

BACKGROUND: Color = (19, 25, 23)
PANEL: Color = (28, 35, 32)
HEADER: Color = (39, 48, 43)
EDGE: Color = (82, 96, 83)
HIGHLIGHT: Color = (108, 123, 102)
SHADOW: Color = (12, 18, 16)
BUTTON: Color = (51, 63, 54)
BUTTON_ACTIVE: Color = (75, 73, 48)
TEXT: Color = (232, 234, 216)
MUTED: Color = (169, 186, 169)
ACCENT: Color = (229, 190, 105)
SUCCESS: Color = (116, 211, 159)
TRACK: Color = (41, 50, 44)


def frame(screen: pygame.Surface, rectangle: pygame.Rect, color: Color) -> None:
    """Hard pixel edges with a raised top and recessed lower edge."""
    pygame.draw.rect(screen, color, rectangle)
    pygame.draw.rect(screen, EDGE, rectangle, 1)
    if rectangle.width > 4 and rectangle.height > 4:
        pygame.draw.line(screen, HIGHLIGHT, rectangle.topleft, (rectangle.right - 2, rectangle.top))
        pygame.draw.line(
            screen,
            SHADOW,
            (rectangle.left + 1, rectangle.bottom - 2),
            (rectangle.right - 2, rectangle.bottom - 2),
        )


def panel(screen: pygame.Surface, rectangle: pygame.Rect) -> None:
    frame(screen, rectangle, PANEL)
    header = pygame.Rect(rectangle.x + 1, rectangle.y + 1, rectangle.width - 2, 37)
    header.height = min(header.height, max(0, rectangle.height - 2))
    pygame.draw.rect(screen, HEADER, header)
    pygame.draw.line(
        screen,
        ACCENT,
        (rectangle.left + 2, rectangle.top + 2),
        (rectangle.right - 3, rectangle.top + 2),
        2,
    )


def active_border(screen: pygame.Surface, rectangle: pygame.Rect, color: Color = ACCENT) -> None:
    pygame.draw.rect(screen, color, rectangle, 2)
    pygame.draw.rect(screen, color, (rectangle.x + 3, rectangle.y + 4, 3, rectangle.height - 8))
