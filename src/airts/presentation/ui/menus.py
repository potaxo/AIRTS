"""Menus interface reads a scoped view and emits clickable actions."""

from __future__ import annotations

from typing import Protocol

import pygame

from airts.presentation.metrics import PresentationMetrics
from airts.presentation.ui.drawing import UiPainter


class MenusView(Protocol):
    _settings_buttons: list[tuple[pygame.Rect, str]]
    canvas_rect: pygame.Rect
    command_bar_rect: pygame.Rect
    presentation_metrics: PresentationMetrics
    ui: UiPainter
    window_size: tuple[int, int]


def draw_settings(view: MenusView, screen: pygame.Surface) -> None:
    x = view.command_bar_rect.x + 14
    y = view.command_bar_rect.y - 208
    view._settings_buttons.clear()
    pygame.draw.rect(screen, (34, 41, 51), pygame.Rect(x, y, 268, 202), border_radius=4)
    view.ui.small_text(
        screen,
        f"Resolution {view.window_size[0]} x {view.window_size[1]}",
        (x + 10, y + 8),
        (225, 232, 238),
    )
    lower = pygame.Rect(x + 8, y + 30, 120, 24)
    higher = pygame.Rect(x + 140, y + 30, 120, 24)
    view.ui.button(screen, lower, "Lower")
    view.ui.button(screen, higher, "Higher")
    view._settings_buttons.extend(((lower, "resolution_lower"), (higher, "resolution_higher")))
    metrics = view.presentation_metrics
    view.ui.small_text(
        screen,
        f"Frame p95 {metrics.frame_p95_ms:5.1f} ms  Present {metrics.present_p95_ms:5.1f} ms",
        (x + 10, y + 62),
        (166, 191, 215),
    )
    view.ui.small_text(
        screen,
        f"Render {metrics.render_p95_ms:5.1f} ms  Sim {metrics.simulation_p95_ms:5.1f} ms",
        (x + 10, y + 82),
        (166, 191, 215),
    )
    view.ui.small_text(
        screen,
        f"Real FPS {metrics.one_percent_low_fps:4.0f}  Submit FPS {metrics.submit_fps:4.0f}",
        (x + 10, y + 102),
        (111, 221, 151),
    )
    y += 122
    for action, label in (("save", "Save"), ("load", "Load"), ("new", "New game")):
        rectangle = pygame.Rect(x + 8, y, 252, 24)
        view.ui.button(screen, rectangle, label)
        view._settings_buttons.append((rectangle, action))
        y += 26


def draw_help(view: MenusView, screen: pygame.Surface) -> None:
    rectangle = pygame.Rect(
        view.canvas_rect.x + 40,
        80,
        max(320, view.canvas_rect.width - 80),
        260,
    )
    pygame.draw.rect(screen, (28, 34, 42), rectangle, border_radius=4)
    pygame.draw.rect(screen, (104, 126, 149), rectangle, 1, border_radius=4)
    view.ui.text(screen, "Controls", (rectangle.x + 18, rectangle.y + 16), (245, 245, 245))
    lines = (
        "1 Select   2 Line   3 Rectangle   4 Freehand",
        "Right-click Move/Attack or finish a line   Middle-drag Pan",
        "Single right-click preserves formation; double right-click gathers.",
        "WASD Camera   T Patrol   Y Defend   X Stop   H Hold",
        "R Repair   Space Pause   Wheel Zoom",
        "Shift multi-select   N Name   E Edit   U Retarget   [ ] Priority",
        "Choose a selected type in the right rail to show its actions.",
    )
    for index, line in enumerate(lines):
        view.ui.small_text(
            screen,
            line,
            (rectangle.x + 18, rectangle.y + 55 + index * 28),
            (190, 205, 220),
        )
